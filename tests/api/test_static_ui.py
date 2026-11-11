"""화면 정적 파일(src/axbuilder/api/static)의 정적 검사.

브라우저 없이 확인할 수 있는 것만 본다: 화면 응답의 CSP(default-src 'self')와 "서버에서 온
글자는 textContent로만" 규칙을 깨는 코드가 없는지, 참조한 파일이 실제로 있는지.
node가 있으면 답변·원문 파서를 실행해 묶음 인용과 악성 입력의 처리 시간도 확인한다.
실제 동작은 브라우저에서 사람이 확인한다.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "axbuilder" / "api" / "static"
TEXT_SUFFIXES = {".html", ".css", ".js", ".svg"}
# SVG 파일이 이미지로 읽히려면 꼭 있어야 하는 XML 이름공간 식별자. 요청을 보내는 주소가 아니다
XML_NAMESPACES = ("http://www.w3.org/2000/svg",)

# 마크업으로 해석하거나 문자열을 코드로 실행하는 API, 외부 주소, 인라인 스타일,
# 값을 밖으로 내보내거나 다른 곳에 남길 수 있는 API
FORBIDDEN = {
    "innerHTML": re.compile(r"innerHTML"),
    "insertAdjacentHTML": re.compile(r"insertAdjacentHTML"),
    "outerHTML": re.compile(r"outerHTML"),
    "document.write": re.compile(r"document\.write"),
    "DOMParser": re.compile(r"DOMParser"),
    "createContextualFragment": re.compile(r"createContextualFragment"),
    "srcdoc": re.compile(r"srcdoc", re.IGNORECASE),
    "eval(": re.compile(r"\beval\s*\("),
    "new Function": re.compile(r"\bnew\s+Function\b"),
    "timer with a string": re.compile(r"""\bset(?:Timeout|Interval)\(\s*["'`]"""),
    "external URL": re.compile(r"(?:https?:)?//[A-Za-z0-9]", re.IGNORECASE),
    "javascript: URL": re.compile(r"javascript:", re.IGNORECASE),
    "<style>": re.compile(r"<style", re.IGNORECASE),
    'style=""': re.compile(r"""style\s*=\s*["']""", re.IGNORECASE),
    "cssText": re.compile(r"cssText"),
    "location write": re.compile(
        r"\blocation\s*=(?!=)|\blocation\.(?:href|hash|search|pathname)\s*=(?!=)"
        r"|\blocation\.(?:assign|replace)\s*\("
    ),
    "window.open": re.compile(r"\bwindow\.open\b|(?<![\w.])open\s*\(\s*[\"'`]"),
    "URL property write": re.compile(r"\.(?:href|src|action|formAction)\s*=(?!=)"),
    "sessionStorage": re.compile(r"sessionStorage"),
    "document.cookie": re.compile(r"document\.cookie"),
    "console": re.compile(r"\bconsole\."),
    "postMessage": re.compile(r"postMessage"),
}
# 금지 패턴에 걸리지만 정당한 줄: (금지 이름, 파일, 그 줄에 있어야 하는 글자) -> 이유.
# 줄 전체가 아니라 이 글자가 든 줄만 넘어간다. 다른 줄에서 같은 패턴을 쓰면 실패한다
ALLOWED_LINES = {
    (
        "console",
        "js/views/connect.js",
        '"console.log(data.answer, data.citations);",',
    ): "연결 탭이 사용자에게 글자로 보여 주는 JavaScript 예시 코드(실행하지 않는다)",
}
# 인라인 이벤트 속성: 마크업의 on…="" 과, JS에서 같은 일을 하는 .on… = 대입·setAttribute("on…")
INLINE_HANDLER_MARKUP = re.compile(r"""\son[a-z]+\s*=\s*["']""", re.IGNORECASE)
INLINE_HANDLER_JS = re.compile(r"""\.on[a-z]+\s*=(?!=)|setAttribute\(\s*["'`]on""")

# setAttribute는 이름이 문자열 리터럴이고 상태를 나타내는 속성일 때만 쓴다
SAFE_SET_ATTRIBUTE = re.compile(r"""setAttribute\(\s*"(?:aria-[a-z]+|data-[a-z-]+|role)"\s*,""")
# 예외 한 곳: h()가 속성을 넣는 줄. 바로 앞 줄에서 허용 목록(isSafeAttribute)을 통과한 이름만 온다
H_SET_ATTRIBUTE_LINE = 'else node.setAttribute(key, value === true ? "" : String(value));'
H_GUARD_LINE = "else if (!isSafeAttribute(key)) throw new Error("
# h()의 속성으로 쓰면 안 되는 이름: 주소를 싣거나, 값이 실행되거나, 인라인 스타일인 것
UNSAFE_ATTRIBUTE = re.compile(
    r"^(?:href|xlink:href|src|srcset|srcdoc|style|action|formaction|data|code|codebase|poster"
    r"|background|ping|on.+)$",
    re.IGNORECASE,
)
# h()가 속성이 아니라 직접 처리하는 키(class, text 등)와 불리언 속성
H_SPECIAL_KEYS = {"class", "text", "value", "on", "data"}
H_BOOLEAN_KEYS = {"disabled", "hidden", "checked", "required", "open", "readOnly"}

