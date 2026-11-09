"""평가 실행기: run_eval(검색 지표 / LLM 답변 지표)과 CLI 보조 함수."""

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from axbuilder.eval.dataset import Evidence, GoldItem
from axbuilder.eval.run import (
    EvalReport,
    format_markdown,
    load_corpus_chunks,
    main,
    run_eval,
    save_report,
)
from axbuilder.generation.answer import REFUSAL_TEXT
from axbuilder.generation.llm import FakeLLM, LLMResponse, LLMUnavailableError
from axbuilder.models import Chunk, ScoredChunk
from axbuilder.workspace import Workspace

ws = Workspace(
    id="bank",
    name="AXB은행(가상)",
    industry="finance",
    roles={"상담원": ["public"], "준법감시": ["public", "internal"], "견학생": []},
)


def chunk(doc_id: str, index: int, article: str | None = None) -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}::{index:04d}",
        doc_id=doc_id,
        workspace_id="bank",
        title=doc_id,
        text="본문",
        article=article,
    )


law1 = chunk("banking-act", 1, article="제1조")
law2 = chunk("banking-act", 2, article="제2조")
prod = chunk("prod-fixed-deposit", 0)

items_with_one_oos = [
    GoldItem(
        id="bank-001",
        question="q1",
        answer="a1",
        evidence=[Evidence(doc_id="banking-act", article="제2조")],
        qtype="lookup",
    ),
    GoldItem(
        id="bank-002",
        question="q2",
        answer="a2",
        evidence=[Evidence(doc_id="prod-fixed-deposit")],
        qtype="table",
    ),
    GoldItem(id="bank-003", question="q3", answer=REFUSAL_TEXT, evidence=[], qtype="out_of_scope"),
]


class QueryRetriever:
    """질문마다 정해 둔 순위를 돌려주고 받은 인자를 기록하는 가짜 검색기."""

    def __init__(self, results: dict[str, list[Chunk]]) -> None:
        self.results = results
        self.calls: list[tuple[str, int, list[str] | None]] = []

    def search(
        self, query: str, k: int = 5, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]:
        self.calls.append((query, k, access_levels))
        ranked = self.results.get(query, [])[:k]
        return [ScoredChunk(chunk=c, score=1.0 / (i + 1)) for i, c in enumerate(ranked)]


@pytest.fixture
def fake_retriever() -> QueryRetriever:
    # q1: 정답(제2조)이 1위, q2: 정답 문서(prod)가 없음, q3: 범위 밖
    return QueryRetriever({"q1": [law2, law1], "q2": [law1, law2], "q3": [law1]})


# --- 계획의 테스트 (완료 조건 명세) ---


def test_run_eval_retrieval_only_skips_out_of_scope(fake_retriever):
    report = run_eval(
        items_with_one_oos, fake_retriever, k=5, llm=None, workspace=ws, role="준법감시"
    )
    assert report.n == len(items_with_one_oos) - 1 and report.refusal_accuracy is None


# --- 검색 지표만 ---


def test_retrieval_only_metrics_and_breakdown(fake_retriever):
    report = run_eval(
        items_with_one_oos, fake_retriever, k=5, llm=None, workspace=ws, role="준법감시"
    )
    assert report.recall_at_k == 0.5 and report.mrr == 0.5
    assert report.citation_precision is None
    assert report.by_qtype == {
        "lookup": {"n": 1, "recall_at_k": 1.0, "mrr": 1.0},
        "table": {"n": 1, "recall_at_k": 0.0, "mrr": 0.0},
    }
    # 범위 밖 문항은 검색하지 않는다. 역할의 접근 등급과 k를 그대로 넘긴다
    assert fake_retriever.calls == [
        ("q1", 5, ["public", "internal"]),
        ("q2", 5, ["public", "internal"]),
    ]


def test_retrieval_only_records_each_item(fake_retriever):
    report = run_eval(
        items_with_one_oos, fake_retriever, k=5, llm=None, workspace=ws, role="준법감시"
    )
    first, second = report.items
    assert first["id"] == "bank-001" and first["qtype"] == "lookup"
    assert [r["chunk_id"] for r in first["retrieved"]] == [law2.chunk_id, law1.chunk_id]
    assert [r["hit"] for r in first["retrieved"]] == [True, False]
    assert first["recall_at_k"] == 1.0 and first["reciprocal_rank"] == 1.0
    assert first["doc_level_evidence"] is False and second["doc_level_evidence"] is True
    assert first["latency_ms"] >= 0.0
    assert report.config["k"] == 5 and report.config["role"] == "준법감시"
    json.dumps(report.model_dump(mode="json"), ensure_ascii=False)  # 보고서는 JSON으로 저장된다


