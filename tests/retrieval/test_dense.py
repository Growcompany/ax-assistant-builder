import hashlib
import math
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

import axbuilder
import axbuilder.retrieval.dense as dense_module
from axbuilder.models import Chunk
from axbuilder.retrieval.dense import (
    MAX_BATCH_CHARS,
    DenseIndex,
    HashEmbedder,
    SentenceTransformerEmbedder,
)
from axbuilder.settings import Settings


def chunk(index: int, text: str, doc_id: str = "bank-guide") -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}::{index:04d}",
        doc_id=doc_id,
        workspace_id="bank",
        title="테스트 문서",
        text=text,
    )


# pytest는 pyproject의 pythonpath=["src"]로 이 작업 폴더의 src를 import한다. 서브프로세스는 그
# 설정을 물려받지 않아서, 그냥 실행하면 editable 설치가 가리키는 폴더(다른 worktree일 수 있다)를
# import한다. 그래서 지금 import한 src를 PYTHONPATH로 넘긴다
SRC = Path(axbuilder.__file__).resolve().parents[1]


def run_python(code: str, **env: str) -> str:
    """이 작업 폴더의 src를 import하는 새 파이썬 프로세스로 code를 실행하고 stdout을 돌려준다."""
    pythonpath = os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": pythonpath, "PYTHONIOENCODING": "utf-8", **env},
        check=True,
    ).stdout


# BM25 테스트와 같은 청크
c1 = chunk(0, "예금자 보호 한도는 1억원이다")
c2 = chunk(1, "전자금융거래 접근매체 관리")
c3 = chunk(2, "예금 금리 우대 조건")


class SpyEmbedder:
    """embed / embed_query 호출을 기록하고 실제 계산은 감싼 임베더에 맡긴다."""

    def __init__(self, inner: HashEmbedder) -> None:
        self.inner = inner
        self.dim = inner.dim
        self.embed_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return self.inner.embed(texts)

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self.inner.embed_query(text)


def test_search_uses_embed_query():
    spy = SpyEmbedder(HashEmbedder())  # embed / embed_query 호출을 기록
    idx = DenseIndex(spy, ":memory:", "t")
    idx.build([c1])
    idx.search("예금", k=1)
    assert spy.query_calls == ["예금"] and all("예금" not in batch for batch in spy.embed_calls)


def test_dense_returns_most_similar_first():
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([c1, c2, c3])
    assert idx.search("예금자 보호 한도", k=1)[0].chunk.chunk_id == c1.chunk_id


def test_dense_access_filter():
    # BM25와 같은 시나리오: internal 청크는 public 필터에서 제외
    internal = c3.model_copy(update={"access_level": "internal"})
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([c1, c2, internal])
    ids = [r.chunk.chunk_id for r in idx.search("예금", k=3, access_levels=["public"])]
    assert internal.chunk_id not in ids


def test_build_is_idempotent():
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([c1, c2, c3])
    idx.build([c1, c2, c3])
    assert len(idx.search("예금", k=10)) == 3


# ---- 계획 외 추가 테스트 ----


def test_hash_embed_query_equals_embed():
    emb = HashEmbedder()
    assert emb.embed_query("예금자 보호") == emb.embed(["예금자 보호"])[0]


def test_hash_embedder_is_normalized_and_deterministic():
    emb = HashEmbedder(dim=32)
    [vec] = emb.embed(["전자금융거래 접근매체"])
    assert len(vec) == 32 == emb.dim
    assert math.sqrt(sum(x * x for x in vec)) == pytest.approx(1.0)
    # 파이썬 hash()는 프로세스마다 값이 바뀐다. 다른 프로세스에서도 같은 벡터여야 한다
    code = (
        "from axbuilder.retrieval.dense import HashEmbedder;"
        "print(HashEmbedder(dim=32).embed(['전자금융거래 접근매체'])[0])"
    )
    out = run_python(code, PYTHONHASHSEED="123")
    assert [float(x) for x in out.strip("[]\n").split(",")] == pytest.approx(vec)


