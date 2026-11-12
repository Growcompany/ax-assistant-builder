"""Document를 검색 단위 Chunk로 나눈다: 조항 단위, 또는 고정 길이 + 겹침.

조항 단위에서 max_chars를 주면 그보다 긴 본문 조항을 항 → 호 → 목 → 줄 → 글자 수 순서의 경계에서
나누고, 이웃 조각을 max_chars 안에서 욕심내어 묶는다(긴 조항의 뒤쪽 항이 리랭커 입력 512토큰에서
잘려 나가지 않게). 둘째 조각부터 조항 머리 줄을 붙인다.
"""

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
# 부칙의 "다른 법률의 개정"에는 다른 법의 조 번호로 시작하는 지시문이 줄 맨 앞에 온다.
# 조 번호 바로 뒤에 본문 대신 고칠 위치가 온다: "제18조 중 “…”를", "제109조 각 호 외의 부분 중",
# "제275조 및 제446조제50호를 각각 삭제한다." ("제3조 및 제4조 생략"은 진짜 부칙 조항)
_REF = r"제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호(?:의\d+)?)?"
AMENDMENT_AFTER_HEADER = re.compile(
    rf"(?:[ \t]*(?:및|ㆍ|,)[ \t]*{_REF})*"
    r"(?:[을를]|[ \t]*(?:중|각[ \t]*호|본문|단서|전단|후단|제목)(?=\s|$))"
)
# "제5조를 다음과 같이 한다.", "제33조의5를 다음과 같이 신설한다." 다음 줄은 새로 쓰는 다른 법 조문
REWRITTEN_ARTICLE_BEFORE = re.compile(
    r"조(?:의\d+)?[을를][ \t]+(?:각각[ \t]+)?다음과[ \t]+같이[ \t]+(?:신설)?한다\.\s*$"
)

# 긴 조항을 나눌 때 조각에 붙이는 조항 머리: "제11조(대리인 거래)", "제5조 (특례)", 제목이 없으면
# "제7조". 제목 괄호가 80자를 넘거나 앞 공백이 3칸을 넘으면(머리가 아닐 수 있다) 조 번호만 쓴다.
# 길이에 상한을 두어 머리는 최대 95자다(MIN_MAX_CHARS의 절반 아래). 조 번호가 4자리,
# 가지 번호가 3자리를 넘는 조항은 머리를 알아보지 않고 머리 없이 나눈다. 끝의 (?![\d의])는
# 번호가 상한을 넘을 때 앞부분만 잘라 머리로 잡지 않게 한다("제1조의1234"에서 "제1조" 등)
ARTICLE_HEAD = re.compile(
    r"제\d{1,4}조(?:의\d{1,3})?(?:[ \t]{0,3}[(（][^()（）\n]{1,80}[)）])?(?![\d의])"
)
_NON_SPACE = re.compile(r"\S")  # str.isspace()가 아닌 글자 (strip()과 같은 기준)
# 조항을 나누는 경계. 앞의 것부터 쓰고, 그래도 긴 덩어리만 다음 경계로 더 나눈다.
# 항: 줄 맨 앞의 원문자 ①~⑳, ㉑~㉟, ㊱~㊿ (법령 원문에 ㊿까지 있고, 뒤에 공백이 없기도 하다:
#     "①이 법은")
# 호: 줄 맨 앞의 "1. " "2의2. "   목: 줄 맨 앞의 "가. " "나. "   줄: 비지 않은 줄의 맨 앞
SPLIT_LEVELS = (
    re.compile(r"^[ \t]*[①-⑳㉑-㉟㊱-㊿]", re.MULTILINE),
    re.compile(r"^[ \t]*\d{1,3}(?:의\d{1,2})?\.[ \t]", re.MULTILINE),
    re.compile(r"^[ \t]*[가나다라마바사아자차카타파하]\.[ \t]", re.MULTILINE),
    re.compile(r"^(?=[ \t]*\S)", re.MULTILINE),
)
# max_chars의 하한. 조항 머리(이 코퍼스에서 최대 47자, 제목은 80자까지 머리로 본다)를 붙이고도
# 본문이 들어갈 자리가 남아야 한다. Settings.max_chunk_chars의 하한과 같다
MIN_MAX_CHARS = 200

