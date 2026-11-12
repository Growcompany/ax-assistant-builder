"""평가 실행기: 골든셋으로 검색(과 답변)을 채점하고 보고서를 남긴다.

사용:
    python -m axbuilder.eval.run --workspace bank --retriever {bm25,dense,hybrid} --k 5 [--with-llm]
        [--reranker {none,kure-nano,bge-v2-m3}] [--candidates 30] [--max-chunk-chars N]
결과는 reports/{workspace}/{YYYYmmdd-HHMM}-{retriever}.json에 저장하고 마크다운 표로 출력한다.
--reranker를 주면 검색기의 앞 --candidates개를 리랭커로 다시 줄 세운 뒤 k개를 쓰고, 파일 이름은
{retriever}+{reranker}-c{candidates}가 된다.
--max-chunk-chars를 주면 N자보다 긴 본문 조항을 나눠 색인하고(chunk_document의 max_chars), 파일
이름 끝에 -m{N}을, config에 chunking을 남긴다. 서버 설정 AXB_MAX_CHUNK_CHARS는 보지 않는다.
원문은 미리 받아 두어야 한다(python scripts/download_corpus.py bank). --with-llm은 AXB_LLM_MODE를
따른다(기본 local = Ollama). cloud면 문항마다 유료 Claude API를 부르므로 --allow-cloud를 함께
줘야 한다. 색인은 메모리에만 만든다.
"""

import argparse
import hashlib
import itertools
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel, Field

from axbuilder.corpus import CorpusSource, load_sources, source_file
from axbuilder.eval.dataset import TAGS, Evidence, GoldItem, QType, load_golden, validate_golden
from axbuilder.eval.metrics import (
    citation_precision,
    grounded_refusal_correct,
    matches,
    recall_at_k,
    recall_at_k_strict,
    reciprocal_rank,
    reciprocal_rank_strict,
    refusal_correct,
)
from axbuilder.generation.answer import answer
from axbuilder.generation.llm import LLMClient, LLMUnavailableError, get_llm
from axbuilder.generation.refusal import RefusalKind
from axbuilder.ingest.chunking import MIN_MAX_CHARS, chunk_document
from axbuilder.ingest.loaders import load_document
from axbuilder.models import Answer, Chunk, ScoredChunk
from axbuilder.retrieval.bm25 import BM25Index
from axbuilder.retrieval.hybrid import RRF_K, HybridRetriever, Retriever
from axbuilder.retrieval.rerank import (
    RERANKER_MODELS,
    CrossEncoderReranker,
    Reranker,
    RerankerUnavailableError,
    RerankRetriever,
)
from axbuilder.settings import Settings
from axbuilder.workspace import Workspace, WorkspaceNotFoundError, WorkspaceStore

REPO_ROOT = Path(__file__).resolve().parents[3]
RETRIEVERS = ("bm25", "dense", "hybrid")
RERANKERS = ("none", *RERANKER_MODELS)
DEFAULT_RERANK_CANDIDATES = 30  # 리랭커에 넘기는 후보 수
# 골든셋에 내부 지침 근거가 있으므로 기본 역할은 public·internal을 모두 보는 역할이다
DEFAULT_ROLE = "준법감시"
# workspace.yaml이 아직 없을 때(Task 10 API 전) 쓰는 가상 은행 설정 (scripts/demo_answer.py와 같음)
BANK_WORKSPACE = Workspace(
    id="bank",
    name="AXB은행(가상)",
    industry="finance",
    roles={"상담원": ["public"], "준법감시": ["public", "internal"]},
)
QTYPE_ORDER: tuple[str, ...] = get_args(QType)


