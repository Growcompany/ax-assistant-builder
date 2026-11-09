"""국가법령정보센터(law.go.kr) 법령·행정규칙 본문 HTML을 조문 줄 구조를 살린 텍스트로 바꾼다.

본문 HTML은 조문의 조·항·호·목이 각각 <p> 하나다. <p> 같은 블록 요소를 줄로 바꾸면 각 조문이
줄 맨 앞 "제N조(제목)"으로 시작해 조항 단위 분할(chunking.ARTICLE_HEADER)이 그대로 동작한다.
"""

import re
from html.parser import HTMLParser

# 여는·닫는 태그가 줄을 끊는 블록 요소
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "dl", "dt", "dd",
    "table", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
}  # fmt: skip
# 화면 기능용이라 내용을 버리는 요소
_SKIP_TAGS = {"script", "style", "title", "textarea", "noscript", "select", "button"}
# 원문은 "부      칙"처럼 글자 사이를 띄운다
_SPACED_ADDENDA = re.compile(r"^부\s+칙")


def law_html_to_text(html: str) -> str:
    """조문 줄 텍스트. 법령명 제목(<h2>) 앞의 화면 메뉴(판례·연혁 버튼 등)는 버린다."""
    parser = _LawTextParser()
    parser.feed(html)
    parser.close()
    lines = parser.finish()
    return "\n".join(_SPACED_ADDENDA.sub("부칙", line) for line in lines)


class _LawTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # &lt; 같은 문자 참조를 글자로
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._skip_depth = 0
        self._title_at: int | None = None  # 첫 <h2>가 시작된 줄 번호

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._flush()
            if tag == "h2" and self._title_at is None:
                self._title_at = len(self.lines)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <br /> 같은 빈 요소. 기본 동작(start + end)을 따르면 건너뛰기 깊이가 어긋날 수 있다
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._buffer.append(data)

    def finish(self) -> list[str]:
        self._flush()
        return self.lines[self._title_at or 0 :]

    def _flush(self) -> None:
        line = " ".join("".join(self._buffer).split())
        self._buffer.clear()
        if line:
            self.lines.append(line)