def test_k_cuts_the_ranking(fake_retriever):
    report = run_eval(
        items_with_one_oos, fake_retriever, k=1, llm=None, workspace=ws, role="준법감시"
    )
    assert report.recall_at_k == 0.5  # q1은 1위가 정답, q2는 없음
    assert all(len(item["retrieved"]) <= 1 for item in report.items)


def test_unknown_role_raises(fake_retriever):
    with pytest.raises(ValueError):
        run_eval(items_with_one_oos, fake_retriever, k=5, llm=None, workspace=ws, role="해커")


def test_role_without_access_levels_retrieves_nothing(fake_retriever):
    report = run_eval(
        items_with_one_oos, fake_retriever, k=5, llm=None, workspace=ws, role="견학생"
    )
    assert report.recall_at_k == 0.0 and fake_retriever.calls == []


def test_nothing_to_score_raises(fake_retriever):
    with pytest.raises(ValueError):
        run_eval(items_with_one_oos[2:], fake_retriever, k=5, llm=None, workspace=ws, role="상담원")


# --- LLM 답변까지 ---


def test_with_llm_scores_answers_on_every_item(fake_retriever):
    llm = FakeLLM("답입니다 [1].")  # 늘 1번 자료를 인용해 답한다
    report = run_eval(
        items_with_one_oos, fake_retriever, k=5, llm=llm, workspace=ws, role="준법감시"
    )
    assert report.n == 3 and len(llm.calls) == 3
    # 검색 지표는 근거가 있는 문항(q1, q2)만
    assert report.recall_at_k == 0.5 and report.mrr == 0.5
    # q1은 제2조(정답) 인용 1.0, q2는 제1조 인용 0.0, q3(범위 밖)도 인용했으니 0.0
    assert report.citation_precision == pytest.approx(1 / 3)
    # q1·q2는 답해서 맞고, q3는 거절해야 하는데 답했다
    assert report.refusal_accuracy == pytest.approx(2 / 3)
    assert report.by_qtype["out_of_scope"] == {
        "n": 1,
        "citation_precision": 0.0,
        "refusal_accuracy": 0.0,
    }
    oos = report.items[2]
    assert oos["refused"] is False and oos["refusal_correct"] is False
    assert oos["citations"] == [law1.chunk_id] and oos["recall_at_k"] is None


def test_with_llm_refusing_everything(fake_retriever):
    report = run_eval(
        items_with_one_oos,
        fake_retriever,
        k=5,
        llm=FakeLLM(REFUSAL_TEXT),
        workspace=ws,
        role="준법감시",
    )
    assert report.refusal_accuracy == pytest.approx(1 / 3)  # 범위 밖 1문항만 맞음
    assert report.citation_precision is None  # 인용이 하나도 없다
    assert report.items[0]["answer"] == REFUSAL_TEXT and report.items[0]["refused"] is True


class FlakyLLM(FakeLLM):
    """ok_calls번은 답하고 그다음부터 LLMUnavailableError를 낸다(실행 도중 Ollama가 꺼진 경우)."""

    def __init__(self, reply: str, ok_calls: int) -> None:
        super().__init__(reply)
        self.ok_calls = ok_calls

    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        if len(self.calls) >= self.ok_calls:
            raise LLMUnavailableError("Ollama server is not reachable")
        return super().generate(system, user, max_tokens)


def test_with_llm_failure_names_the_item(fake_retriever):
    # 두 번째 문항에서 끊기면 어느 문항에서, 몇 문항을 채점한 뒤 멈췄는지 알려 준다
    with pytest.raises(LLMUnavailableError, match=r"bank-002 \(1/3 scored\).*not reachable"):
        run_eval(
            items_with_one_oos,
            fake_retriever,
            k=5,
            llm=FlakyLLM("답 [1].", ok_calls=1),
            workspace=ws,
            role="준법감시",
        )


# --- CLI 보조 함수 ---


def report_for(fake_retriever: QueryRetriever, **config) -> EvalReport:
    report = run_eval(
        items_with_one_oos,
        fake_retriever,
        k=5,
        llm=FakeLLM("답 [1]."),
        workspace=ws,
        role="준법감시",
    )
    report.config.update(config)
    return report


def test_format_markdown_has_overall_qtype_and_article_only_rows(fake_retriever):
    text = format_markdown(report_for(fake_retriever, retriever="bm25", workspace="bank"))
    lines = text.splitlines()
    assert any("Recall@5" in line and "MRR" in line and "거절 정확도" in line for line in lines)
    assert any(line.startswith("| lookup | 1 | 1 | 1.000 | 1.000 | 1 |") for line in lines)
    # 문서 단위 근거(q2)를 뺀 행: 근거가 있는 문항 중 q1만
    assert any(line.startswith("| 조항 단위 근거만 | 1 | 1 | 1.000 |") for line in lines)