class EvalReport(BaseModel):
    config: dict  # 실행 조건: 검색기, k, 역할, 모델, 커밋 등 (같은 조건으로 다시 돌릴 수 있게)
    n: int  # 채점한 문항 수 (검색 지표만이면 out_of_scope 제외)
    recall_at_k: float  # 근거가 있는 문항의 평균
    mrr: float
    # 답 문구(anchor) 기준 Recall·MRR(Task 24). 골든셋에 anchor가 하나도 없으면 None
    recall_at_k_strict: float | None = None
    mrr_strict: float | None = None
    citation_precision: float | None  # 인용이 하나라도 있는 문항의 평균. LLM 없이는 None
    refusal_accuracy: float | None  # LLM 없이는 None
    # 정해진 문구(EXACT)만 거절로 센 값 = v0.2.0까지의 규칙. 예전 보고서에는 없어 기본값을 둔다
    refusal_accuracy_strict: float | None = None
    # 근거가 상위 k에 있으면 답변, 없으면 거절이 정답인 지표와 그 내역 (grounded_refusal_correct)
    grounded_refusal_accuracy: float | None = None
    over_refusals: int | None = None  # 근거가 있는데 거절
    grounded_refusals: int | None = None  # 근거가 없어서 거절 (범위 밖 포함)
    ungrounded_answers: int | None = None  # 근거가 없는데 답함 (환각 위험 신호)
    by_qtype: dict[str, dict[str, float]]  # qtype → {"n", 지표...} (잴 수 없는 지표는 빠짐)
    # 태그 → by_qtype과 같은 요약(Task 24). 태그가 있는 문항만, 예전 보고서에는 없어 기본값을 둔다
    by_tag: dict[str, dict[str, float]] = Field(default_factory=dict)
    items: list[dict]  # 문항별 기록: 검색 결과·적중 여부·답변·지연 (실패 사례 분석용)
    # 계획의 Interfaces에 없는 필드: 색인·평가 시간과 질문당 지연(초·밀리초). 결과라 config와 나눈다
    timing: dict[str, float] = Field(default_factory=dict)


def run_eval(
    items: list[GoldItem],
    retriever: Retriever,
    k: int,
    llm: LLMClient | None,
    workspace: Workspace,
    role: str,
) -> EvalReport:
    """llm이 None이면 검색 지표만(out_of_scope 문항 제외), 있으면 answer()로 모든 문항에 답한다."""
    if role not in workspace.roles:
        raise ValueError(f"unknown role {role!r} for workspace {workspace.id!r}")
    access_levels = list(workspace.roles[role])
    # 범위 밖 문항은 근거가 없어 검색으로 채점할 수 없다. 답변 평가에서는 거절했는지를 본다
    scored = items if llm is not None else [i for i in items if i.qtype != "out_of_scope"]
    if not any(item.evidence for item in scored):
        raise ValueError("no golden items with evidence to score")

    # 답 문구가 있는 근거가 하나라도 있을 때만 엄격 지표를 단다(없으면 보고서가 이전과 같다)
    strict = any(ev.anchor for item in scored for ev in item.evidence)
    records = []
    started = time.perf_counter()
    for item in scored:
        t0 = time.perf_counter()
        result: Answer | None = None
        if llm is None:
            # answer()와 같은 규칙: 볼 수 있는 등급이 없으면 검색하지 않는다
            retrieved = (
                retriever.search(item.question, k=k, access_levels=access_levels)
                if access_levels
                else []
            )
        else:
            try:
                result = answer(item.question, retriever, llm, workspace, role, k=k)
            except LLMUnavailableError as e:
                # 일부만 채점한 보고서는 완전한 보고서와 섞여 비교를 흐리므로 남기지 않고,
                # 어디서 멈췄는지만 알린다
                raise LLMUnavailableError(
                    f"{item.id} ({len(records)}/{len(scored)} scored): {e}"
                ) from e
            retrieved = result.retrieved
        latency_ms = (time.perf_counter() - t0) * 1000
        records.append(_record(item, retrieved, k, result, latency_ms, strict))
    eval_seconds = time.perf_counter() - started

    overall = summarize(records)
    present = {r["qtype"] for r in records}
    by_qtype = {
        qtype: summarize([r for r in records if r["qtype"] == qtype])
        for qtype in QTYPE_ORDER
        if qtype in present
    }
    by_tag = {
        tag: summarize([r for r in records if tag in r["tags"]])
        for tag in TAGS
        if any(tag in r["tags"] for r in records)
    }
    latencies = [r["latency_ms"] for r in records]
    return EvalReport(
        config={
            "workspace": workspace.id,
            "role": role,
            "access_levels": access_levels,
            "k": k,
            "with_llm": llm is not None,
            "golden_items": len(items),
        },
        n=len(records),
        recall_at_k=overall["recall_at_k"],
        mrr=overall["mrr"],
        recall_at_k_strict=overall.get("recall_at_k_strict"),
        mrr_strict=overall.get("mrr_strict"),
        citation_precision=overall.get("citation_precision"),
        refusal_accuracy=overall.get("refusal_accuracy"),
        refusal_accuracy_strict=overall.get("refusal_accuracy_strict"),
        grounded_refusal_accuracy=overall.get("grounded_refusal_accuracy"),
        over_refusals=overall.get("over_refusals"),
        grounded_refusals=overall.get("grounded_refusals"),
        ungrounded_answers=overall.get("ungrounded_answers"),
        by_qtype=by_qtype,
        by_tag=by_tag,
        items=records,
        timing={
            "eval_seconds": eval_seconds,
            "mean_latency_ms": statistics.fmean(latencies),
            "median_latency_ms": statistics.median(latencies),
            "max_latency_ms": max(latencies),
        },
    )