# 브라우저 저장소에 남겨도 되는 것: 테마, 고른 워크스페이스, 워크스페이스별 고른 역할
STORAGE_KEYS = {"theme", "workspace", "roles"}
STORAGE_FILES = {"js/state.js", "js/theme-init.js"}

# 파서가 악성 입력 하나를 처리하는 시간 상한. 개발 PC에서는 모두 100ms 안쪽이다.
# 느린 CI를 감안해 넉넉히 둔다
PARSER_LIMIT_MS = 1000


def _static_files() -> list[Path]:
    return sorted(p for p in STATIC.rglob("*") if p.is_file())


def _text_files() -> list[Path]:
    return [p for p in _static_files() if p.suffix in TEXT_SUFFIXES]


def _js_files() -> list[Path]:
    return [p for p in _text_files() if p.suffix == ".js"]


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


def _allowed(label: str, name: str, line: str) -> bool:
    return any(
        label == allowed_label and name == allowed_name and fragment in line
        for allowed_label, allowed_name, fragment in ALLOWED_LINES
    )


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
            if pattern.search(line) and not _allowed(label, _name(path), line):
                found.append(f"{_name(path)}:{number}: {line.strip()[:80]}")
    assert found == [], f"{label} 금지"


def test_allowed_lines_still_exist() -> None:
    # 예외 목록이 낡아 "아무 줄이나 허용"으로 남지 않게: 예외마다 그 줄이 실제로 있어야 한다
    for label, name, fragment in ALLOWED_LINES:
        lines = _read(STATIC / name).splitlines()
        assert any(fragment in line and FORBIDDEN[label].search(line) for line in lines), (
            f"{name}에 예외로 둔 줄이 없다: {fragment}"
        )


def test_no_inline_event_handlers() -> None:
    found = []
    for path in _text_files():
        pattern = INLINE_HANDLER_JS if path.suffix == ".js" else INLINE_HANDLER_MARKUP
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if pattern.search(line):
                found.append(f"{_name(path)}:{number}: {line.strip()[:80]}")
    assert found == []


def test_set_attribute_only_with_safe_literal_names() -> None:
    found = []
    for path in _js_files():
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if "setAttribute(" not in line or SAFE_SET_ATTRIBUTE.search(line):
                continue
            if _name(path) == "js/dom.js" and line.strip() == H_SET_ATTRIBUTE_LINE:
                continue
            found.append(f"{_name(path)}:{number}: {line.strip()[:80]}")
    assert found == []


def test_h_checks_attribute_names_before_setting_them() -> None:
    # h()의 setAttribute 바로 앞 줄이 허용 목록 검사여야 하고, 허용 목록에 위험한 이름이 없어야 한다
    source = _read(STATIC / "js" / "dom.js")
    lines = [line.strip() for line in source.splitlines()]
    index = lines.index(H_SET_ATTRIBUTE_LINE)
    assert lines[index - 1].startswith(H_GUARD_LINE)
    listed = re.search(r"const SAFE_ATTRIBUTES = new Set\(\[(.*?)\]\);", source, flags=re.DOTALL)
    assert listed, "dom.js에 SAFE_ATTRIBUTES가 없다"
    names = re.findall(r'"([^"]+)"', listed.group(1))
    assert names, "SAFE_ATTRIBUTES가 비어 있다"
    assert [name for name in names if UNSAFE_ATTRIBUTE.match(name)] == []
    # 접두사 허용은 aria-*, data-* 둘뿐이다
    assert "const SAFE_ATTRIBUTE_PREFIX = /^(?:aria|data)-[a-z][a-z-]*$/;" in source


def _skip_string(text: str, start: int) -> int:
    """text[start]가 따옴표일 때 그 문자열이 끝난 다음 위치."""
    quote = text[start]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if quote == "`" and text.startswith("${", index):
            depth = 1
            index += 2
            while index < len(text) and depth:
                if text[index] in "\"'`":
                    index = _skip_string(text, index)
                    continue
                depth += {"{": 1, "}": -1}.get(text[index], 0)
                index += 1
            continue
        if char == quote:
            return index + 1
        index += 1
    return index


