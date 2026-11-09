"""Document를 검색 단위 Chunk로 나눈다: 조항 단위, 또는 고정 길이 + 겹침."""

import bisect
import re
from collections.abc import Callable

from axbuilder.models import Chunk, Document

# 줄 맨 앞의 조항 머리 "제2조", "제2조의2". 바로 뒤가 공백·괄호·줄 끝일 때만 머리로 본다.
# PDF 줄바꿈으로 줄 앞에 온 다른 조항 언급("제3조에 따른", "제3조제1항", "제2조의 규정")은 제외
ARTICLE_HEADER = re.compile(r"^[ \t]*(제\d+조(?:의\d+)?)(?=[\s(（【]|$)", re.MULTILINE)
# 줄 맨 앞의 부칙 머리 "부칙", "부 칙 <법률 제21296호, 2025. 12. 30.>", "부칙(…)<제2013-44호, …>".
# 바로 뒤가 줄 끝·꺾쇠·괄호일 때만 머리로 본다.
# "부칙 제7조의 개정규정은"처럼 부칙 조항을 가리키는 말이 줄 맨 앞에 와도 머리가 아니다
ADDENDA_HEADER = re.compile(r"^[ \t]*(부[ \t]*칙)[ \t]*(?=$|[<(（\[〈【])", re.MULTILINE)
ADDENDA = "부칙"

# (원문에서의 시작 위치, 청크 본문, 조 번호)
Piece = tuple[int, str, str | None]


def chunk_by_article(doc: Document) -> list[Chunk]:
    """조항 머리부터 다음 조항 머리 앞까지를 한 청크로. 첫 조항 앞 글은 article=None 청크.

    부칙 줄 뒤의 조항은 article을 "부칙 제N조"로 붙여 본문 조항과 겹치지 않게 한다. 부칙 머리 줄은
    그 부칙의 첫 조항 청크 앞에 붙이고, 조항 없이 본문만 있는 부칙은 article="부칙" 청크가 된다.
    """
    text, page_at = _join_pages(doc)
    starts: list[int] = [0]
    articles: list[str | None] = [None]
    in_addenda = False
    for start, label in _headers(text):
        if label == ADDENDA:
            in_addenda = True
        elif in_addenda:
            label = f"{ADDENDA} {label}"
            if articles[-1] == ADDENDA:  # 바로 앞이 부칙 머리 줄이면 그 줄부터 이 조항 청크로
                articles[-1] = label
                continue
        starts.append(start)
        articles.append(label)
    bounds = [*starts, len(text)]

    pieces: list[Piece] = []
    for start, end, article in zip(bounds[:-1], bounds[1:], articles, strict=True):
        body = text[start:end]
        if body.strip():
            pieces.append((start + _leading_space(body), body.strip(), article))
    return _to_chunks(doc, pieces, page_at)


def chunk_fixed(doc: Document, size: int = 800, overlap: int = 100) -> list[Chunk]:
    """글자 수 기준 고정 길이 창. 이웃한 청크는 overlap 글자를 공유한다."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError(f"need size > 0 and 0 <= overlap < size, got {size=}, {overlap=}")
    text, page_at = _join_pages(doc)

    pieces: list[Piece] = []
    for start in range(0, len(text), size - overlap):
        # 겹침이 정확히 맞도록 자른 그대로 둔다 (strip하지 않음)
        body = text[start : start + size]
        if body.strip():
            pieces.append((start + _leading_space(body), body, None))
        if start + size >= len(text):  # 마지막 창이 끝에 닿았으면 그 뒤 창은 겹침뿐이다
            break
    return _to_chunks(doc, pieces, page_at)


def chunk_document(doc: Document) -> list[Chunk]:
    """조항 머리가 2개 이상이면 doc_type과 무관하게 조항 단위, 아니면 고정 길이.

    가상 내부 지침처럼 법령이 아닌 문서도 제N조 형식이면 조항 단위가 검색·인용에 낫다.
    """
    text, _ = _join_pages(doc)
    if len(ARTICLE_HEADER.findall(text)) >= 2:
        return chunk_by_article(doc)
    return chunk_fixed(doc)


def _headers(text: str) -> list[tuple[int, str]]:
    """조항 머리(제N조)와 부칙 머리를 원문 위치 순서로."""
    found = [(m.start(1), m.group(1)) for m in ARTICLE_HEADER.finditer(text)]
    found += [(m.start(1), ADDENDA) for m in ADDENDA_HEADER.finditer(text)]
    return sorted(found)


def _join_pages(doc: Document) -> tuple[str, Callable[[int], int]]:
    """빈 페이지를 뺀 페이지들을 줄바꿈으로 잇고, 위치 → 페이지 번호 함수를 함께 돌려준다."""
    bodies: list[str] = []
    starts: list[int] = []
    numbers: list[int] = []
    offset = 0
    for page in doc.pages:
        body = page.text.strip()
        if not body:
            continue
        bodies.append(body)
        starts.append(offset)
        numbers.append(page.number)
        offset += len(body) + 1  # 페이지 사이 "\n"

    def page_at(pos: int) -> int:
        return numbers[bisect.bisect_right(starts, pos) - 1]

    return "\n".join(bodies), page_at


def _leading_space(text: str) -> int:
    return len(text) - len(text.lstrip())


def _to_chunks(doc: Document, pieces: list[Piece], page_at: Callable[[int], int]) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"{doc.meta.doc_id}::{index:04d}",
            doc_id=doc.meta.doc_id,
            workspace_id=doc.workspace_id,
            title=doc.meta.title,
            text=body,
            page=page_at(pos),
            article=article,
            access_level=doc.meta.access_level,
        )
        for index, (pos, body, article) in enumerate(pieces)
    ]