def _label(ev: Evidence) -> str:
    parts = [ev.doc_id, ev.article, None if ev.page is None else f"p.{ev.page}"]
    return " ".join(part for part in parts if part)


def _record(
    item: GoldItem,
    retrieved: list[ScoredChunk],
    k: int,
    result: Answer | None,
    latency_ms: float,
    strict: bool = False,
) -> dict[str, Any]:
    chunks = [s.chunk for s in retrieved][: max(k, 0)]
    has_evidence = bool(item.evidence)
    record: dict[str, Any] = {
        "id": item.id,
        "qtype": item.qtype,
        "tags": list(item.tags),
        "question": item.question,
        "evidence": [_label(ev) for ev in item.evidence],
        # 문서 단위 근거(article·page 없음)는 그 문서의 아무 청크나 맞아 지표가 후해진다
        "doc_level_evidence": any(ev.article is None and ev.page is None for ev in item.evidence),
        "retrieved": [
            {
                "chunk_id": s.chunk.chunk_id,
                "doc_id": s.chunk.doc_id,
                "article": s.chunk.article,
                "page": s.chunk.page,
                "score": s.score,
                "hit": any(matches(s.chunk, ev) for ev in item.evidence),
            }
            for s in retrieved[: max(k, 0)]
        ],
        "recall_at_k": recall_at_k(chunks, item.evidence, k) if has_evidence else None,
        "reciprocal_rank": reciprocal_rank(chunks, item.evidence) if has_evidence else None,
        "latency_ms": latency_ms,
    }
    if strict:
        record["recall_at_k_strict"] = (
            recall_at_k_strict(chunks, item.evidence, k) if has_evidence else None
        )
        record["reciprocal_rank_strict"] = (
            reciprocal_rank_strict(chunks, item.evidence) if has_evidence else None
        )
    if result is not None:
        # 근거가 둘인 문항에서 하나만 들어와도 "있음". 범위 밖 문항은 근거가 없어 늘 False
        evidence_in_context = bool(record["recall_at_k"])
        record.update(
            {
                "refused": result.refused,
                "refusal_correct": refusal_correct(result.refused, item.qtype),
                "citations": [c.chunk_id for c in result.citations],
                "citation_precision": citation_precision(result.citations, item.evidence),
                "answer": result.text,
                "llm": None if result.llm is None else result.llm.model_dump(),
                "refusal_kind": result.refusal_kind.value,
                "refusal_correct_strict": refusal_correct(
                    result.refusal_kind is RefusalKind.EXACT, item.qtype
                ),
                # 정규화 전 모델 출력. 바꿔 말한 거절도 answer는 정해진 문구라 원문은 여기서 본다
                "raw_answer": None if result.llm is None else result.llm.text,
                "evidence_in_context": evidence_in_context,
                "grounded_refusal_correct": grounded_refusal_correct(
                    result.refused, evidence_in_context
                ),
            }
        )
    return record


