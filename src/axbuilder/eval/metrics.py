"""검색·답변 지표. 근거(Evidence)와 청크·인용의 위치를 같은 규칙(matches)으로 비교한다."""

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


def recall_at_k(retrieved: list[Chunk], evidence: list[Evidence], k: int) -> float:
    """상위 k개 청크로 맞춘 근거의 비율. 근거 하나를 여러 청크가 맞혀도 한 번만 센다."""
    if not evidence:
        # 범위 밖 문항처럼 근거가 없으면 비율을 정의할 수 없다. 0이나 1로 채우면 평균이 왜곡된다
        raise ValueError("recall@k is undefined without evidence")
    top = retrieved[: max(k, 0)]
    found = sum(any(matches(chunk, ev) for chunk in top) for ev in evidence)
    return found / len(evidence)


def reciprocal_rank(retrieved: list[Chunk], evidence: list[Evidence]) -> float:
    """어느 근거든 처음 맞는 청크 순위(1부터)의 역수. 없으면 0."""
    for rank, chunk in enumerate(retrieved, start=1):
        if any(matches(chunk, ev) for ev in evidence):
            return 1 / rank
    return 0.0


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
