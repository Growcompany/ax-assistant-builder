// 답변 글자를 안전하게 화면 구조로 바꾼다.
// 문자열을 잘라 토큰으로 만들고, 토큰마다 DOM 노드를 만든다(마크업으로 해석하는 단계가 없다).
// 지원: 빈 줄로 나눈 문단, "- " 목록, **굵게**, 인용 번호 [n]과 묶음 [1, 2].
// 번호는 1 <= n <= 인용 수일 때만 인용 칩이 되고, 그 밖의 번호는 글자 그대로 둔다.
//   "[1]"       -> 칩1
//   "[1, 2]"    -> 칩1 ", " 칩2        (서버는 붙어 있는 인용을 이렇게 묶어 보낸다)
//   "[1,2,3]"   -> 칩1 "," 칩2 "," 칩3
//   "[1, 7]"    -> 칩1 ", " "[7]"      (인용이 2건일 때: 범위 밖 번호만 글자로)
//   "[7]", "[7, 8]" -> 글자 그대로
//
// 정규식은 모두 입력 길이에 비례하는 시간에 끝나게 썼고(겹치는 반복 없음, 반복 횟수 상한),
// 그와 별개로 입력 상한을 둔다: 너무 긴 줄은 해석하지 않고, 너무 긴 답변은 서식 없이 보여 준다.

import { h } from "./dom.js";

export const MAX_LINE_CHARS = 2000; // 이보다 긴 줄은 해석하지 않고 글자 그대로
export const MAX_TEXT_CHARS = 50000; // 이보다 긴 글은 전체를 서식 없이

// **굵게** 또는 [번호(, 번호)*]. 번호 사이 공백은 2칸까지, 묶음은 20개까지만 본다.
const INLINE = /\*\*([^*\n]+)\*\*|\[(\d{1,4}(?:[ \t]{0,2},[ \t]{0,2}\d{1,4}){0,19})\]/g;
const GROUP_PART = /\d+|[^\d]+/g;
const BULLET = /^[ \t]{0,8}[-*•][ \t]+(\S.*)$/;

// 글자 방향을 뒤집는 제어 문자. 답변·원문에 섞여 있으면 "%5.3"이 "3.5%"로 보이거나 인용 번호의
// 위치가 바뀌어 보인다. 표시하기 전에 지운다(ZWJ·ZWNJ는 한글·이모지 표시에 쓰이므로 둔다).
// 코드 포인트로 만든다: 소스에 보이지 않는 문자를 직접 적지 않으려고.
const BIDI_CONTROLS = new RegExp(
  `[${[0x061c, 0x200e, 0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069]
    .map((code) => String.fromCodePoint(code))
    .join("")}]`,
  "g",
);

export function stripBidi(text) {
  return String(text ?? "").replace(BIDI_CONTROLS, "");
}

function citeGroup(inner, citationCount) {
  const parts = inner.match(GROUP_PART) || [];
  const numbers = parts.filter((part) => /^\d/.test(part)).map(Number);
  if (!numbers.some((number) => number >= 1 && number <= citationCount)) return null;
  return parts.map((part) => {
    if (!/^\d/.test(part)) return { type: "text", text: part };
    const number = Number(part);
    return number >= 1 && number <= citationCount ? { type: "cite", number } : { type: "text", text: `[${part}]` };
  });
}

// 한 줄 -> [{type: "text"|"bold"|"cite", ...}]
export function parseInline(text, citationCount) {
  if (text.length > MAX_LINE_CHARS) return [{ type: "text", text }];
  const tokens = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) tokens.push({ type: "text", text: text.slice(last, match.index) });
    if (match[1] !== undefined) {
      tokens.push({ type: "bold", children: parseInline(match[1], citationCount) });
    } else {
      const group = citeGroup(match[2], citationCount);
      if (group) tokens.push(...group);
      else tokens.push({ type: "text", text: match[0] });
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) tokens.push({ type: "text", text: text.slice(last) });
  return tokens;
}

// 전체 답변 -> [{type: "paragraph", lines: [tokens]} | {type: "list", items: [tokens]}]
export function parseAnswer(source, citationCount) {
  const text = stripBidi(source).replace(/\r\n?/g, "\n");
  if (text.length > MAX_TEXT_CHARS) {
    return [{ type: "paragraph", lines: text.split("\n").map((line) => [{ type: "text", text: line }]) }];
  }
  const blocks = [];
  let current = null;
  for (const line of text.split("\n")) {
    if (!line.trim()) {
      current = null;
      continue;
    }
    const bullet = line.length > MAX_LINE_CHARS ? null : BULLET.exec(line);
    const type = bullet ? "list" : "paragraph";
    if (!current || current.type !== type) {
      current = type === "list" ? { type, items: [] } : { type, lines: [] };
      blocks.push(current);
    }
    if (bullet) current.items.push(parseInline(bullet[1], citationCount));
    else current.lines.push(parseInline(line, citationCount));
  }
  return blocks;
}

function renderInline(tokens, makeCite) {
  return tokens.map((token) => {
    if (token.type === "bold") return h("strong", null, renderInline(token.children, makeCite));
    if (token.type === "cite") return makeCite(token.number);
    return document.createTextNode(token.text);
  });
}

// makeCite(번호) -> 인용 칩 노드. 칩의 모양과 동작은 부르는 쪽이 정한다.
export function renderAnswer(text, citationCount, makeCite) {
  const root = h("div", { class: "answer-text" });
  for (const block of parseAnswer(text, citationCount)) {
    if (block.type === "list") {
      root.appendChild(h("ul", null, block.items.map((item) => h("li", null, renderInline(item, makeCite)))));
    } else {
      const paragraph = h("p");
      block.lines.forEach((line, index) => {
        if (index > 0) paragraph.appendChild(h("br"));
        for (const node of renderInline(line, makeCite)) paragraph.appendChild(node);
      });
      root.appendChild(paragraph);
    }
  }
  return root;
}