# 요약 지표 이름 → 문항 기록의 키
_SUMMARY_KEYS = {
    "recall_at_k": "recall_at_k",
    "mrr": "reciprocal_rank",
    "recall_at_k_strict": "recall_at_k_strict",
    "mrr_strict": "reciprocal_rank_strict",
    "citation_precision": "citation_precision",
    "refusal_accuracy": "refusal_correct",
    "refusal_accuracy_strict": "refusal_correct_strict",
    "grounded_refusal_accuracy": "grounded_refusal_correct",
}
# 근거 기준 거절 지표의 내역: 이름 → (근거가 상위 k에 있는가, 거절했는가)
_REFUSAL_COUNTS = {
    "over_refusals": (True, True),
    "grounded_refusals": (False, True),
    "ungrounded_answers": (False, False),
}


def summarize(records: list[dict[str, Any]]) -> dict[str, float]:
    """문항 기록 묶음의 평균. 값이 있는 문항만 평균하고, 하나도 없으면 그 지표는 뺀다.

    recall·mrr은 근거가 있는 문항, citation_precision은 인용이 있는 문항(범위 밖 문항에 인용을
    달아 답했으면 0점으로 포함), refusal_accuracy는 답변을 받은 모든 문항이 대상이다.
    """
    summary: dict[str, float] = {"n": len(records)}
    for name, key in _SUMMARY_KEYS.items():
        values = [float(r[key]) for r in records if r.get(key) is not None]
        if values:
            summary[name] = math.fsum(values) / len(values)
    answered = [r for r in records if r.get("evidence_in_context") is not None]
    if answered:
        for name, case in _REFUSAL_COUNTS.items():
            summary[name] = sum((r["evidence_in_context"], r["refused"]) == case for r in answered)
    return summary


# ---- CLI ----


def load_corpus_chunks(
    sources_path: Path,
    raw_dir: Path,
    repo_root: Path,
    workspace_id: str,
    max_chars: int | None = None,
) -> list[Chunk]:
    """sources.yaml의 문서를 모두 읽어 청크로 나눈다. 하나라도 없으면 FileNotFoundError.

    문서가 빠진 채 평가하면 그 문서를 근거로 한 문항이 오류 없이 0점이 되므로 멈춘다.
    max_chars는 chunk_document에 넘긴다(긴 조항 나누기, None이면 나누지 않는다).
    """
    sources = load_sources(sources_path)
    paths = {s.doc_id: source_file(s, raw_dir, repo_root) for s in sources}
    missing = [f"{doc_id} ({path})" for doc_id, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "missing documents: "
            + ", ".join(missing)
            + f"; run: python scripts/download_corpus.py {workspace_id}"
        )
    chunks: list[Chunk] = []
    for source in sources:
        doc = load_document(paths[source.doc_id], source.doc_meta(), workspace_id)
        chunks.extend(chunk_document(doc, max_chars))
    return chunks


