// 답변 글자를 안전하게 화면 구조로 바꾼다.
// 문자열을 잘라 토큰으로 만들고, 토큰마다 DOM 노드를 만든다(마크업으로 해석하는 단계가 없다).
// 지원: 빈 줄로 나눈 문단, "- " 목록, **굵게**, 인용 번호 [n].
// [n]은 1 <= n <= 인용 수일 때만 인용 칩이 되고, 그 밖의 번호는 글자 그대로 둔다.

import { h } from "./dom.js";

const INLINE = /\*\*([^*\n]+)\*\*|\[(\d{1,4})\]/g;
const BULLET = /^\s*[-*•]\s+(.*)$/;

// 한 줄 -> [{type: "text"|"bold"|"cite", ...}]
export function parseInline(text, citationCount) {
  const tokens = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) tokens.push({ type: "text", text: text.slice(last, match.index) });
    if (match[1] !== undefined) {
      tokens.push({ type: "bold", children: parseInline(match[1], citationCount) });
    } else {
      const number = Number(match[2]);
      if (number >= 1 && number <= citationCount) tokens.push({ type: "cite", number });
      else tokens.push({ type: "text", text: match[0] });
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) tokens.push({ type: "text", text: text.slice(last) });
  return tokens;
}

// 전체 답변 -> [{type: "paragraph", lines: [tokens]} | {type: "list", items: [tokens]}]
export function parseAnswer(text, citationCount) {
  const blocks = [];
  let current = null;
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  for (const line of lines) {
    if (!line.trim()) {
      current = null;
      continue;
    }
    const bullet = BULLET.exec(line);
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
      root.appendChild(
        h("ul", null, block.items.map((item) => h("li", null, renderInline(item, makeCite)))),
      );
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