def test_format_markdown_with_llm_shows_each_metric_denominator(fake_retriever):
    # LLM 표는 지표마다 분모가 다르다: Recall·MRR은 근거가 있는 문항, 인용 정밀도는 인용한 문항
    lines = format_markdown(report_for(fake_retriever)).splitlines()
    assert "| 구분 | n | 근거 n | Recall@5 | MRR | 인용 n | 인용 정밀도 | 거절 정확도 |" in lines
    assert "| 전체 | 3 | 2 | 0.500 | 0.500 | 3 | 0.333 | 0.667 |" in lines
    assert "| out_of_scope | 1 | 0 | - | - | 1 | 0.000 | 0.000 |" in lines
    refusing = run_eval(
        items_with_one_oos,
        fake_retriever,
        k=5,
        llm=FakeLLM(REFUSAL_TEXT),
        workspace=ws,
        role="준법감시",
    )
    # 모두 거절하면 인용한 문항이 없어 인용 정밀도를 잴 수 없다
    assert "| 전체 | 3 | 2 | 0.500 | 0.500 | 0 | - | 0.333 |" in format_markdown(refusing)


def test_format_markdown_retrieval_only_has_no_answer_columns(fake_retriever):
    report = run_eval(
        items_with_one_oos, fake_retriever, k=5, llm=None, workspace=ws, role="준법감시"
    )
    text = format_markdown(report)
    assert "Recall@5" in text and "거절 정확도" not in text and "인용 정밀도" not in text


def test_save_report_does_not_overwrite(tmp_path, fake_retriever):
    report = report_for(fake_retriever)
    now = datetime(2026, 11, 10, 9, 5)
    first = save_report(report, tmp_path / "bank", "hybrid", now)
    second = save_report(report, tmp_path / "bank", "hybrid", now)
    assert first.name == "20261110-0905-hybrid.json"
    assert second.name == "20261110-0905-hybrid-2.json"
    assert EvalReport.model_validate_json(first.read_text(encoding="utf-8")) == report


def write_corpus(root: Path, with_url_doc: bool) -> Path:
    """root/corpus/bank/ 아래 sources.yaml, 가상 문서 1개, golden.jsonl을 만든다."""
    corpus = root / "corpus" / "bank"
    (corpus / "internal").mkdir(parents=True)
    (corpus / "internal" / "guide.md").write_text(
        "제1조(목적) 이 지침은 상담 응대 원칙을 정한다.\n"
        "제2조(콜백) 콜백 요청은 4시간 안에 처리한다.\n",
        encoding="utf-8",
    )
    sources = [
        {
            "doc_id": "guide-counseling",
            "title": "상담 응대 지침",
            "source": "가상",
            "doc_type": "internal",
            "access_level": "internal",
            "path": "corpus/bank/internal/guide.md",
            "format": "md",
            "license": "직접 작성",
        }
    ]
    if with_url_doc:
        sources.append(
            {
                "doc_id": "banking-act",
                "title": "은행법",
                "source": "국가법령정보센터",
                "doc_type": "law",
                "url": "https://www.law.go.kr/x",
                "format": "html",
                "license": "저작권법 제7조",
            }
        )
    (corpus / "sources.yaml").write_text(
        yaml.safe_dump(sources, allow_unicode=True), encoding="utf-8"
    )
    golden = [
        {
            "id": "bank-001",
            "question": "콜백 요청은 몇 시간 안에 처리해야 하나요?",
            "answer": "4시간",
            "evidence": [{"doc_id": "guide-counseling", "article": "제2조"}],
            "qtype": "lookup",
        },
        {
            "id": "bank-002",
            "question": "오늘 환율은?",
            "answer": REFUSAL_TEXT,
            "evidence": [],
            "qtype": "out_of_scope",
        },
    ]
    (corpus / "golden.jsonl").write_text(
        "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in golden), encoding="utf-8"
    )
    return corpus


def test_load_corpus_chunks_reads_repo_documents(tmp_path):
    corpus = write_corpus(tmp_path, with_url_doc=False)
    chunks = load_corpus_chunks(corpus / "sources.yaml", tmp_path / "raw", tmp_path, "bank")
    assert [c.article for c in chunks] == ["제1조", "제2조"]
    assert {c.access_level for c in chunks} == {"internal"}


def test_load_corpus_chunks_fails_when_a_document_is_missing(tmp_path):
    # 문서가 빠진 채 평가하면 그 문서의 문항이 조용히 0점이 된다
    corpus = write_corpus(tmp_path, with_url_doc=True)
    with pytest.raises(FileNotFoundError, match="banking-act"):
        load_corpus_chunks(corpus / "sources.yaml", tmp_path / "raw", tmp_path, "bank")


