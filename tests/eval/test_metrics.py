"""검색·답변 지표: matches, Recall@k, 역순위(RR), 인용 정밀도, 거절 정확도."""

import pytest

from axbuilder.eval.dataset import Evidence
from axbuilder.eval.metrics import (
    citation_precision,
    matches,
    recall_at_k,
    reciprocal_rank,
    refusal_correct,
)
from axbuilder.models import Chunk, Citation


def chunk(doc_id: str, index: int, article: str | None = None, page: int | None = None) -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}::{index:04d}",
        doc_id=doc_id,
        workspace_id="bank",
        title=doc_id,
        text="본문",
        page=page,
        article=article,
    )


def cite(c: Chunk) -> Citation:
    return Citation(
        chunk_id=c.chunk_id, doc_id=c.doc_id, title=c.title, page=c.page, article=c.article
    )


x1 = chunk("banking-act", 1, article="제1조", page=1)
x2 = chunk("banking-act", 2, article="제2조", page=1)
x3 = chunk("prod-fixed-deposit", 0, page=2)
chunk_art1 = x1

ev_x1 = Evidence(doc_id="banking-act", article="제1조")
ev_x2 = Evidence(doc_id="banking-act", article="제2조")
ev_x3 = Evidence(doc_id="prod-fixed-deposit")  # 문서 단위 근거
ev_missing = Evidence(doc_id="depositor-protection-act", article="제32조")


# --- 계획의 테스트 (완료 조건 명세) ---


def test_recall_at_k():
    assert recall_at_k([x1, x2, x3], [ev_x2, ev_missing], k=2) == 0.5


def test_reciprocal_rank():
    assert reciprocal_rank([x1, x2, x3], [ev_x3]) == pytest.approx(1 / 3)
    assert reciprocal_rank([x1], [ev_missing]) == 0.0


def test_matches_requires_article_when_given():
    assert not matches(chunk_art1, Evidence(doc_id=chunk_art1.doc_id, article="제2조"))


def test_citation_precision_none_without_citations():
    assert citation_precision([], [ev_x1]) is None


def test_refusal_correct():
    assert refusal_correct(True, "out_of_scope") and not refusal_correct(True, "lookup")


# --- matches: Task 8 청크 라벨 대조와 같은 규칙 ---


def test_matches_document_level_evidence_accepts_any_chunk_of_the_document():
    assert matches(x1, Evidence(doc_id="banking-act"))
    assert matches(x2, Evidence(doc_id="banking-act"))
    assert not matches(x3, Evidence(doc_id="banking-act"))


def test_matches_requires_same_document_even_with_same_article():
    assert not matches(x1, Evidence(doc_id="depositor-protection-act", article="제1조"))


def test_matches_requires_page_when_given():
    assert matches(x3, Evidence(doc_id="prod-fixed-deposit", page=2))
    assert not matches(x3, Evidence(doc_id="prod-fixed-deposit", page=3))
    assert not matches(x1, Evidence(doc_id="banking-act", article="제1조", page=2))


def test_matches_addenda_article_is_not_the_main_article():
    addenda = chunk("banking-act", 9, article="부칙 제1조")
    assert not matches(addenda, ev_x1)


# --- recall_at_k ---


def test_recall_counts_each_evidence_once():
    # 문서 단위 근거 하나를 청크 3개가 맞혀도 근거 1개 중 1개 = 1.0 (1을 넘지 않는다)
    assert recall_at_k([x1, x2, x3], [Evidence(doc_id="banking-act")], k=3) == 1.0


def test_recall_only_looks_at_top_k():
    assert recall_at_k([x1, x2, x3], [ev_x3], k=2) == 0.0
    assert recall_at_k([x1, x2, x3], [ev_x3], k=3) == 1.0
    assert recall_at_k([x1], [ev_x1], k=10) == 1.0  # 결과가 k보다 적어도 된다
    assert recall_at_k([x1], [ev_x1], k=0) == 0.0


def test_recall_multi_evidence():
    assert recall_at_k([x1, x3], [ev_x1, ev_x2, ev_x3], k=5) == pytest.approx(2 / 3)


def test_recall_without_evidence_is_undefined():
    with pytest.raises(ValueError):
        recall_at_k([x1], [], k=5)


# --- reciprocal_rank ---


def test_reciprocal_rank_uses_first_chunk_matching_any_evidence():
    assert reciprocal_rank([x3, x1, x2], [ev_x2, ev_x1]) == 0.5
    assert reciprocal_rank([x1, x2], [ev_x1]) == 1.0
    assert reciprocal_rank([], [ev_x1]) == 0.0


# --- citation_precision ---


def test_citation_precision_is_fraction_of_citations_matching_evidence():
    assert citation_precision([cite(x1), cite(x2)], [ev_x1]) == 0.5
    assert citation_precision([cite(x1), cite(x3)], [ev_x1, ev_x3]) == 1.0


def test_citation_precision_uses_the_same_rule_as_matches():
    assert citation_precision([cite(x2)], [Evidence(doc_id="banking-act")]) == 1.0
    assert citation_precision([cite(x2)], [ev_x1]) == 0.0


def test_citation_precision_with_citations_but_no_evidence_is_zero():
    # 범위 밖 질문(근거 없음)에 인용을 달아 답했다 = 맞는 인용이 하나도 없다
    assert citation_precision([cite(x1)], []) == 0.0


# --- refusal_correct ---


@pytest.mark.parametrize(
    ("refused", "qtype", "expected"),
    [
        (True, "out_of_scope", True),
        (False, "out_of_scope", False),
        (True, "lookup", False),
        (False, "lookup", True),
        (False, "multi_doc", True),
    ],
)
def test_refusal_correct_table(refused, qtype, expected):
    assert refusal_correct(refused, qtype) is expected