def test_hash_embedder_handles_short_and_empty_text():
    emb = HashEmbedder()
    one_char, empty = emb.embed(["은", ""])
    assert math.sqrt(sum(x * x for x in one_char)) == pytest.approx(1.0)
    assert all(x == 0.0 for x in empty)  # 0으로 나누지 않는다


def test_hash_embedder_ignores_spacing():
    # 띄어쓰기만 다른 표현은 같은 벡터 (한국어는 띄어쓰기가 자주 틀린다)
    emb = HashEmbedder()
    assert emb.embed_query("예금자보호") == emb.embed_query("예금자 보호")


def test_search_returns_chunk_with_all_fields():
    full = Chunk(
        chunk_id="law-1::0003",
        doc_id="law-1",
        workspace_id="bank",
        title="예금자보호법",
        text="제2조의2(보호한도) 보호한도는 대통령령으로 정한다.",
        page=3,
        article="제2조의2",
        access_level="internal",
    )
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([full])
    [result] = idx.search("보호한도", k=1)
    assert result.chunk == full
    assert -1.0 <= result.score <= 1.0


def test_score_is_cosine_similarity():
    emb = HashEmbedder()
    idx = DenseIndex(emb, ":memory:", "t")
    idx.build([c1, c2, c3])
    query = emb.embed_query("예금 금리")
    for result in idx.search("예금 금리", k=3):
        [doc] = emb.embed([result.chunk.text])
        assert result.score == pytest.approx(sum(q * d for q, d in zip(query, doc, strict=True)))


def test_results_sorted_by_score_and_limited_by_k():
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([c1, c2, c3])
    results = idx.search("예금 금리 우대", k=2)
    assert len(results) == 2
    assert results[0].score >= results[1].score
    assert idx.search("예금", k=0) == []


def test_returns_nearest_even_without_shared_characters():
    # dense는 점수 하한을 두지 않는다: "은?"처럼 BM25가 비는 질문도 결과가 나와야 한다 (Task 5)
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([c1, c2, c3])
    assert len(idx.search("은?", k=3)) == 3


def test_access_filter_applies_before_top_k():
    # 권한 밖 청크가 1위여도 k=1에서 공개 청크가 나와야 한다 (필터 → top-k)
    internal = chunk(0, "예금 금리 우대 조건", doc_id="internal-rule").model_copy(
        update={"access_level": "internal"}
    )
    public = chunk(0, "전자금융거래 접근매체 관리")
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([internal, public])
    assert idx.search("예금 금리 우대 조건", k=1)[0].chunk == internal
    assert [r.chunk for r in idx.search("예금 금리 우대 조건", k=1, access_levels=["public"])] == [
        public
    ]


def test_empty_access_levels_returns_nothing():
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([c1, c2, c3])
    assert idx.search("예금", k=3, access_levels=[]) == []


def test_build_replaces_previous_index():
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([c1, c2, c3])
    idx.build([c2])
    assert [r.chunk for r in idx.search("예금", k=10)] == [c2]


def test_search_on_empty_index():
    assert DenseIndex(HashEmbedder(), ":memory:", "t").search("예금", k=3) == []
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build([])
    assert idx.search("예금", k=3) == []


def test_persists_to_folder(tmp_path):
    # 로컬 모드(path=)는 폴더에 저장된다.
    # 같은 폴더는 한 번에 클라이언트 하나만 열 수 있어 먼저 닫는다
    idx = DenseIndex(HashEmbedder(), tmp_path / "qdrant", "bank")
    idx.build([c1, c2, c3])
    idx.close()
    reopened = DenseIndex(HashEmbedder(), tmp_path / "qdrant", "bank")
    try:
        assert reopened.search("예금자 보호 한도", k=1)[0].chunk == c1
    finally:
        reopened.close()