def format_markdown(report: EvalReport) -> str:
    """전체·qtype별·근거 단위별·태그별 표. 근거 단위별 행으로 문서 단위 근거의 부풀림을 본다.

    LLM 표는 지표마다 분모가 달라 "근거 n"(Recall·MRR의 분모)과 "인용 n"(인용 정밀도의
    분모) 열을 함께 보여 준다. 거절 정확도의 분모는 n이다.
    """
    config = report.config
    with_llm = bool(config.get("with_llm"))
    k = config.get("k")
    # (열 이름, 지표 이름, 분모 열인가) — 분모 열은 그 지표 값이 있는 문항 수를 센다
    layout = [(f"Recall@{k}", "recall_at_k", False), ("MRR", "mrr", False)]
    if report.recall_at_k_strict is not None:
        layout += [(f"Recall@{k}(답 문구)", "recall_at_k_strict", False)]
        layout += [("MRR(답 문구)", "mrr_strict", False)]
    if with_llm:
        layout = [
            ("근거 n", "recall_at_k", True),
            *layout,
            ("인용 n", "citation_precision", True),
            ("인용 정밀도", "citation_precision", False),
            ("거절 정확도", "refusal_accuracy", False),
            ("거절 정확도(문구 일치만)", "refusal_accuracy_strict", False),
            ("근거 기준 거절 정확도", "grounded_refusal_accuracy", False),
        ]
    columns = ["구분", "n", *(column for column, _, _ in layout)]

    def row(label: str, summary: dict[str, float], records: list[dict[str, Any]]) -> str:
        cells = [label, str(int(summary["n"]))]
        for _, name, is_count in layout:
            if is_count:
                key = _SUMMARY_KEYS[name]
                cells.append(str(sum(r.get(key) is not None for r in records)))
            else:
                cells.append(f"{summary[name]:.3f}" if name in summary else "-")
        return "| " + " | ".join(cells) + " |"

    overall: dict[str, float] = {"n": report.n}
    for name in _SUMMARY_KEYS:
        value = getattr(report, name)
        if value is not None:
            overall[name] = value
    rows = [row("전체", overall, report.items)]
    rows += [
        row(qtype, summary, [r for r in report.items if r["qtype"] == qtype])
        for qtype, summary in report.by_qtype.items()
    ]
    article_only = [r for r in report.items if r["evidence"] and not r["doc_level_evidence"]]
    doc_level = [r for r in report.items if r["doc_level_evidence"]]
    if article_only:
        rows.append(row("조항 단위 근거만", summarize(article_only), article_only))
    if doc_level:
        rows.append(row("문서 단위 근거 포함", summarize(doc_level), doc_level))
    for tag, summary in report.by_tag.items():
        rows.append(row(f"태그 {tag}", summary, [r for r in report.items if tag in r["tags"]]))

    levels = ", ".join(config.get("access_levels", []))
    retriever = config.get("retriever", "?")
    if config.get("reranker"):
        retriever += f" + {config['reranker']} (candidates={config.get('candidates')})"
    lines = [
        f"### {config.get('workspace')} · {retriever} · k={k} · "
        f"role={config.get('role')} ({levels})",
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *rows,
    ]
    if report.over_refusals is not None:
        lines += [
            "",
            f"근거가 있는데 거절 {report.over_refusals} · 근거가 없어서 거절 "
            f"{report.grounded_refusals} · 근거가 없는데 답함 {report.ungrounded_answers} "
            f"(근거 = 상위 {k}개에 라벨된 근거가 하나라도 있음)",
        ]
    details = [
        f"{key}={config[key]}"
        for key in (
            "n_chunks",
            "embed_model",
            "llm_model",
            "rerank_truncated",
            "rerank_pairs",
            "commit",
        )
        if config.get(key) is not None
    ]
    if details:
        lines += ["", " · ".join(details)]
    if report.timing:
        lines += ["", " · ".join(f"{key}={value:.2f}" for key, value in report.timing.items())]
    return "\n".join(lines)