def _object_keys(text: str, start: int) -> list[str]:
    """text[start]가 "{"인 객체 리터럴의 맨 바깥 키들."""
    keys = []
    depth = 0
    expecting_key = False
    index = start
    while index < len(text):
        char = text[index]
        if char in "\"'`":
            end = _skip_string(text, index)
            if depth == 1 and expecting_key and re.match(r"\s*:", text[end:]):
                keys.append(text[index + 1 : end - 1])
            expecting_key = False
            index = end
            continue
        if char in "{[(":
            depth += 1
            expecting_key = char == "{" and depth == 1
        elif char in "}])":
            depth -= 1
            if depth == 0:
                break
        elif char == "," and depth == 1:
            expecting_key = True
        elif depth == 1 and expecting_key and (char.isalpha() or char in "_$"):
            match = re.match(r"[\w$]+", text[index:])
            if re.match(r"\s*:", text[index + match.end() :]):
                keys.append(match.group())
            expecting_key = False
            index += match.end()
            continue
        elif not char.isspace():
            expecting_key = False
        index += 1
    return keys


def _h_calls(text: str) -> list[list[str]]:
    """소스에서 h(태그, { 속성 }, …) 호출마다 속성 객체의 키 목록."""
    calls = []
    for match in re.finditer(r"(?<![\w$.])h\(", text):
        index = match.end()
        depth = 0
        # 첫 인자(태그)를 건너뛰고 첫 번째 맨 바깥 쉼표를 찾는다
        while index < len(text):
            char = text[index]
            if char in "\"'`":
                index = _skip_string(text, index)
                continue
            if char in "([{":
                depth += 1
            elif char in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif char == "," and depth == 0:
                rest = text[index + 1 :]
                offset = len(rest) - len(rest.lstrip())
                if rest[offset : offset + 1] == "{":
                    calls.append(_object_keys(text, index + 1 + offset))
                break
            index += 1
    return calls


def test_h_scanner_reads_property_keys() -> None:
    # 아래 검사가 실제로 키를 읽는지: 스캐너 자체를 확인한다
    sample = """
      h("a", { class: "x", "aria-label": `열기 ${f({ href: 1 })}`,
               on: { click: () => go({ src: 2 }) } }, "글")
      h(ok ? "ol" : "ul", { id: "list", hidden: true }, items.map((item) => h("li", null, item)))
      h("img", { src: url })
    """
    assert _h_calls(sample) == [["class", "aria-label", "on"], ["id", "hidden"], ["src"]]


def test_h_calls_use_no_unsafe_attribute() -> None:
    found = []
    seen = 0
    for path in _js_files():
        for keys in _h_calls(_read(path)):
            for key in keys:
                seen += 1
                if key in H_SPECIAL_KEYS or key in H_BOOLEAN_KEYS:
                    continue
                if UNSAFE_ATTRIBUTE.match(key):
                    found.append(f"{_name(path)}: h()의 속성 {key}")
    assert seen > 100, "h() 호출의 속성을 읽지 못했다"
    assert found == []


def test_browser_storage_holds_only_preferences() -> None:
    used_keys: set[str] = set()
    for path in _js_files():
        text = _read(path)
        if "localStorage" in text:
            assert _name(path) in STORAGE_FILES, f"{_name(path)}가 localStorage를 직접 쓴다"
        used_keys.update(re.findall(r"""prefs\.(?:get|set)\(\s*["']([^"']+)["']""", text))
        used_keys.update(re.findall(r"""["']axb\.([a-z]+)["']""", text))
        # 키를 변수로 넘기면 무엇을 저장하는지 이 검사가 알 수 없다
        assert not re.search(r"prefs\.(?:get|set)\(\s*[^\"'\s]", text), _name(path)
    assert used_keys, "저장 키를 하나도 찾지 못했다"
    assert used_keys <= STORAGE_KEYS, f"허용하지 않은 저장 키: {sorted(used_keys - STORAGE_KEYS)}"
    # 키 이름은 state.js에서 "axb." 접두사를 붙여 만든다
    assert "window.localStorage.setItem(`axb.${key}`, value)" in _read(STATIC / "js" / "state.js")


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
    for path in _js_files():
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