def test_chunks_returns_every_indexed_chunk(tmp_path):
    # 서버를 다시 켜면 BM25(메모리)는 사라진다. 폴더에 남은 색인에서 청크를 꺼내 다시 만든다
    many = [chunk(i, f"예금 상품 {i}번 설명") for i in range(300)]  # scroll 한 페이지보다 많게
    idx = DenseIndex(HashEmbedder(), tmp_path / "qdrant", "bank")
    idx.build(many)
    idx.close()
    reopened = DenseIndex(HashEmbedder(), tmp_path / "qdrant", "bank")
    try:
        assert sorted(reopened.chunks(), key=lambda c: c.chunk_id) == many
    finally:
        reopened.close()


def test_rebuild_on_folder_drops_old_points(tmp_path):
    # Windows에서 qdrant-client(1.20) 로컬 모드의 delete_collection은 SQLite 파일을 닫지 않고
    # 지우려다 조용히 실패한다. 같은 이름으로 다시 만들면 옛 점이 되살아났다(재색인 버그)
    many = [chunk(i, f"예금 {i}") for i in range(3)]
    idx = DenseIndex(HashEmbedder(), tmp_path / "qdrant", "bank")
    idx.build(many)
    idx.build(many[:1])
    assert idx.chunks() == many[:1]
    idx.close()
    reopened = DenseIndex(HashEmbedder(), tmp_path / "qdrant", "bank")
    try:
        assert reopened.chunks() == many[:1]
        reopened.build(many[1:])
        assert sorted(reopened.chunks(), key=lambda c: c.chunk_id) == many[1:]
    finally:
        reopened.close()


def test_rebuild_on_folder_with_new_dimension(tmp_path):
    # 임베딩 모델을 바꿔 차원이 달라져도 같은 폴더에 다시 색인할 수 있다
    idx = DenseIndex(HashEmbedder(dim=64), tmp_path / "qdrant", "bank")
    idx.build([c1, c2, c3])
    idx.close()
    idx = DenseIndex(HashEmbedder(dim=32), tmp_path / "qdrant", "bank")
    try:
        idx.build([c1, c2])
        assert len(idx.search("예금", k=10)) == 2
    finally:
        idx.close()


def test_chunks_of_missing_or_empty_collection():
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    assert idx.chunks() == []
    idx.build([])
    assert idx.chunks() == []


# ---- SentenceTransformerEmbedder: 실제 모델 없이 호출 방식만 확인 ----


class FakeSentenceTransformer:
    """sentence_transformers.SentenceTransformer 대역. encode 호출 인자를 기록한다."""

    prompts_for_new: dict[str, str] = {}
    instances: list["FakeSentenceTransformer"] = []

    def __init__(self, model_name: str, device: str | None = None) -> None:
        self.model_name = model_name
        self.device = device
        self.prompts = dict(FakeSentenceTransformer.prompts_for_new)
        self.calls: list[tuple[list[str], dict]] = []
        FakeSentenceTransformer.instances.append(self)

    def get_embedding_dimension(self) -> int:
        return 2

    def encode(self, texts, **kwargs):
        import numpy as np

        self.calls.append((list(texts), kwargs))
        # 글자 수를 첫 성분에 넣어 결과가 어느 입력의 것인지 알 수 있게 한다
        return np.array([[float(len(t)), 1.0] for t in texts], dtype=np.float32)


@pytest.fixture
def fake_st(monkeypatch):
    # snowflake-arctic-embed-l-v2.0-ko의 config_sentence_transformers.json과 같은 프롬프트
    monkeypatch.setattr(FakeSentenceTransformer, "prompts_for_new", {"query": "query: "})
    monkeypatch.setattr(FakeSentenceTransformer, "instances", [])
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return FakeSentenceTransformer


def test_st_embedder_uses_query_prompt_and_normalizes(fake_st):
    emb = SentenceTransformerEmbedder("some/model")
    [model] = fake_st.instances
    assert (model.model_name, model.device, emb.dim) == ("some/model", "cuda", 2)

    assert emb.embed(["문서1", "문서2"]) == [[3.0, 1.0], [3.0, 1.0]]
    assert emb.embed_query("질문") == [2.0, 1.0]
    (doc_texts, doc_kwargs), (query_texts, query_kwargs) = model.calls
    assert doc_texts == ["문서1", "문서2"] and query_texts == ["질문"]
    assert doc_kwargs.get("prompt_name") is None and doc_kwargs["normalize_embeddings"] is True
    assert query_kwargs["prompt_name"] == "query" and query_kwargs["normalize_embeddings"] is True


