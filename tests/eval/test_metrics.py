"""검색·답변 지표: matches, Recall@k, 역순위(RR), 인용 정밀도, 거절 정확도."""

import pytest

from axbuilder.eval.dataset import Evidence
from axbuilder.eval.metrics import (
    citation_precision,
    grounded_refusal_correct,
    matches,
    matches_strict,
    recall_at_k,
    recall_at_k_strict,
    reciprocal_rank,
    reciprocal_rank_strict,
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


# --- grounded_refusal_correct: 근거가 프롬프트에 있었는가로 거절을 채점한다 ---


@pytest.mark.parametrize(
    ("refused", "evidence_in_context", "expected"),
    [
        (False, True, True),  # 근거가 있고 답했다
        (True, True, False),  # 근거가 있는데 거절(과잉 거절)
        (True, False, True),  # 근거가 없어서 거절(범위 밖, 검색 실패)
        (False, False, False),  # 근거가 없는데 답했다(환각 위험)
    ],
)
def test_grounded_refusal_correct_table(refused, evidence_in_context, expected):
    assert grounded_refusal_correct(refused, evidence_in_context) is expected


def test_grounded_and_plain_refusal_metrics_differ_only_on_retrieval_misses():
    # 답해야 하는 문항인데 검색이 근거를 못 가져왔다: 거절하면 기존 지표는 오답, 근거 기준은 정답
    assert not refusal_correct(True, "lookup") and grounded_refusal_correct(True, False)
    assert refusal_correct(False, "lookup") and not grounded_refusal_correct(False, False)
    # 범위 밖(근거 없음)과 근거가 들어온 문항에서는 두 지표가 같다
    for refused in (True, False):
        assert refusal_correct(refused, "out_of_scope") == grounded_refusal_correct(refused, False)
        assert refusal_correct(refused, "lookup") == grounded_refusal_correct(refused, True)


# --- 답 문구(anchor) 기준 채점 (Task 24): 조항을 나누거나 문서 단위 근거일 때 답이 든 청크만 ---


def text_chunk(doc_id: str, index: int, text: str, article: str | None = None) -> Chunk:
    return chunk(doc_id, index, article=article).model_copy(update={"text": text})


NL = chr(10)  # 줄바꿈

# 같은 제7조를 나눈 두 조각: 답(2주 이내)은 둘째 조각에만 있다
piece1 = text_chunk(
    "electronic-financial-transactions-act", 9, "제7조(거래내용의 확인) ① 확인", "제7조"
)
piece2 = text_chunk(
    "electronic-financial-transactions-act",
    10,
    "제7조(거래내용의 확인)" + NL + "② 요청을 받은 날부터" + NL + "2주  이내에",
    "제7조",
)
ev_anchor = Evidence(
    doc_id="electronic-financial-transactions-act", article="제7조", anchor="받은 날부터 2주 이내에"
)


def test_matches_strict_needs_the_anchor_in_the_chunk_text():
    assert matches(piece1, ev_anchor) and not matches_strict(piece1, ev_anchor)
    # 줄바꿈·연속 공백은 공백 하나로 맞춘 뒤 비교한다
    assert matches_strict(piece2, ev_anchor)


UNICODE_SPACED = [
    "받은\u00a0날부터 2주\u00a0이내에",  # NBSP (원문 HTML의 &nbsp;)
    "받은\u3000날부터\u30002주\u3000이내에",  # 전각 공백
    "받은\u2009날부터 2주\u2009이내에",  # 얇은 공백
    "받은 \u00a0" + NL + "\u3000날부터\t2주" + NL + NL + "\u2009이내에",  # 섞여 있다
]


@pytest.mark.parametrize("spaced", UNICODE_SPACED)
def test_matches_strict_treats_unicode_spaces_and_newlines_as_one_space(spaced):
    # PR #24 리뷰: str.split()의 공백(유니코드 공백 포함)을 하나로 맞춘다.
    # 정규식으로 바꿀 때의 회귀를 막는다
    in_chunk = text_chunk(
        "electronic-financial-transactions-act", 11, "제7조(거래내용의 확인)" + NL + spaced, "제7조"
    )
    assert matches_strict(in_chunk, ev_anchor)
    in_anchor = Evidence(
        doc_id="electronic-financial-transactions-act", article="제7조", anchor=spaced
    )
    assert matches_strict(piece2, in_anchor)
    assert not matches_strict(piece1, in_anchor)


def test_matches_strict_without_anchor_is_matches():
    for c in (x1, x2, x3):
        for ev in (ev_x1, ev_x2, ev_x3, ev_missing):
            assert matches_strict(c, ev) == matches(c, ev)


def test_matches_strict_still_requires_doc_and_article():
    other = text_chunk("banking-act", 3, "받은 날부터 2주 이내에", "제7조")
    assert not matches_strict(other, ev_anchor)


def test_matches_strict_narrows_document_level_evidence():
    ev = Evidence(doc_id="prod-fixed-deposit", anchor="최대 3회")
    rows = [
        text_chunk("prod-fixed-deposit", i, t)
        for i, t in enumerate(["금리 표", "| 자동 재예치 | 최대 3회 |"])
    ]
    assert [matches(c, ev) for c in rows] == [True, True]
    assert [matches_strict(c, ev) for c in rows] == [False, True]


def test_strict_recall_and_reciprocal_rank():
    ranked = [piece1, x1, piece2]
    assert recall_at_k(ranked, [ev_anchor], k=2) == 1.0
    assert recall_at_k_strict(ranked, [ev_anchor], k=2) == 0.0
    assert recall_at_k_strict(ranked, [ev_anchor], k=3) == 1.0
    assert reciprocal_rank(ranked, [ev_anchor]) == 1.0
    assert reciprocal_rank_strict(ranked, [ev_anchor]) == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        recall_at_k_strict(ranked, [], k=3)