def cli_args(corpus: Path, reports: Path, *extra: str) -> list[str]:
    return [
        "--workspace",
        "bank",
        "--retriever",
        "bm25",
        "--k",
        "5",
        "--corpus-dir",
        str(corpus.parent),
        "--reports-dir",
        str(reports),
        *extra,
    ]


def test_cli_bm25_end_to_end(tmp_path, monkeypatch, capsys):
    corpus = write_corpus(tmp_path, with_url_doc=False)
    monkeypatch.setenv("AXB_DATA_DIR", str(tmp_path / "data"))
    reports = tmp_path / "reports"
    code = main(cli_args(corpus, reports))
    assert code == 0
    saved = list((reports / "bank").glob("*-bm25.json"))
    assert len(saved) == 1
    report = EvalReport.model_validate_json(saved[0].read_text(encoding="utf-8"))
    assert report.n == 1 and report.recall_at_k == 1.0  # 범위 밖 문항은 빠진다
    assert report.config["retriever"] == "bm25" and report.config["n_chunks"] == 2
    assert report.config["role"] == "준법감시"  # 기본 역할은 내부 지침까지 본다
    assert report.config["golden"] == "corpus/bank/golden.jsonl"  # 저장소 기준 상대 경로
    assert "| 전체 | 1 | 1.000 |" in capsys.readouterr().out


def use_llm(monkeypatch, tmp_path: Path, mode: str, llm) -> list[object]:
    """main()이 get_llm 대신 가짜 LLM을 받게 하고, get_llm에 넘어온 설정을 기록한다."""
    monkeypatch.setenv("AXB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AXB_LLM_MODE", mode)
    monkeypatch.delenv("AXB_LOCAL_MODEL", raising=False)
    monkeypatch.delenv("AXB_CLAUDE_MODEL", raising=False)
    requested: list[object] = []

    def fake_get_llm(settings):
        requested.append(settings)
        return llm

    monkeypatch.setattr("axbuilder.eval.run.get_llm", fake_get_llm)
    return requested


def test_cli_cloud_llm_needs_allow_cloud(tmp_path, monkeypatch, capsys):
    # 클라우드 모드의 --with-llm은 문항마다 유료 API를 부르므로 명시적으로 허락해야 한다
    corpus = write_corpus(tmp_path, with_url_doc=False)
    requested = use_llm(monkeypatch, tmp_path, "cloud", FakeLLM("답 [1]."))
    reports = tmp_path / "reports"
    with pytest.raises(SystemExit) as exc:
        main(cli_args(corpus, reports, "--with-llm"))
    assert exc.value.code == 2
    assert requested == [] and not reports.exists()
    err = capsys.readouterr().err
    assert "--allow-cloud" in err and "up to 3 paid" in err  # 예열 1 + 문항 2


def test_cli_with_llm_announces_calls_and_saves_report(tmp_path, monkeypatch, capsys):
    corpus = write_corpus(tmp_path, with_url_doc=False)
    llm = FakeLLM("4시간입니다 [1].")
    use_llm(monkeypatch, tmp_path, "cloud", llm)
    reports = tmp_path / "reports"
    assert main(cli_args(corpus, reports, "--with-llm", "--allow-cloud")) == 0
    # 시작할 때 모드·모델·최대 호출 수를 알린다 (예열 1 + 문항 2)
    assert "LLM: cloud claude-opus-5-5, up to 3 calls" in capsys.readouterr().err
    assert 2 <= len(llm.calls) <= 3  # 범위 밖 문항은 검색 결과가 없으면 LLM을 부르지 않는다
    (saved,) = (reports / "bank").glob("*-bm25.json")
    report = EvalReport.model_validate_json(saved.read_text(encoding="utf-8"))
    assert report.n == 2 and report.config["with_llm"] is True
    assert report.config["llm_mode"] == "cloud"
    assert report.config["llm_model"] == "claude-opus-5-5"


def test_cli_llm_failure_mid_run_saves_no_report(tmp_path, monkeypatch, capsys):
    # 예열은 통과하고 첫 문항에서 끊긴 경우: 일부만 채점한 보고서는 남기지 않는다
    corpus = write_corpus(tmp_path, with_url_doc=False)
    use_llm(monkeypatch, tmp_path, "local", FlakyLLM("4시간입니다 [1].", ok_calls=1))
    reports = tmp_path / "reports"
    assert main(cli_args(corpus, reports, "--with-llm")) == 1
    assert not list(reports.glob("**/*.json"))
    err = capsys.readouterr().err
    assert "LLM unavailable: bank-001 (0/2 scored)" in err and "not reachable" in err
