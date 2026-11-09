import pytest

from axbuilder.models import Chunk, ScoredChunk
from axbuilder.retrieval.bm25 import BM25Index
from axbuilder.retrieval.dense import DenseIndex, HashEmbedder
from axbuilder.retrieval.hybrid import HybridRetriever, Retriever, rrf_fuse


def chunk(index: int, text: str, doc_id: str = "bank-guide") -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}::{index:04d}",
        doc_id=doc_id,
        workspace_id="bank",
        title="테스트 문서",
        text=text,
    )


def scored(c: Chunk, score: float = 1.0) -> ScoredChunk:
    return ScoredChunk(chunk=c, score=score)


# BM25·dense 테스트와 같은 청크
c1 = chunk(0, "예금자 보호 한도는 1억원이다")
c2 = chunk(1, "전자금융거래 접근매체 관리")
c3 = chunk(2, "예금 금리 우대 조건")

# rrf_fuse는 순위만 보므로 점수는 아무 값이나 넣는다
a, b, c = scored(c1, 9.0), scored(c2, 5.0), scored(c3, 1.0)


def ids(results: list[ScoredChunk]) -> list[str]:
    return [r.chunk.chunk_id for r in results]


@pytest.fixture
def hybrid() -> HybridRetriever:
    bm25 = BM25Index()
    bm25.build([c1, c2, c3])
    dense = DenseIndex(HashEmbedder(), ":memory:", "t")
    dense.build([c1, c2, c3])
    return HybridRetriever(bm25, dense)


def test_rrf_order():
    fused = rrf_fuse([[a, b, c], [b, c, a]], k=60)
    assert [s.chunk.chunk_id for s in fused] == [
        b.chunk.chunk_id,
        a.chunk.chunk_id,
        c.chunk.chunk_id,
    ]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)


def test_rrf_includes_items_from_one_list_only():
    assert len(rrf_fuse([[a], [b]], k=60)) == 2


def test_hybrid_works_when_bm25_empty(hybrid):
    # "은?" 처럼 BM25가 빈 결과여도 dense 결과로 동작
    assert hybrid.bm25.search("은?", k=3) == []
    assert len(hybrid.search("은?", k=3)) > 0


# ---- 계획 외 추가 테스트: rrf_fuse ----


def test_rrf_scores_follow_formula():
    # a: 1위·3위, b: 2위·1위, c: 3위·2위 (순위는 1부터)
    fused = {s.chunk.chunk_id: s.score for s in rrf_fuse([[a, b, c], [b, c, a]], k=60)}
    assert fused[a.chunk.chunk_id] == pytest.approx(1 / 61 + 1 / 63)
    assert fused[c.chunk.chunk_id] == pytest.approx(1 / 63 + 1 / 62)


def test_rrf_item_in_one_list_scores_single_term():
    [only_a, only_b] = rrf_fuse([[a], [b]], k=60)
    assert only_a.score == pytest.approx(1 / 61) and only_b.score == pytest.approx(1 / 61)


def test_rrf_ignores_raw_scores():
    # BM25 점수(수십)와 코사인(-1~1)은 척도가 달라도 순위만 같으면 결과가 같다
    bm25_like = [scored(c1, 30.0), scored(c2, 12.0)]
    cosine_like = [scored(c2, 0.83), scored(c3, 0.41)]
    scaled = [scored(c1, 3000.0), scored(c2, 0.001)]
    assert rrf_fuse([bm25_like, cosine_like]) == rrf_fuse([scaled, cosine_like])


def test_rrf_k_controls_how_fast_rank_weight_drops():
    # k가 작으면 1위의 몫이 커서 한쪽 1위가 양쪽 중위권을 이긴다
    # a: 왼쪽 1위, 오른쪽 없음 / b: 양쪽 3위
    d = scored(chunk(3, "다른 청크"))
    e = scored(chunk(4, "또 다른 청크"))
    left, right = [a, d, b], [e, c, b]

    def a_before_b(k: int) -> bool:
        order = ids(rrf_fuse([left, right], k=k))
        return order.index(a.chunk.chunk_id) < order.index(b.chunk.chunk_id)

    assert a_before_b(k=0)  # 1/1 > 1/3 + 1/3
    assert not a_before_b(k=60)  # 1/61 < 1/63 + 1/63


def test_rrf_tie_keeps_first_seen_order():
    # 동점이면 먼저 나온 목록(하이브리드에서는 BM25)의 것이 앞선다 — 실행마다 같은 순서
    assert ids(rrf_fuse([[a], [b]])) == [a.chunk.chunk_id, b.chunk.chunk_id]
    assert ids(rrf_fuse([[b], [a]])) == [b.chunk.chunk_id, a.chunk.chunk_id]


def test_rrf_counts_duplicate_in_one_list_once():
    # 한 목록에 같은 청크가 두 번 있어도 높은 순위 한 번만 센다
    [fused_a, fused_b] = rrf_fuse([[a, a, b]], k=60)
    assert fused_a.chunk == c1 and fused_a.score == pytest.approx(1 / 61)
    assert fused_b.score == pytest.approx(1 / 63)