# 답변·원문 파서를 node로 실행한다: 묶음 인용, 방향 제어 문자, 그리고 악성 입력의 처리 시간.
# 20만 자 입력과, 입력 상한 바로 아래(줄 1,999자 × 전체 49,000자쯤)의 입력을 모두 잰다
PARSER_SCRIPT = r"""
import assert from "node:assert/strict";
import { parseAnswer, parseInline, stripBidi } from "./answer.mjs";
import { parsePassage } from "./passage.mjs";

const kind = (t) => (t.type === "cite" ? `c${t.number}` : t.type === "text" ? t.text : t.type);
const kinds = (tokens) => tokens.map(kind);
assert.deepEqual(kinds(parseInline("금리 [1]", 2)), ["금리 ", "c1"]);
assert.deepEqual(kinds(parseInline("[1, 2]", 2)), ["c1", ", ", "c2"]);
assert.deepEqual(kinds(parseInline("[1,2]", 2)), ["c1", ",", "c2"]);
assert.deepEqual(kinds(parseInline("[1, 2, 3]", 3)), ["c1", ", ", "c2", ", ", "c3"]);
assert.deepEqual(kinds(parseInline("[1, 7]", 2)), ["c1", ", ", "[7]"]);
assert.deepEqual(kinds(parseInline("[7, 8] [0] [3]", 2)), ["[7, 8]", " ", "[0]", " ", "[3]"]);
assert.deepEqual(kinds(parseInline("**굵게 [2]**", 2)), ["bold"]);
assert.deepEqual(kinds(parseInline("[1 2] [a] []", 2)), ["[1 2] [a] []"]);

const rlo = String.fromCodePoint(0x202e);
assert.equal(stripBidi(`연 ${rlo}%5.3 [1]`), "연 %5.3 [1]");
assert.equal(parseAnswer(`${rlo}- 하나`, 1)[0].type, "list");
const zwj = String.fromCodePoint(0x200d);
assert.equal(stripBidi(`가${zwj}나`), `가${zwj}나`);

const table = parsePassage("### 제목\n\n| a | b |\n|---|---|\n| 1 | 2 |").blocks;
assert.deepEqual(table.map((b) => b.type), ["heading", "table"]);
assert.deepEqual(table[1].head, ["a", "b"]);
assert.equal(parsePassage("#" + " ".repeat(60000)).blocks[0].type, "text");
assert.equal(parsePassage("가".repeat(60000)).plain, true);

const units = {
  spaces: " ", tabs: "\t", hashes: "#", "hash+spaces": "# ", pipes: "|", "pipe+space": "| ",
  "pipe+dashes": "|--", stars: "*", "star pairs": "**a", brackets: "[", "open cites": "[1, ",
  "cite lists": "[1,2,3]", dashes: "-", "dash+space": "- ", "number+dot": "1.",
  "number+dot+space": "1. ",
  "heading+trailing spaces": null,
};
const timings = {};
const time = (label, run) => {
  const start = performance.now();
  run();
  timings[label] = Math.round((performance.now() - start) * 10) / 10;
};
for (const [name, unit] of Object.entries(units)) {
  const big =
    unit === null ? "#" + " ".repeat(199999) : unit.repeat(Math.ceil(200000 / unit.length));
  const line = big.slice(0, 1999);
  const capped = Array.from({ length: 24 }, () => line).join("\n");
  const short = unit === null ? "# " + " ".repeat(8) : unit.repeat(12).slice(0, 11);
  const manyLines = Array.from({ length: 4000 }, () => short).join("\n");
  time(`passage 200k ${name}`, () => parsePassage(big));
  time(`answer 200k ${name}`, () => parseAnswer(big, 5));
  time(`passage capped ${name}`, () => parsePassage(capped));
  time(`answer capped ${name}`, () => parseAnswer(capped, 5));
  time(`passage lines ${name}`, () => parsePassage(manyLines));
  time(`answer lines ${name}`, () => parseAnswer(manyLines, 5));
}
console.log(JSON.stringify(timings));
"""


def test_parsers_handle_grouped_citations_and_hostile_input_quickly(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node가 없어 파서를 실행해 볼 수 없다")
    # 정적 파일은 .js인 ES 모듈이다. node가 버전과 상관없이 ES 모듈로 읽게 .mjs로 복사한다
    for name in ("answer", "passage", "dom", "naming", "validate"):
        source = _read(STATIC / "js" / f"{name}.js")
        (tmp_path / f"{name}.mjs").write_text(source.replace('.js"', '.mjs"'), encoding="utf-8")
    script = tmp_path / "check.mjs"
    script.write_text(PARSER_SCRIPT, encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - 우리가 쓴 스크립트를 node로 실행한다
        [node, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    timings = json.loads(result.stdout.strip().splitlines()[-1])
    assert len(timings) > 50
    slow = {label: ms for label, ms in timings.items() if ms > PARSER_LIMIT_MS}
    assert slow == {}, f"파서가 느린 입력: {slow}"
