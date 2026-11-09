import math

import pytest

from axbuilder.models import Chunk
from axbuilder.retrieval.bm25 import BM25Index


def chunk(index: int, text: str, doc_id: str = "bank-guide") -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}::{index:04d}",
        doc_id=doc_id,
        workspace_id="bank",
        title="테스트 문서",
        text=text,
    )


c1 = chunk(0, "예금자 보호 한도는 1억원이다")
c2 = chunk(1, "전자금융거래 접근매체 관리")
c3 = chunk(2, "예금 금리 우대 조건")


def test_ranks_matching_chunk_first():
    idx = BM25Index()
    idx.build([c1, c2, c3])
    assert idx.search("예금자 보호 한도", k=3)[0].chunk.chunk_id == c1.chunk_id


def test_rare_term_scores_higher_than_common_term():
    # "예금"은 c1·c3에, "접근매체"는 c2에만 있다
    idx = BM25Index()
    idx.build([c1, c2, c3])
    assert idx.search("접근매체", k=1)[0].score > idx.search("예금", k=1)[0].score


def test_no_match_returns_empty():
    idx = BM25Index()
    idx.build([c1, c2, c3])
    assert idx.search("자동차보험", k=3) == []


def test_query_without_content_tokens():
    idx = BM25Index()
    idx.build([c1, c2, c3])
    assert idx.search("은?", k=3) == []


def test_access_level_filter():
    internal = c3.model_copy(update={"access_level": "internal"})
    idx = BM25Index()
    idx.build([c1, c2, internal])
    ids = [r.chunk.chunk_id for r in idx.search("예금", k=3, access_levels=["public"])]
    assert internal.chunk_id not in ids


# ---- 계획 외 추가 테스트: IDF·길이 정규화를 문서 길이 영향 없이 직접 확인 ----


def test_idf_prefers_rare_term_at_equal_length():
    # Kiwi는 "예금자"를 한 단어로 보므로 위 c1·c3로는 "흔한 단어" 비교가 안 된다.
    # 길이가 같은 청크에서 3/4에 나오는 "예금"과 1/4에만 나오는 "적금"을 비교한다.
    chunks = [
        chunk(i, t) for i, t in enumerate(["예금 금리", "예금 한도", "예금 이자", "적금 이자"])
    ]
    idx = BM25Index()
    idx.build(chunks)
    assert idx.search("적금", k=1)[0].score > idx.search("예금", k=1)[0].score


def test_score_matches_formula():
    d1, d2 = chunk(0, "예금 금리"), chunk(1, "적금 금리 우대")
    idx = BM25Index(k1=1.5, b=0.75)
    idx.build([d1, d2])
    n_docs, df, tf, dl, avgdl, k1, b = 2, 1, 1, 2, 2.5, 1.5, 0.75
    idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
    expected = idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
    [result] = idx.search("예금", k=5)
    assert result.chunk == d1
    assert result.score == pytest.approx(expected)


def test_b_controls_length_normalization():
    short = chunk(0, "예금 금리")
    long = chunk(1, "예금 금리 우대 조건 안내 절차")
    default = BM25Index()
    default.build([short, long])
    by_id = {r.chunk.chunk_id: r.score for r in default.search("예금", k=2)}
    assert by_id[short.chunk_id] > by_id[long.chunk_id]

    no_norm = BM25Index(b=0.0)
    no_norm.build([short, long])
    s, lg = no_norm.search("예금", k=2)
    assert s.score == pytest.approx(lg.score)


def test_empty_access_levels_returns_nothing():
    idx = BM25Index()
    idx.build([c1, c2, c3])
    assert idx.search("예금", k=3, access_levels=[]) == []


def test_k_limits_results():
    idx = BM25Index()
    idx.build([c1, c2, c3])
    assert len(idx.search("예금자 보호 접근매체 금리", k=2)) == 2
    assert idx.search("예금", k=0) == []


def test_search_on_empty_index():
    assert BM25Index().search("예금", k=3) == []
    idx = BM25Index()
    idx.build([])
    assert idx.search("예금", k=3) == []


def test_chunk_without_tokens_is_ignored():
    idx = BM25Index()
    idx.build([chunk(0, "?!"), c3])
    assert [r.chunk.chunk_id for r in idx.search("예금", k=3)] == [c3.chunk_id]


def test_build_replaces_previous_index():
    idx = BM25Index()
    idx.build([c1, c2, c3])
    idx.build([c2])
    assert idx.search("예금", k=3) == []


@pytest.mark.parametrize("params", [{"k1": -0.1}, {"b": -0.1}, {"b": 1.1}])
def test_rejects_invalid_parameters(params):
    with pytest.raises(ValueError):
        BM25Index(**params)