def save_report(report: EvalReport, reports_dir: Path, retriever: str, now: datetime) -> Path:
    """reports_dir/{YYYYmmdd-HHMM}-{retriever}.json에 저장한다.

    같은 분에 같은 검색기로 다시 돌리면 덮어쓰지 않고 -2, -3을 붙인다.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y%m%d-%H%M}-{retriever}"
    text = report.model_dump_json(indent=2) + "\n"
    for number in itertools.count(1):
        path = reports_dir / (f"{stem}.json" if number == 1 else f"{stem}-{number}.json")
        try:
            with path.open("x", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except FileExistsError:
            continue
        return path
    raise AssertionError("unreachable")


def _display_path(path: Path, root: Path) -> str:
    """보고서에 남길 경로. 저장소 안이면 상대 경로(내 PC의 사용자 폴더 이름이 남지 않게)."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corpus_digest(sources: list[CorpusSource], raw_dir: Path, repo_root: Path) -> str:
    """색인한 파일 전체의 지문. 원문을 다시 받아 내용이 바뀌면 값이 달라진다."""
    digest = hashlib.sha256()
    for source in sources:
        file_hash = _sha256(source_file(source, raw_dir, repo_root))
        digest.update(f"{source.doc_id}:{file_hash}\n".encode())
    return digest.hexdigest()[:12]


def _git_commit(repo_root: Path) -> str | None:
    """코드 버전. 커밋하지 않은 변경이 있으면 -dirty를 붙인다."""
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return head + ("-dirty" if status else "")


def _load_workspace(store: WorkspaceStore, workspace_id: str) -> Workspace:
    try:
        return store.get(workspace_id)
    except WorkspaceNotFoundError:
        if workspace_id != BANK_WORKSPACE.id:
            raise
        return BANK_WORKSPACE


def make_reranker(key: str) -> Reranker:
    """--reranker 선택지로 리랭커를 만든다. 모델은 첫 score() 때 불러온다."""
    return CrossEncoderReranker(RERANKER_MODELS[key])