def test_st_embedder_without_query_prompt(fake_st, monkeypatch):
    # 질문 프롬프트가 없는 모델(예: bge-m3)에 prompt_name="query"를 넘기면 오류가 난다
    monkeypatch.setattr(fake_st, "prompts_for_new", {})
    emb = SentenceTransformerEmbedder("plain/model", device="cpu")
    emb.embed_query("질문")
    [model] = fake_st.instances
    [(_, kwargs)] = model.calls
    assert model.device == "cpu" and kwargs.get("prompt_name") is None


def test_st_embedder_accepts_old_dimension_method_name(fake_st, monkeypatch):
    # 옛 sentence-transformers에는 get_sentence_embedding_dimension만 있다
    monkeypatch.delattr(fake_st, "get_embedding_dimension")
    monkeypatch.setattr(fake_st, "get_sentence_embedding_dimension", lambda self: 3, raising=False)
    assert SentenceTransformerEmbedder("old/model").dim == 3


def test_st_embedder_prefers_new_dimension_method_name(fake_st, monkeypatch):
    # 설치본(6.2.0)에서 옛 이름은 FutureWarning을 낸다. 새 이름이 있으면 새 이름만 쓴다
    def old_name(self) -> int:
        raise AssertionError("deprecated get_sentence_embedding_dimension was called")

    monkeypatch.setattr(fake_st, "get_sentence_embedding_dimension", old_name, raising=False)
    assert SentenceTransformerEmbedder("new/model").dim == 2


def test_st_embedder_empty_batch_skips_model(fake_st):
    emb = SentenceTransformerEmbedder("some/model")
    assert emb.embed([]) == []
    assert fake_st.instances[0].calls == []


