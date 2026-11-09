"""받아 둔 코퍼스로 검색기를 만들고 answer()로 질문에 답해 본다 (수동 E2E 확인용).

사용: python scripts/demo_answer.py [--retriever hybrid|bm25] [--role 상담원] [질문 ...]
질문을 주지 않으면 기본 질문 3개(법령 조회 / 상품설명서 표 / 자료 밖)를 돌린다.
먼저 scripts/download_corpus.py bank 로 원문을 받아 두어야 한다. LLM은 AXB_LLM_MODE를 따른다
(기본 local = Ollama). 색인은 메모리에만 만들고 디스크에 저장하지 않는다.
"""

import argparse
import sys
import time
from pathlib import Path

from axbuilder.corpus import load_sources, source_file
from axbuilder.generation.answer import answer
from axbuilder.generation.llm import get_llm
from axbuilder.ingest.chunking import chunk_document
from axbuilder.ingest.loaders import load_document
from axbuilder.models import Answer, Chunk
from axbuilder.retrieval.bm25 import BM25Index
from axbuilder.retrieval.hybrid import HybridRetriever, Retriever
from axbuilder.settings import Settings
from axbuilder.workspace import Workspace, WorkspaceNotFoundError, WorkspaceStore

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_QUESTIONS = [
    "예금자보호 한도는 얼마인가요?",
    "AXB 든든 정기예금에 12개월 만기일시지급식으로 가입하면 기본금리는 몇 %인가요?",
    "AXB은행 신용카드의 연회비는 얼마인가요?",
]

# workspace.yaml이 아직 없을 때(Task 10 API 전) 쓰는 가상 은행 설정
DEFAULT_BANK = Workspace(
    id="bank",
    name="AXB은행(가상)",
    industry="finance",
    roles={"상담원": ["public"], "준법감시": ["public", "internal"]},
)


def load_chunks(workspace_id: str, raw_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for source in load_sources(REPO_ROOT / "corpus" / workspace_id / "sources.yaml"):
        path = source_file(source, raw_dir, REPO_ROOT)
        if not path.is_file():
            print(f"[skip] {source.doc_id}: {path} 없음 (download_corpus.py 먼저)", file=sys.stderr)
            continue
        chunks.extend(chunk_document(load_document(path, source.doc_meta(), workspace_id)))
    return chunks


def build_retriever(kind: str, chunks: list[Chunk], settings: Settings) -> Retriever:
    bm25 = BM25Index()
    bm25.build(chunks)
    if kind == "bm25":
        return bm25
    # 모델·GPU가 필요한 import는 hybrid일 때만
    from axbuilder.retrieval.dense import DenseIndex, SentenceTransformerEmbedder

    dense = DenseIndex(SentenceTransformerEmbedder(settings.embed_model), ":memory:", "demo")
    dense.build(chunks)
    return HybridRetriever(bm25, dense)


def show(question: str, a: Answer) -> None:
    print(f"\n### 질문: {question}")
    print(f"거절: {a.refused}")
    print(f"답변: {a.text}")
    print("인용:")
    for i, c in enumerate(a.citations, start=1):
        where = c.article or (f"p.{c.page}" if c.page is not None else "")
        print(f"  ({i}) {c.doc_id} {where} — {c.title} [{c.chunk_id}]")
    if not a.citations:
        print("  (없음)")
    print("검색 결과(프롬프트에 넣은 순서 = 자료 번호):")
    for n, s in enumerate(a.retrieved, start=1):
        c = s.chunk
        where = c.article or (f"p.{c.page}" if c.page is not None else "")
        print(f"  [{n}] {s.score:.4f} {c.doc_id} {where} ({c.access_level})")
    if a.llm is not None:
        raw = a.llm.text.replace("\n", " ")
        print(
            f"LLM: {a.llm.model} in={a.llm.input_tokens} out={a.llm.output_tokens} "
            f"{a.llm.latency_ms / 1000:.1f}s stop={a.llm.stop_reason}"
        )
        if raw.strip() != a.text:
            print(f"LLM 원문: {raw}")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔에서 한글이 깨지지 않게
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("questions", nargs="*", help="질문 (없으면 기본 질문 3개)")
    parser.add_argument("--workspace", default="bank")
    parser.add_argument("--role", default="상담원")
    parser.add_argument("--retriever", choices=["hybrid", "bm25"], default="hybrid")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    store = WorkspaceStore(settings.data_dir / "workspaces")
    try:
        workspace = store.get(args.workspace)
    except WorkspaceNotFoundError:
        if args.workspace != DEFAULT_BANK.id:
            raise
        workspace = DEFAULT_BANK

    start = time.perf_counter()
    chunks = load_chunks(workspace.id, store.path(workspace.id) / "raw")
    retriever = build_retriever(args.retriever, chunks, settings)
    print(
        f"색인: {len(chunks)}청크, retriever={args.retriever}, k={args.k}, "
        f"role={args.role} {workspace.roles.get(args.role)}, llm_mode={settings.llm_mode} "
        f"({time.perf_counter() - start:.1f}s)"
    )

    llm = get_llm(settings)
    for question in args.questions or DEFAULT_QUESTIONS:
        show(question, answer(question, retriever, llm, workspace, args.role, k=args.k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