def build_retriever(
    kind: str, chunks: list[Chunk], embed_model: str, min_candidates: int = 0
) -> tuple[Retriever, dict[str, Any], dict[str, float]]:
    """(검색기, 검색기 설정, {"model_load_seconds", "index_seconds"}).

    min_candidates는 hybrid가 BM25·dense에서 각각 받는 후보 수의 하한이다(기본값보다 클 때만
    쓰인다). 리랭커에 넘길 후보 수를 주면 hybrid가 그만큼을 합쳐서 돌려줄 수 있다.
    """
    timing = {"model_load_seconds": 0.0, "index_seconds": 0.0}
    bm25 = BM25Index()
    params: dict[str, Any] = {}
    if kind != "dense":
        started = time.perf_counter()
        bm25.build(chunks)
        timing["index_seconds"] += time.perf_counter() - started
        params.update({"bm25_k1": bm25.k1, "bm25_b": bm25.b})
    if kind == "bm25":
        return bm25, params, timing
    # 모델·GPU가 필요한 import는 dense·hybrid일 때만
    from axbuilder.retrieval.dense import DenseIndex, SentenceTransformerEmbedder

    started = time.perf_counter()
    embedder = SentenceTransformerEmbedder(embed_model)
    timing["model_load_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    dense = DenseIndex(embedder, ":memory:", "eval")
    dense.build(chunks)
    timing["index_seconds"] += time.perf_counter() - started
    params.update({"embed_model": embed_model, "embed_dim": embedder.dim})
    if kind == "dense":
        return dense, params, timing
    hybrid = HybridRetriever(bm25, dense)
    # search(k)는 이미 max(candidates, k)개씩 받으므로 결과는 같다. 실제로 쓰인 값이 보고서의
    # retriever_params.candidates에 남도록 여기서 맞춘다
    hybrid.candidates = max(hybrid.candidates, min_candidates)
    params.update({"rrf_k": RRF_K, "candidates": hybrid.candidates})
    return hybrid, params, timing


def _rerank_counts(reranker: Reranker | None) -> tuple[int | None, int | None]:
    """(잘린 쌍 수, 채점한 쌍 수)의 누적값. 세지 않는 리랭커면 None."""
    return getattr(reranker, "truncated", None), getattr(reranker, "scored", None)


def _delta(before: int | None, after: int | None) -> int | None:
    return None if before is None or after is None else after - before


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔에서 한글이 깨지지 않게
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--workspace", default="bank")
    parser.add_argument("--retriever", choices=RETRIEVERS, required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--with-llm", action="store_true", help="answer()로 답변 지표까지")
    parser.add_argument(
        "--allow-cloud",
        action="store_true",
        help="AXB_LLM_MODE=cloud에서 --with-llm으로 유료 Claude API 호출을 허락",
    )
    parser.add_argument("--role", default=DEFAULT_ROLE)
    parser.add_argument(
        "--reranker",
        choices=RERANKERS,
        default="none",
        help="검색기의 후보를 다시 줄 세울 크로스 인코더 (기본 none)",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=None,
        help=f"리랭커에 넘기는 후보 수 (기본 {DEFAULT_RERANK_CANDIDATES}, --reranker와 함께만)",
    )
    parser.add_argument(
        "--max-chunk-chars",
        type=int,
        default=None,
        help=f"이 글자 수보다 긴 본문 조항을 항·호 경계에서 나눈다 ({MIN_MAX_CHARS} 이상)",
    )
    parser.add_argument("--corpus-dir", type=Path, default=REPO_ROOT / "corpus")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be >= 1")
    use_reranker = args.reranker != "none"
    if args.candidates is not None and not use_reranker:
        # 리랭커 없이 받아 두면 hybrid 내부 후보 수를 바꾸는 옵션으로 오해하기 쉽다
        parser.error("--candidates needs --reranker (it is the number of passages reranked)")
    candidates = DEFAULT_RERANK_CANDIDATES if args.candidates is None else args.candidates
    if candidates < 1:
        parser.error("--candidates must be >= 1")
    if args.max_chunk_chars is not None and args.max_chunk_chars < MIN_MAX_CHARS:
        parser.error(f"--max-chunk-chars must be >= {MIN_MAX_CHARS}")

    started_at = datetime.now()
    started = time.perf_counter()
    settings = Settings.from_env()
    store = WorkspaceStore(settings.data_dir / "workspaces")
    workspace = _load_workspace(store, args.workspace)
    if args.role not in workspace.roles:
        parser.error(f"unknown role {args.role!r}; choose from {sorted(workspace.roles)}")

    corpus_dir = args.corpus_dir / workspace.id
    repo_root = args.corpus_dir.resolve().parent  # sources.yaml의 path는 저장소 루트 기준
    raw_dir = store.path(workspace.id) / "raw"
    golden_path = corpus_dir / "golden.jsonl"
    items = load_golden(golden_path)
    sources = load_sources(corpus_dir / "sources.yaml")
    problems = validate_golden(items, {s.doc_id for s in sources})
    if problems:
        print("golden set problems:\n" + "\n".join(problems), file=sys.stderr)
        return 1

    llm_model = None
    if args.with_llm:
        llm_model = settings.claude_model if settings.llm_mode == "cloud" else settings.local_model
        # 예열 1번 + 문항마다 최대 1번 (검색 결과가 없는 문항은 LLM을 부르지 않는다)
        max_calls = len(items) + 1
        if settings.llm_mode == "cloud" and not args.allow_cloud:
            parser.error(
                f"AXB_LLM_MODE=cloud: --with-llm makes up to {max_calls} paid Claude API calls "
                f"({llm_model}); add --allow-cloud to proceed"
            )
        print(
            f"LLM: {settings.llm_mode} {llm_model}, up to {max_calls} calls "
            f"(1 warm-up + {len(items)} questions)",
            file=sys.stderr,
        )

    t0 = time.perf_counter()
    try:
        chunks = load_corpus_chunks(
            corpus_dir / "sources.yaml", raw_dir, repo_root, workspace.id, args.max_chunk_chars
        )
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    load_seconds = time.perf_counter() - t0

    reranker: Reranker | None = None
    rerank_load_seconds = 0.0
    if use_reranker:
        # 색인(임베딩)보다 먼저 불러와, 모델이 없으면 기다리지 않고 바로 알린다
        reranker = make_reranker(args.reranker)
        t0 = time.perf_counter()
        try:
            reranker.score("예금", ["예금자 보호 한도는 1억원이다"])
        except RerankerUnavailableError as e:
            print(f"reranker unavailable: {e}", file=sys.stderr)
            return 1
        rerank_load_seconds = time.perf_counter() - t0

    retriever, params, build_timing = build_retriever(
        args.retriever, chunks, settings.embed_model, candidates if use_reranker else 0
    )
    if reranker is not None:
        build_timing["model_load_seconds"] += rerank_load_seconds
        retriever = RerankRetriever(retriever, reranker, candidates)
    # 첫 질문의 지연에 CUDA 초기화·Ollama 모델 적재 시간이 섞이지 않게 한 번 미리 부른다.
    # 리랭커가 있으면 후보 수만큼의 배치도 여기서 한 번 돈다
    t0 = time.perf_counter()
    retriever.search("예금", k=1)
    llm = get_llm(settings) if args.with_llm else None
    try:
        if llm is not None:
            llm.generate("짧게 답하세요.", "안녕하세요", max_tokens=8)
        warmup_seconds = time.perf_counter() - t0
        # 잘린 입력 수는 평가 문항에서 생긴 것만 센다(위의 적재·예열 호출 제외)
        before = _rerank_counts(reranker)
        report = run_eval(items, retriever, args.k, llm, workspace, args.role)
    except LLMUnavailableError as e:
        print(f"LLM unavailable: {e}", file=sys.stderr)
        return 1

    report.config.update(
        {
            "retriever": args.retriever,
            "retriever_params": params,
            "embed_model": params.get("embed_model"),
            "llm_mode": settings.llm_mode if llm is not None else None,
            "llm_model": llm_model,
            "n_docs": len(sources),
            "n_chunks": len(chunks),
            "golden": _display_path(golden_path, repo_root),
            "golden_sha256": _sha256(golden_path)[:12],
            "corpus_sha256": _corpus_digest(sources, raw_dir, repo_root),
            "commit": _git_commit(REPO_ROOT),
            "started_at": started_at.isoformat(timespec="seconds"),
        }
    )
    label = args.retriever
    if reranker is not None:
        # 리랭커가 없을 때는 넣지 않는다: --reranker none의 보고서는 이전과 같아야 한다
        after = _rerank_counts(reranker)
        report.config.update(
            {
                "reranker": reranker.name,
                "candidates": candidates,
                "rerank_truncated": _delta(before[0], after[0]),
                "rerank_pairs": _delta(before[1], after[1]),
            }
        )
        label = f"{args.retriever}+{args.reranker}-c{candidates}"
    if args.max_chunk_chars is not None:
        # 리랭커와 같은 규칙: 옵션이 없으면 넣지 않아 보고서·파일 이름이 이전과 같다
        report.config["chunking"] = {"max_chars": args.max_chunk_chars}
        label += f"-m{args.max_chunk_chars}"
    # 읽는 순서대로: 준비(읽기·모델·색인·예열) → 평가 → 전체
    report.timing = {
        "load_seconds": load_seconds,
        **build_timing,
        "warmup_seconds": warmup_seconds,
        **report.timing,
        "total_seconds": time.perf_counter() - started,
    }
    path = save_report(report, args.reports_dir / workspace.id, label, started_at)
    print(format_markdown(report))
    print(f"\nsaved: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
