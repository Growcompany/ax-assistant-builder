// 근거 원문(passages[i].text)을 안전하게 화면 구조로 바꾼다.
// answer.js와 같은 방식이다: 문자열을 줄 단위로 잘라 블록으로 만들고 블록마다 DOM 노드를 만든다
// (글자는 모두 텍스트 노드로 들어간다. 마크업으로 해석하는 단계가 없다).
//
// 원문은 대부분 법령 평문이라 평문이 그대로 보이는 것이 먼저다. 마크다운 문서에서 온 원문에 한해
// 다음만 그린다:
//   "# " ~ "#### " 줄        -> 작은 소제목
//   연속된 "| a | b |" 줄    -> 표 (구분선 줄 "|---|---|"은 지운다. 구분선 바로 앞 줄이 머리)
//   "- " / "* " 줄           -> 목록
//   "1. " 줄                 -> 번호 목록 (원문의 번호를 그대로 쓴다. 3호부터 시작하면 3부터)
//   **굵게**
// 그 밖의 글자는 그대로, 줄바꿈도 그대로 둔다.
// 표는 청크 경계에서 잘릴 수 있다: 머리 줄 없이 시작하면 머리 없는 표로 그리고, 열 수가 표의 첫 줄과
// 다른 줄은 표에 넣지 않고 글자 그대로 보여 준다.

import { parseInline } from "./answer.js";
import { h } from "./dom.js";

const HEADING = /^\s{0,3}(#{1,4})\s+(.*\S)\s*$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_RULE = /^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$/;
const BULLET = /^\s*[-*]\s+(.*)$/;
const NUMBERED = /^\s*(\d{1,3})\.\s+(.*)$/;

function cells(line) {
  const inner = line.trim().slice(1, -1);
  return inner.split("|").map((cell) => cell.trim());
}

// 원문 -> 블록 배열:
//   { type: "heading", text }
//   { type: "table", head: [칸] | null, rows: [[칸]] }
//   { type: "list", ordered, items: [{ number, text }] }
//   { type: "text", lines: [줄] }
export function parsePassage(source) {
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let current = null;
  const push = (block) => {
    blocks.push(block);
    current = block;
    return block;
  };
  const addText = (line) => {
    if (!current || current.type !== "text") push({ type: "text", lines: [] });
    current.lines.push(line);
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const heading = HEADING.exec(line);
    if (heading) {
      push({ type: "heading", text: heading[2] });
      current = null;
      continue;
    }
    if (TABLE_ROW.test(line)) {
      if (TABLE_RULE.test(line)) {
        // 구분선: 바로 앞에 줄 하나짜리 표가 있으면 그 줄이 머리다. 구분선 자체는 그리지 않는다
        if (current && current.type === "table" && !current.head && current.rows.length === 1) {
          current.head = current.rows.pop();
        }
        if (current && current.type === "table") continue;
        // 표 없이 구분선으로 시작한 경우(청크가 머리 줄 뒤에서 잘림): 구분선만 지운다
        if (index + 1 < lines.length && TABLE_ROW.test(lines[index + 1])) continue;
        addText(line);
        continue;
      }
      const row = cells(line);
      if (current && current.type === "table") {
        const width = (current.head || current.rows[0]).length;
        if (row.length === width) current.rows.push(row);
        else {
          addText(line); // 열 수가 안 맞는 줄은 글자 그대로
        }
      } else {
        push({ type: "table", head: null, rows: [row] });
      }
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      if (!current || current.type !== "list" || current.ordered !== ordered) {
        push({ type: "list", ordered, items: [] });
      }
      current.items.push(ordered ? { number: Number(numbered[1]), text: numbered[2] } : { number: null, text: bullet[1] });
      continue;
    }
    if (!line.trim()) {
      // 빈 줄: 글 블록 안에서는 줄바꿈으로 남기고, 표·목록·소제목 뒤에서는 블록을 끝낸다
      if (current && current.type === "text") current.lines.push("");
      else current = null;
      continue;
    }
    addText(line);
  }

  // 머리만 있고 내용 줄이 없는 표, 앞뒤 빈 줄 정리
  const cleaned = [];
  for (const block of blocks) {
    if (block.type === "text") {
      while (block.lines.length && !block.lines[0].trim()) block.lines.shift();
      while (block.lines.length && !block.lines[block.lines.length - 1].trim()) block.lines.pop();
      if (block.lines.length) cleaned.push(block);
    } else if (block.type === "table") {
      if (block.head && !block.rows.length) {
        block.rows.push(block.head);
        block.head = null;
      }
      if (block.rows.length) cleaned.push(block);
    } else {
      cleaned.push(block);
    }
  }
  return cleaned;
}

// 한 줄의 글자 -> 노드. **굵게**만 본다(원문 속 [1] 같은 글자는 인용 번호가 아니라 글자다).
function inline(text) {
  const render = (tokens) =>
    tokens.map((token) =>
      token.type === "bold" ? h("strong", null, render(token.children)) : document.createTextNode(token.text),
    );
  return render(parseInline(text, 0));
}

export function renderPassage(source) {
  const root = h("div", { class: "passage" });
  for (const block of parsePassage(source)) {
    if (block.type === "heading") {
      root.appendChild(h("h4", { class: "passage-heading" }, inline(block.text)));
    } else if (block.type === "table") {
      const row = (items, tag) => h("tr", null, items.map((item) => h(tag, tag === "th" ? { scope: "col" } : null, inline(item))));
      root.appendChild(
        h(
          "div",
          { class: "passage-table-wrap" },
          h(
            "table",
            { class: "passage-table" },
            block.head ? h("thead", null, row(block.head, "th")) : null,
            h("tbody", null, block.rows.map((items) => row(items, "td"))),
          ),
        ),
      );
    } else if (block.type === "list") {
      root.appendChild(
        h(
          block.ordered ? "ol" : "ul",
          { class: "passage-list" },
          block.items.map((item) => h("li", block.ordered ? { value: String(item.number) } : null, inline(item.text))),
        ),
      );
    } else {
      // 평문: 줄바꿈을 그대로 둔다(.passage-text가 pre-wrap)
      const text = h("p", { class: "passage-text" });
      block.lines.forEach((line, index) => {
        if (index > 0) text.appendChild(document.createTextNode("\n"));
        for (const node of inline(line)) text.appendChild(node);
      });
      root.appendChild(text);
    }
  }
  return root;
}
