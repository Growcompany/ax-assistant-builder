"""검색·답변 지표. 근거(Evidence)와 청크·인용의 위치를 같은 규칙(matches)으로 비교한다."""

from collections.abc import Callable

from axbuilder.eval.dataset import Evidence
from axbuilder.models import Chunk, Citation


def _same_place(doc_id: str, article: str | None, page: int | None, ev: Evidence) -> bool:
    return (
        doc_id == ev.doc_id
        and (ev.article is None or article == ev.article)
        and (ev.page is None or page == ev.page)
    )


def matches(chunk: Chunk, ev: Evidence) -> bool:
    """doc_id가 같고, ev.article이 있으면 article이, ev.page가 있으면 page가 같다.

    article·page가 없는 근거는 문서 단위라 그 문서의 어느 청크든 맞는다.
    """
    return _same_place(chunk.doc_id, chunk.article, chunk.page, ev)


def matches_strict(chunk: Chunk, ev: Evidence) -> bool:
    """matches이고, ev.anchor가 있으면 청크 본문에 그 문구가 들어 있다(anchor가 없으면 matches).

    조항을 나눈 조각이나 문서 단위 근거(상품설명서)는 라벨만으로는 답이 없는 청크도 맞는다.
    공백(줄바꿈·연속 공백)은 양쪽 모두 하나로 맞춘 뒤 부분 문자열로 비교한다.
    """
    return matches(chunk, ev) and (
        ev.anchor is None or " ".join(ev.anchor.split()) in " ".join(chunk.text.split())
    )


Matcher = Callable[[Chunk, Evidence], bool]


def _recall(retrieved: list[Chunk], evidence: list[Evidence], k: int, match: Matcher) -> float:
    if not evidence:
        # 범위 밖 문항처럼 근거가 없으면 비율을 정의할 수 없다. 0이나 1로 채우면 평균이 왜곡된다
        raise ValueError("recall@k is undefined without evidence")
    top = retrieved[: max(k, 0)]
    found = sum(any(match(chunk, ev) for chunk in top) for ev in evidence)
    return found / len(evidence)


def _reciprocal_rank(retrieved: list[Chunk], evidence: list[Evidence], match: Matcher) -> float:
    for rank, chunk in enumerate(retrieved, start=1):
        if any(match(chunk, ev) for ev in evidence):
            return 1 / rank
    return 0.0


def recall_at_k(retrieved: list[Chunk], evidence: list[Evidence], k: int) -> float:
    """상위 k개 청크로 맞춘 근거의 비율. 근거 하나를 여러 청크가 맞혀도 한 번만 센다."""
    return _recall(retrieved, evidence, k, matches)


def reciprocal_rank(retrieved: list[Chunk], evidence: list[Evidence]) -> float:
    """어느 근거든 처음 맞는 청크 순위(1부터)의 역수. 없으면 0."""
    return _reciprocal_rank(retrieved, evidence, matches)


def recall_at_k_strict(retrieved: list[Chunk], evidence: list[Evidence], k: int) -> float:
    """recall_at_k와 같되 matches_strict로 센다(답 문구가 든 청크만)."""
    return _recall(retrieved, evidence, k, matches_strict)


def reciprocal_rank_strict(retrieved: list[Chunk], evidence: list[Evidence]) -> float:
    """reciprocal_rank와 같되 matches_strict로 센다."""
    return _reciprocal_rank(retrieved, evidence, matches_strict)


def citation_precision(citations: list[Citation], evidence: list[Evidence]) -> float | None:
    """인용 중 근거에 맞는 비율. 인용이 없으면(거절 등) 잴 수 없으므로 None."""
    if not citations:
        return None
    correct = sum(
        any(_same_place(c.doc_id, c.article, c.page, ev) for ev in evidence) for c in citations
    )
    return correct / len(citations)


def refusal_correct(refused: bool, qtype: str) -> bool:
    """범위 밖 질문은 거절해야 맞고, 그 밖의 질문은 답해야 맞다."""
    return refused == (qtype == "out_of_scope")


def grounded_refusal_correct(refused: bool, evidence_in_context: bool) -> bool:
    """프롬프트에 넣은 자료에 정답 근거가 있으면 답해야 맞고, 없으면 거절해야 맞다.

    refusal_correct는 검색이 근거를 못 가져와 모델이 거절한 것(받은 자료로는 옳은 행동)도
    오답으로 센다. 이 지표는 검색 실패와 생성 실패를 가른다: 근거가 없어서 거절한 것은 정답,
    근거가 있는데 거절한 것(과잉 거절)과 근거가 없는데 답한 것(환각 위험)은 오답이다.

    evidence_in_context = 상위 k개 청크에 라벨된 근거가 하나라도 있다(recall_at_k > 0).
    근거가 둘인 문항에서 하나만 들어와도 "있음"이다: 받은 근거로 일부라도 답할 수 있어서다.
    범위 밖 문항은 근거가 없으므로 늘 False다. 라벨 기준이라, 라벨에 없는 청크(대체 근거)로
    답할 수 있었던 문항은 "없음"으로 잘못 분류될 수 있다. 답의 내용이 맞았는지는 보지 않는다.
    """
    return refused != evidence_in_context