def test_st_embedder_splits_long_texts_and_keeps_order(fake_st):
    # 긴 청크를 여러 개 묶으면 GPU 메모리가 넘친다: 배치 크기 × 최장 글자 수 <= MAX_BATCH_CHARS
    emb = SentenceTransformerEmbedder("some/model")
    longest = "가" * MAX_BATCH_CHARS
    quarter = "나" * (MAX_BATCH_CHARS // 4)
    texts = ["짧은 글", longest, quarter, "다", quarter]
    assert [v[0] for v in emb.embed(texts)] == [float(len(t)) for t in texts]  # 입력 순서 유지

    [model] = fake_st.instances
    batches = [batch for batch, _ in model.calls]
    assert batches == [[longest], [quarter, quarter, "짧은 글", "다"]]
    assert [kwargs["batch_size"] for _, kwargs in model.calls] == [1, 4]


def test_st_embedder_caps_batch_size_for_short_texts(fake_st):
    emb = SentenceTransformerEmbedder("some/model")
    assert len(emb.embed(["예금"] * 70)) == 70
    assert [len(batch) for batch, _ in fake_st.instances[0].calls] == [32, 32, 6]


def test_dense_module_does_not_import_sentence_transformers():
    # CI는 ml extra 없이 돈다: 모듈 import만으로 torch를 끌어오면 안 된다
    code = (
        "import sys, axbuilder.retrieval.dense;"
        "assert 'sentence_transformers' not in sys.modules;"
        "assert 'torch' not in sys.modules"
    )
    run_python(code)


def test_subprocess_imports_this_checkout():
    # 위 두 테스트가 다른 체크아웃의 코드를 검사하지 않는지 (리뷰를 worktree에서 돌릴 때)
    out = run_python("import axbuilder; print(axbuilder.__file__)")
    assert Path(out.strip()).resolve().parents[1] == SRC


# ---- 실제 모델 (pytest -m slow) ----


@pytest.mark.slow
def test_real_model_korean_similarity():
    idx = DenseIndex(SentenceTransformerEmbedder(Settings.from_env().embed_model), ":memory:", "t")
    idx.build([c1, c2, c3])
    assert idx.search("예금 보험 보장 금액", k=1)[0].chunk.chunk_id == c1.chunk_id


@pytest.mark.slow
def test_real_model_timing(capsys):
    # 로딩·임베딩 시간을 PR에 기록하기 위한 측정 (assert는 느슨하게)
    import torch

    start = time.perf_counter()
    emb = SentenceTransformerEmbedder(Settings.from_env().embed_model)
    load_s = time.perf_counter() - start

    texts = [
        f"제{i}조(예시) 예금자 보호 한도와 금리 우대 조건을 설명한다. {i}" * 5 for i in range(100)
    ]
    emb.embed(texts[:2])  # 첫 호출의 CUDA 초기화 비용은 빼고 잰다
    start = time.perf_counter()
    vectors = emb.embed(texts)
    embed_s = time.perf_counter() - start

    assert len(vectors) == 100 and len(vectors[0]) == emb.dim
    with capsys.disabled():
        print(
            f"\n[timing] cuda={torch.cuda.is_available()} dim={emb.dim} "
            f"load={load_s:.1f}s embed100={embed_s:.2f}s"
        )


# ---- 전체 코드 감사: 동점 순서, 재색인 실패 ----


def tied_chunks(count: int) -> list[Chunk]:
    """본문이 같은 청크(벡터·점수가 같다). 실제 코퍼스에도 "제2조 생략" 같은 조항이 54개 있다."""
    return [chunk(0, "제2조 생략", doc_id=f"law-{i:02d}") for i in range(count)]


def search_ids(folder: Path, query: str, k: int, **kwargs) -> list[str]:
    idx = DenseIndex(HashEmbedder(), folder, "bank")
    try:
        return [r.chunk.chunk_id for r in idx.search(query, k=k, **kwargs)]
    finally:
        idx.close()


@pytest.mark.parametrize("k", [1, 5, 12, 13, 30])
def test_tied_scores_are_ordered_by_chunk_id_in_every_session(tmp_path, k):
    # Qdrant 로컬 모드는 동점을 저장 순서대로 돌려주는데, 그 순서가 색인한 세션과 다시 연 세션에서
    # 다르다. 재시작 전후로 같은 질문의 상위 결과와 인용이 바뀌었다. k가 동점 묶음 중간을 자를 때
    # (k=1, 5)도 어느 것이 남는지 정해져 있어야 한다
    tied = tied_chunks(12)
    others = [chunk(i, f"전자금융거래 접근매체 관리 {i}") for i in range(5)]
    mixed = [tied[7], others[0], tied[2], *tied[8:], others[1], *tied[:2], *others[2:], *tied[3:7]]
    folder = tmp_path / "qdrant"
    idx = DenseIndex(HashEmbedder(), folder, "bank")
    idx.build(mixed)
    built = idx.search("제2조 생략", k=k)
    idx.close()

    expected = [c.chunk_id for c in tied][:k]
    assert [r.chunk.chunk_id for r in built][:12] == expected
    assert len(built) == min(k, 17)
    assert [r.score for r in built] == sorted((r.score for r in built), reverse=True)
    for _ in range(2):  # 다시 연 세션
        assert search_ids(folder, "제2조 생략", k)[:12] == expected


def test_tie_at_the_cut_is_resolved_among_all_tied_chunks(tmp_path):
    # 동점 묶음이 k보다 훨씬 커도(받아 온 수를 넘어도) 묶음 전체에서 chunk_id가 앞선 것이 남는다
    tied = tied_chunks(200)
    folder = tmp_path / "qdrant"
    idx = DenseIndex(HashEmbedder(), folder, "bank")
    idx.build(list(reversed(tied)) + [c1])
    first = [r.chunk.chunk_id for r in idx.search("제2조 생략", k=3)]
    idx.close()
    assert first == [c.chunk_id for c in tied[:3]]
    assert search_ids(folder, "제2조 생략", 3) == first


def test_tie_order_respects_access_filter(tmp_path):
    tied = [
        c.model_copy(update={"access_level": "internal" if i % 2 else "public"})
        for i, c in enumerate(tied_chunks(10))
    ]
    folder = tmp_path / "qdrant"
    idx = DenseIndex(HashEmbedder(), folder, "bank")
    idx.build(list(reversed(tied)))
    idx.close()
    internal = [c.chunk_id for c in tied if c.access_level == "internal"]
    assert search_ids(folder, "제2조 생략", 3, access_levels=["internal"]) == internal[:3]


class FailingEmbedder(HashEmbedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("CUDA out of memory")


def test_before_replace_runs_after_embedding_and_before_the_store_changes(tmp_path):
    # API는 이 시점에 index.json을 지운다: 임베딩이 실패하면 옛 색인이 그대로 쓸 수 있는 상태로
    # 남고, 저장소를 바꾸기 시작한 뒤에 실패하면 "색인 없음"으로 보인다
    spy = SpyEmbedder(HashEmbedder())
    idx = DenseIndex(spy, ":memory:", "t")
    idx.build([c1, c2])
    seen = []
    idx.build([c3], before_replace=lambda: seen.append((len(spy.embed_calls), idx.chunks())))
    assert len(seen) == 1
    embed_calls, stored = seen[0]
    assert embed_calls == 2  # 새 청크의 임베딩이 끝난 뒤
    assert sorted(stored, key=lambda c: c.chunk_id) == [c1, c2]  # 옛 점은 아직 그대로
    assert idx.chunks() == [c3]


def test_failed_embedding_keeps_the_old_index_and_skips_before_replace(tmp_path):
    folder = tmp_path / "qdrant"
    idx = DenseIndex(HashEmbedder(), folder, "bank")
    idx.build([c1, c2])
    idx.close()
    failing = DenseIndex(FailingEmbedder(), folder, "bank")
    called = []
    try:
        with pytest.raises(RuntimeError, match="out of memory"):
            failing.build([c3], before_replace=lambda: called.append(1))
        assert called == []
        assert sorted(failing.chunks(), key=lambda c: c.chunk_id) == [c1, c2]
    finally:
        failing.close()


# ---- PR #19 리뷰: 실제 임베딩처럼 촘촘한 벡터에서는 같은 벡터의 점수도 마지막 자리가 갈린다 ----


class DenseRandomEmbedder:
    """실제 모델과 같은 모양의 벡터: 촘촘한 float32 난수를 L2 정규화한다(dim 1024).

    본문이 같으면 비트까지 같은 벡터를 낸다. HashEmbedder의 벡터는 성분이 몇 개뿐이라 같은 벡터의
    점수가 갈리지 않아서, 동점 처리가 실제로 동작하는지 보여 주지 못했다.
    """

    dim = 1024

    def _vector(self, text: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big")
        vector = np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)
        return vector / np.linalg.norm(vector)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text).tolist() for text in texts]

    def embed_query(self, text: str) -> list[float]:
        # 질문 벡터가 "제2조 생략" 묶음에 가깝게: 묶음이 상위에 오고 k가 그 가운데를 자른다
        vector = 0.7 * self._vector("제2조 생략") + 0.3 * self._vector(text)
        return (vector / np.linalg.norm(vector)).tolist()


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_identical_dense_vectors_rank_the_same_in_every_session(tmp_path, seed):
    # 청크 945개 가운데 본문이 같은 것 54개(실제 은행 코퍼스와 같은 수). Qdrant 로컬 모드는 비트까지
    # 같은 벡터에도 저장 위치에 따라 마지막 자리가 다른 점수를 준다(…5487 / …5482). 점수를 ==로
    # 비교하면 동점으로 묶이지 않아, 색인한 세션과 다시 연 세션의 순서와 구성원이 달랐다
    embedder = DenseRandomEmbedder()
    assert embedder.embed(["제2조 생략"]) == embedder.embed(["제2조 생략"])
    tied = tied_chunks(54)
    others = [chunk(i, f"전자금융거래 접근매체 관리 {i}", doc_id="other") for i in range(891)]
    mixed = tied + others
    order = np.random.default_rng(seed).permutation(len(mixed))
    folder = tmp_path / "qdrant"
    sessions: dict[int, list[list[str]]] = {5: [], 30: [], 60: []}
    for session in range(3):
        idx = DenseIndex(embedder, folder, "bank")
        try:
            if session == 0:
                idx.build([mixed[i] for i in order])
            for k, results in sessions.items():
                results.append([r.chunk.chunk_id for r in idx.search("질문", k=k)])
        finally:
            idx.close()
    tied_ids = [c.chunk_id for c in tied]
    for k, results in sessions.items():
        assert results[0][:54] == tied_ids[:k], f"k={k}: indexing session"
        assert results[1] == results[0] and results[2] == results[0], f"k={k}: reopened sessions"
        assert len(results[0]) == k