def test_rrf_top_n_and_empty_inputs():
    assert len(rrf_fuse([[a, b, c], [c, b, a]], top_n=2)) == 2
    assert rrf_fuse([[a, b, c]], top_n=0) == []
    assert rrf_fuse([]) == [] and rrf_fuse([[], []]) == []


def test_rrf_keeps_chunk_fields():
    full = c1.model_copy(update={"page": 3, "article": "제2조의2", "access_level": "internal"})
    [fused] = rrf_fuse([[scored(full)], [scored(full)]])
    assert fused.chunk == full


@pytest.mark.parametrize("bad_k", [-1, -60, float("nan")])
def test_rrf_rejects_invalid_k(bad_k):
    # k = -1이면 1위에서 0으로 나누게 된다
    with pytest.raises(ValueError):
        rrf_fuse([[a]], k=bad_k)


# ---- 계획 외 추가 테스트: HybridRetriever ----


class RecordingRetriever:
    """정해 둔 결과를 돌려주고 search 호출 인자를 기록한다 (BM25Index·DenseIndex 대역)."""

    def __init__(self, results: list[ScoredChunk]) -> None:
        self.results = results
        self.calls: list[tuple[str, int, list[str] | None]] = []

    def search(
        self, query: str, k: int = 10, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]:
        self.calls.append((query, k, access_levels))
        return self.results[:k]


def test_hybrid_fuses_both_rankings_with_rrf():
    bm25 = RecordingRetriever([a, b])
    dense = RecordingRetriever([b, c])
    hybrid = HybridRetriever(bm25, dense)
    results = hybrid.search("예금", k=3)
    assert results == rrf_fuse([[a, b], [b, c]], k=60, top_n=3)
    assert ids(results)[0] == b.chunk.chunk_id  # 양쪽에 다 나온 청크가 1위


def test_hybrid_asks_each_retriever_for_candidates():
    # 각 검색기에서 candidates개를 받아 합친 뒤 k개로 자른다
    bm25 = RecordingRetriever([a, b, c])
    dense = RecordingRetriever([c, b, a])
    hybrid = HybridRetriever(bm25, dense, candidates=30)
    assert len(hybrid.search("예금", k=2, access_levels=["public"])) == 2
    assert bm25.calls == dense.calls == [("예금", 30, ["public"])]


def test_hybrid_fetches_at_least_k_candidates():
    # k가 candidates보다 크면 각 검색기에서 k개씩 받는다 (결과가 candidates에 묶이지 않게)
    bm25 = RecordingRetriever([a, b, c])
    dense = RecordingRetriever([])
    hybrid = HybridRetriever(bm25, dense, candidates=2)
    assert len(hybrid.search("예금", k=3)) == 3
    assert bm25.calls == [("예금", 3, None)]


def test_hybrid_works_when_dense_empty():
    hybrid = HybridRetriever(RecordingRetriever([a, b]), RecordingRetriever([]))
    assert ids(hybrid.search("예금", k=5)) == [a.chunk.chunk_id, b.chunk.chunk_id]


def test_hybrid_k_zero_skips_search():
    bm25, dense = RecordingRetriever([a]), RecordingRetriever([a])
    assert HybridRetriever(bm25, dense).search("예금", k=0) == []
    assert bm25.calls == dense.calls == []


@pytest.mark.parametrize("bad", [0, -1])
def test_hybrid_rejects_non_positive_candidates(bad):
    with pytest.raises(ValueError):
        HybridRetriever(BM25Index(), DenseIndex(HashEmbedder(), ":memory:", "t"), candidates=bad)


def test_hybrid_access_filter():
    # BM25·dense와 같은 시나리오: internal 청크는 public 필터에서 양쪽 모두 제외
    internal = c3.model_copy(update={"access_level": "internal"})
    bm25 = BM25Index()
    bm25.build([c1, c2, internal])
    dense = DenseIndex(HashEmbedder(), ":memory:", "t")
    dense.build([c1, c2, internal])
    hybrid = HybridRetriever(bm25, dense)
    assert internal.chunk_id in ids(hybrid.search("예금 금리", k=3))
    assert internal.chunk_id not in ids(hybrid.search("예금 금리", k=3, access_levels=["public"]))
    # 볼 수 있는 등급이 없으면 빈 결과 (fail closed)
    assert hybrid.search("예금 금리", k=3, access_levels=[]) == []


def test_hybrid_ranks_matching_chunk_first(hybrid):
    assert hybrid.search("예금자 보호 한도", k=3)[0].chunk == c1


def test_all_retrievers_share_search_shape(hybrid):
    # BM25Index, DenseIndex, HybridRetriever는 모두 Retriever 모양으로 쓸 수 있다
    retrievers: list[Retriever] = [hybrid.bm25, hybrid.dense, hybrid]
    for retriever in retrievers:
        results = retriever.search("예금 금리", k=2, access_levels=["public"])
        assert 0 < len(results) <= 2
        assert all(isinstance(r, ScoredChunk) for r in results)
