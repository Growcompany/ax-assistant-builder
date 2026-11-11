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
//
// 원문은 올린 문서에서 그대로 오므로 악의적으로 만든 입력일 수 있다. 화면이 멈추지 않게:
//   - 줄은 먼저 끝 공백을 떼고, 정규식은 겹치는 반복이 없는 것만 쓴다(입력 길이에 비례하는 시간)
//   - MAX_LINE_CHARS보다 긴 줄은 해석하지 않고 글자 그대로 둔다
//   - MAX_TEXT_CHARS보다 긴 원문은 전체를 서식 없이 보여 주고 그 사실을 한 줄로 알린다

import { MAX_LINE_CHARS, MAX_TEXT_CHARS, parseInline, stripBidi } from "./answer.js";
import { h } from "./dom.js";

// 모두 끝 공백을 뗀 줄에 쓴다
const HEADING = /^[ \t]{0,3}(#{1,4})[ \t]+(\S.*)$/;
const TABLE_RULE = /^\|(?:[ \t]{0,8}:?-{2,64}:?[ \t]{0,8}\|){1,64}$/;
const BULLET = /^[ \t]{0,8}[-*][ \t]+(\S.*)$/;
const NUMBERED = /^[ \t]{0,8}(\d{1,3})\.[ \t]+(\S.*)$/;
const MAX_TABLE_COLUMNS = 64;

function isTableRow(trimmed) {
  return trimmed.length >= 2 && trimmed.startsWith("|") && trimmed.endsWith("|");
}

function cells(trimmed) {
  return trimmed
    .slice(1, -1)
    .split("|")
    .map((cell) => cell.trim());
}

// 원문 -> { blocks, plain }
//   blocks: { type: "heading", text }
//           { type: "table", head: [칸] | null, rows: [[칸]] }
//           { type: "list", ordered, items: [{ number, text }] }
//           { type: "text", lines: [줄] }
//   plain: 너무 길어서 서식 없이 통째로 보여 주는가
export function parsePassage(source) {
  const text = stripBidi(source).replace(/\r\n?/g, "\n");
  if (text.length > MAX_TEXT_CHARS) {
    return { blocks: [{ type: "text", lines: text.split("\n") }], plain: true };
  }
  const lines = text.split("\n");
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
    if (line.length > MAX_LINE_CHARS) {
      addText(line); // 너무 긴 줄은 해석하지 않는다
      continue;
    }
    const trimmed = line.trim();
    if (!trimmed) {
      // 빈 줄: 글 블록 안에서는 줄바꿈으로 남기고, 표·목록·소제목 뒤에서는 블록을 끝낸다
      if (current && current.type === "text") current.lines.push("");
      else current = null;
      continue;
    }
    const tail = line.trimEnd();
    const heading = HEADING.exec(tail);
    if (heading) {
      push({ type: "heading", text: heading[2] });
      current = null;
      continue;
    }
    if (isTableRow(trimmed)) {
      if (TABLE_RULE.test(trimmed)) {
        // 구분선: 바로 앞에 줄 하나짜리 표가 있으면 그 줄이 머리다. 구분선 자체는 그리지 않는다
        if (current && current.type === "table" && !current.head && current.rows.length === 1) {
          current.head = current.rows.pop();
        }
        if (current && current.type === "table") continue;
        // 표 없이 구분선으로 시작한 경우(청크가 머리 줄 뒤에서 잘림): 구분선만 지운다
        const next = index + 1 < lines.length ? lines[index + 1] : "";
        if (next.length <= MAX_LINE_CHARS && isTableRow(next.trim())) continue;
        addText(line);
        continue;
      }
      const row = cells(trimmed);
      if (row.length > MAX_TABLE_COLUMNS) {
        addText(line);
      } else if (current && current.type === "table") {
        const width = (current.head || current.rows[0]).length;
        if (row.length === width) current.rows.push(row);
        else addText(line); // 열 수가 안 맞는 줄은 글자 그대로
      } else {
        push({ type: "table", head: null, rows: [row] });
      }
      continue;
    }
    const bullet = BULLET.exec(tail);
    const numbered = bullet ? null : NUMBERED.exec(tail);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      if (!current || current.type !== "list" || current.ordered !== ordered) {
        push({ type: "list", ordered, items: [] });
      }
      current.items.push(ordered ? { number: Number(numbered[1]), text: numbered[2] } : { number: null, text: bullet[1] });
      continue;
    }
    addText(line);
  }

  // 머리만 있고 내용 줄이 없는 표, 글 블록 앞뒤의 빈 줄 정리
  const cleaned = [];
  for (const block of blocks) {
    if (block.type === "text") {
      let start = 0;
      let end = block.lines.length;
      while (start < end && !block.lines[start].trim()) start += 1;
      while (end > start && !block.lines[end - 1].trim()) end -= 1;
      if (end > start) cleaned.push({ type: "text", lines: block.lines.slice(start, end) });
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
  return { blocks: cleaned, plain: false };
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
  const { blocks, plain } = parsePassage(source);
  if (plain) {
    root.appendChild(h("p", { class: "passage-note", text: "원문이 길어 서식 없이 보여 줍니다." }));
    root.appendChild(h("p", { class: "passage-text", text: blocks[0].lines.join("\n") }));
    return root;
  }
  for (const block of blocks) {
    if (block.type === "heading") {
      root.appendChild(h("h4", { class: "passage-heading" }, inline(block.text)));
    } else if (block.type === "table") {
      const row = (items, tag) =>
        h(
          "tr",
          null,
          items.map((item) => h(tag, tag === "th" ? { scope: "col" } : null, inline(item))),
        );
      root.appendChild(
        h(
          "div",
          { class: "passage-table-wrap" },
          h(
            "table",
            { class: "passage-table" },
            block.head ? h("thead", null, row(block.head, "th")) : null,
            h(
              "tbody",
              null,
              block.rows.map((items) => row(items, "td")),
            ),
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