# (원문에서의 시작 위치, 청크 본문, 조 번호)
Piece = tuple[int, str, str | None]


def chunk_by_article(doc: Document, max_chars: int | None = None) -> list[Chunk]:
    """조항 머리부터 다음 조항 머리 앞까지를 한 청크로. 첫 조항 앞 글은 article=None 청크.

    부칙 줄 뒤의 조항은 article을 "부칙 제N조"로 붙여 본문 조항과 겹치지 않게 한다. 부칙 머리 줄은
    그 부칙의 첫 조항 청크 앞에 붙이고, 조항 없이 본문만 있는 부칙은 article="부칙" 청크가 된다.
    부칙 안에서 다른 법률을 고치는 줄은 조항 머리로 보지 않고 앞 부칙 조항 본문에 둔다.

    max_chars가 있으면 그보다 긴 본문 조항을 여러 청크로 나눈다(_split_article). article·page는
    원래 조항 그대로라 근거 라벨이 같은 규칙으로 맞는다. 첫 조항 앞 글과 부칙은 나누지 않는다.
    None이면 나누지 않는다(나누기 전과 같은 결과).
    """
    _check_max_chars(max_chars)
    text, page_at = _join_pages(doc)
    starts: list[int] = [0]
    articles: list[str | None] = [None]
    in_addenda = False
    for start, label in _headers(text):
        if label == ADDENDA:
            in_addenda = True
        elif in_addenda:
            if _amends_other_law(text, start, label):
                continue
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
    if max_chars is not None:
        pieces = [part for piece in pieces for part in _split_article(piece, max_chars)]
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


def chunk_document(doc: Document, max_chars: int | None = None) -> list[Chunk]:
    """조항 머리가 2개 이상이면 doc_type과 무관하게 조항 단위, 아니면 고정 길이.

    가상 내부 지침처럼 법령이 아닌 문서도 제N조 형식이면 조항 단위가 검색·인용에 낫다.
    max_chars는 조항 단위에만 쓴다(긴 조항 나누기). 고정 길이 청크는 이미 800자라 그대로다.
    """
    _check_max_chars(max_chars)
    text, _ = _join_pages(doc)
    if len(ARTICLE_HEADER.findall(text)) >= 2:
        return chunk_by_article(doc, max_chars)
    return chunk_fixed(doc)


def _check_max_chars(max_chars: int | None) -> None:
    if max_chars is not None and max_chars < MIN_MAX_CHARS:
        raise ValueError(f"max_chars must be None or >= {MIN_MAX_CHARS}, got {max_chars}")


def _split_article(piece: Piece, max_chars: int) -> list[Piece]:
    """본문 조항 하나를 max_chars 이하의 조각들로. 조각의 위치·article은 원래 조항 그대로다.

    첫 조각은 머리 + 원문의 구분 문자 하나 + 본문(구분 공백이 한 칸이면 원문 그대로다), 둘째
    조각부터는 "머리\n" + 본문이다. 붙인 머리도 max_chars에 센다. 경계로 나눈 덩어리를 차례로 이어
    붙이다가 넘치면 새 조각을 시작한다. 머리를 알아볼 수 없으면 머리 없이 나눈다.

    끝나는 것: 머리는 95자 이하(ARTICLE_HEAD)이고 max_chars >= 200이라 덩어리 한도가 늘 1 이상이다.
    머리 뒤 공백이 아무리 길어도 한 글자로 세므로 한도를 줄이지 않는다.
    """
    pos, body, article = piece
    if article is None or article.startswith(ADDENDA) or len(body) <= max_chars:
        return [piece]
    found = ARTICLE_HEAD.match(body)
    head = found.group() if found else ""
    start = len(head)
    gap = body[start : start + _leading_space(body[start:])]
    # 원문의 머리 뒤 공백이 여러 칸(줄바꿈 900개 등)이어도 첫 조각에는 한 글자만 둔다
    sep = "\n" if "\n" in gap else gap[:1]
    prefix = head + "\n" if head else ""
    budget = max_chars - len(prefix)  # 덩어리 하나(앞뒤 공백을 뺀 길이)의 상한
    units = _units(body, start, len(body), budget, 0)

    # 조각의 길이 = 붙는 머리 + (조각 첫 글자부터 마지막 글자까지). 덩어리마다 한 번씩만 훑는다
    groups: list[tuple[int, int]] = []
    begin, end = units[0]
    first = begin + _leading_space(body[begin:end])  # 이 조각의 첫 글자 위치
    for unit_start, unit_end in units[1:]:
        last = unit_start + len(body[unit_start:unit_end].rstrip())  # 이 덩어리의 끝 글자 다음
        added = len(head) + len(sep) if not groups else len(prefix)
        if last == unit_start or last - first + added <= max_chars:  # 공백뿐인 덩어리는 늘 붙는다
            end = unit_end
        else:
            groups.append((begin, end))
            begin, end = unit_start, unit_end
            first = begin + _leading_space(body[begin:end])
    groups.append((begin, end))
    first_text = head + sep + body[groups[0][0] : groups[0][1]].strip()
    return [(pos, first_text, article)] + [
        (pos, prefix + body[b:e].strip(), article) for b, e in groups[1:]
    ]