def test_tie_groups_are_anchored_to_their_highest_score():
    # 묶음의 가장 높은 점수에서 허용 오차 안에 있는 점만 같은 묶음이다. a≈b, b≈c라도 c가 a에서
    # 오차 밖이면 c는 다음 묶음을 시작한다(연쇄로 묶음이 끝없이 길어지지 않는다)
    tolerance = dense_module.TIE_TOLERANCE
    assert tolerance == 1e-6
    a, b, c = 0.9, 0.9 - 0.6 * tolerance, 0.9 - 1.2 * tolerance
    assert dense_module._tie_groups([a, b, c]) == [0, 0, 1]
    assert dense_module._tie_groups([a, a - 1e-9, b, c, c - 0.5 * tolerance, 0.5]) == [
        0,
        0,
        0,
        1,
        1,
        2,
    ]
    assert dense_module._tie_groups([]) == []
    assert dense_module._tie_groups([0.5]) == [0]


class FixedScores:
    """질문과의 코사인 유사도가 정해진 값이 되는 2차원 벡터를 내는 임베더."""

    dim = 2

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[self.scores[t], math.sqrt(1 - self.scores[t] ** 2)] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


def test_scores_further_apart_than_the_tolerance_keep_score_order():
    # 허용 오차보다 크게 다른 점수는 chunk_id와 상관없이 점수 순서다. 오차 안이면 chunk_id 순서다
    scores = {"z": 0.9, "y": 0.9 - 1e-4, "x": 0.9 - 2e-4, "w": 0.9 - 2e-4 - 1e-8}
    idx = DenseIndex(FixedScores(scores), ":memory:", "t")
    # chunk_id 순서: w(0) < x(1) < y(2) < z(3)
    idx.build([chunk(i, text) for i, text in enumerate(["w", "x", "y", "z"])])
    assert [r.chunk.text for r in idx.search("q", k=4)] == ["z", "y", "w", "x"]
    assert [r.chunk.text for r in idx.search("q", k=3)] == ["z", "y", "w"]  # 경계의 묶음에서 w


def test_boundary_tie_needs_at_most_two_queries(tmp_path, monkeypatch):
    # 전부 동점이어도 질의는 두 번이다(k+1개를 받고, 잘리는 묶음이 이어지면 그 묶음까지 한 번 더)
    idx = DenseIndex(HashEmbedder(), ":memory:", "t")
    idx.build(tied_chunks(500) + [c1, c2, c3])
    calls = []
    original = idx._client.query_points

    def counting(*args, **kwargs):
        calls.append(kwargs.get("limit"))
        return original(*args, **kwargs)

    monkeypatch.setattr(idx._client, "query_points", counting)
    expected = sorted(c.chunk_id for c in tied_chunks(500))[:30]
    assert [r.chunk.chunk_id for r in idx.search("제2조 생략", k=30)] == expected
    assert len(calls) == 2
    calls.clear()
    assert idx.search("예금자 보호 한도", k=1)[0].chunk == c1  # 동점이 없으면 한 번
    assert len(calls) == 1
