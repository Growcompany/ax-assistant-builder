"""화면 정적 파일(src/axbuilder/api/static)의 정적 검사.

브라우저 없이 확인할 수 있는 것만 본다: 화면 응답의 CSP(default-src 'self')와 "서버에서 온
글자는 textContent로만" 규칙을 깨는 코드가 없는지, 참조한 파일이 실제로 있는지.
실제 동작은 브라우저에서 사람이 확인한다.
"""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "axbuilder" / "api" / "static"
TEXT_SUFFIXES = {".html", ".css", ".js", ".svg"}
# SVG 파일이 이미지로 읽히려면 꼭 있어야 하는 XML 이름공간 식별자. 요청을 보내는 주소가 아니다
XML_NAMESPACES = ("http://www.w3.org/2000/svg",)

# 마크업으로 해석하거나 문자열을 코드로 실행하는 API, 외부 주소, 인라인 스타일
FORBIDDEN = {
    "innerHTML": re.compile(r"innerHTML"),
    "insertAdjacentHTML": re.compile(r"insertAdjacentHTML"),
    "outerHTML": re.compile(r"outerHTML"),
    "document.write": re.compile(r"document\.write"),
    "eval(": re.compile(r"\beval\s*\("),
    "new Function": re.compile(r"\bnew\s+Function\b"),
    "external URL": re.compile(r"(?:https?:)?//[A-Za-z0-9]", re.IGNORECASE),
    "javascript: URL": re.compile(r"javascript:", re.IGNORECASE),
    "<style>": re.compile(r"<style", re.IGNORECASE),
    'style=""': re.compile(r"""style\s*=\s*["']""", re.IGNORECASE),
}
# 인라인 이벤트 속성: 마크업의 on…="" 과, JS에서 같은 일을 하는 .on… = 대입·setAttribute("on…")
INLINE_HANDLER_MARKUP = re.compile(r"""\son[a-z]+\s*=\s*["']""", re.IGNORECASE)
INLINE_HANDLER_JS = re.compile(r"""\.on[a-z]+\s*=(?!=)|setAttribute\(\s*["'`]on""")


def _static_files() -> list[Path]:
    return sorted(p for p in STATIC.rglob("*") if p.is_file())


def _text_files() -> list[Path]:
    return [p for p in _static_files() if p.suffix in TEXT_SUFFIXES]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _without_comment_slashes(path: Path, text: str) -> str:
    """외부 주소 검사용: 이름공간 식별자와 JS·CSS 주석 표시(//, /* */)는 주소가 아니다."""
    for namespace in XML_NAMESPACES:
        text = text.replace(namespace, "")
    if path.suffix == ".js":
        # 줄 주석의 "//" 뒤 글자가 주소처럼 보이지 않게 주석 표시만 지운다(내용은 그대로 검사)
        text = re.sub(r"(^|[\s;{}(),])//", r"\1", text)
    return text


def _name(path: Path) -> str:
    return path.relative_to(STATIC).as_posix()


def test_required_files_exist() -> None:
    for name in ("index.html", "app.css", "favicon.svg", "js/main.js"):
        assert (STATIC / name).is_file(), f"static/{name}이 없다"


def test_only_expected_file_types() -> None:
    # 빌드 산출물·지도 파일·글꼴 같은 것이 섞여 들어오지 않게 한다
    unexpected = [_name(p) for p in _static_files() if p.suffix not in TEXT_SUFFIXES]
    assert unexpected == []


@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_no_forbidden_pattern(label: str) -> None:
    pattern = FORBIDDEN[label]
    found = []
    for path in _text_files():
        text = _read(path)
        if label == "external URL":
            text = _without_comment_slashes(path, text)
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{_name(path)}:{number}: {line.strip()[:80]}")
    assert found == [], f"{label} 금지"


def test_no_inline_event_handlers() -> None:
    found = []
    for path in _text_files():
        pattern = INLINE_HANDLER_JS if path.suffix == ".js" else INLINE_HANDLER_MARKUP
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{_name(path)}:{number}: {line.strip()[:80]}")
    assert found == []


def test_scripts_are_external_files() -> None:
    # CSP가 인라인 스크립트를 막는다: <script>는 모두 src가 있고 내용이 없어야 한다
    html = _read(STATIC / "index.html")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE)
    assert scripts, "index.html에 <script>가 없다"
    for attributes, body in scripts:
        assert re.search(r"""\bsrc\s*=\s*["']/static/""", attributes), attributes
        assert body.strip() == ""


def test_index_references_exist() -> None:
    html = _read(STATIC / "index.html")
    references = re.findall(r"""(?:href|src)\s*=\s*["']([^"'#][^"']*)["']""", html)
    assert references, "index.html이 정적 파일을 하나도 참조하지 않는다"
    for reference in references:
        assert reference.startswith("/static/"), f"/static/ 밖을 가리킨다: {reference}"
        assert (STATIC / reference.removeprefix("/static/")).is_file(), f"없는 파일: {reference}"


def test_module_imports_exist() -> None:
    # 번들러가 없어서 깨진 import는 브라우저에서야 드러난다. 상대 경로 import가 모두 있는지 본다
    pattern = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*["']([^"']+)["']""")
    checked = 0
    for path in (p for p in _text_files() if p.suffix == ".js"):
        for target in pattern.findall(_read(path)):
            assert target.startswith("."), f"{_name(path)}: 상대 경로가 아닌 import {target}"
            assert (path.parent / target).is_file(), f"{_name(path)}: 없는 모듈 {target}"
            checked += 1
    assert checked > 0


def test_css_urls_exist() -> None:
    css_path = STATIC / "app.css"
    for target in re.findall(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", _read(css_path)):
        assert not target.startswith("data:"), "data: URL은 CSP의 img-src에서만 허용된다"
        assert (css_path.parent / target).is_file(), f"app.css: 없는 파일 {target}"