def _units(text: str, lo: int, hi: int, budget: int, level: int) -> list[tuple[int, int]]:
    """text[lo:hi]를 이어진 덩어리들로 나눈다. 덩어리마다 앞뒤 공백을 뺀 길이가 budget 이하다.

    SPLIT_LEVELS[level]의 경계에서 자르고, 그래도 긴 덩어리만 다음 경계로 더 자른다. 경계가 다
    떨어지면 글자 수로 자른다(_cut). 공백뿐인 덩어리는 만들지 않는다(뒤 덩어리에 붙는다).
    """
    if len(text[lo:hi].strip()) <= budget:
        return [(lo, hi)]
    if level == len(SPLIT_LEVELS):
        return _cut(text, lo, hi, budget)
    cuts: list[int] = []
    previous = lo
    for match in SPLIT_LEVELS[level].finditer(text, lo + 1, hi):
        if text[previous : match.start()].strip():
            cuts.append(match.start())
            previous = match.start()
    bounds = [lo, *cuts, hi]
    return [
        unit
        for begin, end in zip(bounds[:-1], bounds[1:], strict=True)
        for unit in _units(text, begin, end, budget, level + 1)
    ]


def _cut(text: str, lo: int, hi: int, budget: int) -> list[tuple[int, int]]:
    """경계가 없는 긴 구간을 budget 글자 이하로 자른다. 뒤쪽 절반에 공백이 있으면 그 자리에서.

    한 번 돌 때마다 적어도 한 글자 앞으로 간다(cut > begin >= start). 구간을 한 번만 훑는다.
    """
    if budget < 1:
        raise ValueError(f"budget must be positive, got {budget}")
    content_end = lo + len(text[lo:hi].rstrip())  # 마지막 글자 다음
    spans: list[tuple[int, int]] = []
    start = lo
    while (found := _NON_SPACE.search(text, start, content_end)) and (
        content_end - found.start() > budget
    ):
        begin = found.start()
        end = begin + budget  # text[begin:end]가 budget 글자다. text[end]가 공백이면 낱말 경계
        cut = next((i + 1 for i in range(end, begin + budget // 2, -1) if text[i].isspace()), end)
        spans.append((start, cut))
        start = cut
    spans.append((start, hi))
    return spans


def _headers(text: str) -> list[tuple[int, str]]:
    """조항 머리(제N조)와 부칙 머리를 원문 위치 순서로."""
    found = [(m.start(1), m.group(1)) for m in ARTICLE_HEADER.finditer(text)]
    found += [(m.start(1), ADDENDA) for m in ADDENDA_HEADER.finditer(text)]
    return sorted(found)


def _amends_other_law(text: str, start: int, label: str) -> bool:
    """부칙 안의 이 조항 머리가 다른 법률 개정 지시문이거나, 지시문 바로 뒤에 새로 쓰는 조문인지."""
    if AMENDMENT_AFTER_HEADER.match(text, start + len(label)):
        return True
    # 앞 줄 끝만 보면 된다 (지시문은 짧다)
    return REWRITTEN_ARTICLE_BEFORE.search(text, max(0, start - 200), start) is not None


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
            page=page_at(pos) if doc.paged else None,
            article=article,
            access_level=doc.meta.access_level,
        )
        for index, (pos, body, article) in enumerate(pieces)
    ]
