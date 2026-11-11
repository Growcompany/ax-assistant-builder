// 근거 패널: 인용 하나의 문서 제목·조항·등급과 청크 원문을 보여 준다.
// 넓은 화면에서는 오른쪽 열, 좁은 화면에서는 아래에서 올라오는 판이다.

import { closeEvidence, openEvidence } from "../actions.js";
import { $, badge, h, icon, replace, roleTag } from "../dom.js";
import { docTypeLabel } from "../format.js";
import { documentOf, findRecord, hasPages, on, state } from "../state.js";

let returnTo = null; // 패널을 닫으면 포커스를 돌려줄 인용 칩

export function citationLabel(citation) {
  const parts = [];
  if (citation.article) parts.push(String(citation.article));
  // 쪽 번호는 PDF에서만 뜻이 있다(txt·md·hwpx는 늘 1쪽으로 온다)
  if (typeof citation.page === "number" && hasPages(citation.doc_id)) parts.push(`${citation.page}쪽`);
  return parts.join(", ");
}

// 사용자가 인용을 눌러서 열 때: 포커스를 패널로 옮기고, 닫으면 누른 곳으로 돌려준다.
export function showEvidence(recordId, index, trigger) {
  returnTo = trigger || null;
  openEvidence(recordId, index);
  $("evidence").focus();
}

function close() {
  const target = returnTo;
  returnTo = null;
  closeEvidence();
  if (target && document.contains(target)) target.focus();
}

function move(step) {
  const current = state.evidence;
  const record = current ? findRecord(current.recordId) : null;
  if (!record || !record.response) return;
  const count = record.response.citations.length;
  const next = current.index + step;
  if (next >= 0 && next < count) openEvidence(record.id, next);
}

function render() {
  const root = $("evidence");
  const current = state.evidence;
  const record = current ? findRecord(current.recordId) : null;
  const citations = record && record.response ? record.response.citations : [];
  const citation = current ? citations[current.index] : null;
  const hadFocus = root.contains(document.activeElement) && document.activeElement !== root;
  document.body.classList.toggle("evidence-open", Boolean(citation));

  if (!citation) {
    replace(
      root,
      h(
        "div",
        { class: "evidence-empty" },
        h("h2", { class: "evidence-heading", text: "근거 원문" }),
        h("p", { text: "답변의 번호를 누르면 그 문장이 나온 원문이 여기에 열립니다." }),
              ),
    );
    return;
  }

  const passages = record.response.passages || [];
  const passage = passages[current.index];
  const text = passage && typeof passage.text === "string" ? passage.text : null;
  const doc = documentOf(citation.doc_id);
  const where = citationLabel(citation);
  const index = current.index;

  const numbers = citations.map((item, i) =>
    h(
      "button",
      {
        class: "cite cite-nav",
        type: "button",
        "aria-pressed": String(i === index),
        "aria-label": `근거 ${i + 1}: ${item.title} ${citationLabel(item)}`,
        on: { click: () => openEvidence(record.id, i) },
      },
      String(i + 1),
    ),
  );

  replace(
    root,
    h(
      "header",
      { class: "evidence-head" },
      h("h2", { class: "evidence-heading", text: "근거 원문" }),
      h("button", { class: "btn btn-quiet btn-icon", type: "button", "aria-label": "근거 원문 닫기", on: { click: close } }, icon("close")),
    ),
    h(
      "div",
      { class: "evidence-context" },
      roleTag(record.role, record.levels),
      h("p", { class: "evidence-question", text: record.question }),
    ),
    h(
      "nav",
      { class: "evidence-nav", "aria-label": "이 답변의 근거" },
      h("div", { class: "evidence-numbers" }, numbers),
      h(
        "div",
        { class: "evidence-steps" },
        h("span", { class: "evidence-position", text: `${index + 1} / ${citations.length}` }),
        h("button", { class: "btn btn-quiet btn-icon", type: "button", "aria-label": "이전 근거", disabled: index === 0, on: { click: () => move(-1) } }, icon("prev")),
        h("button", { class: "btn btn-quiet btn-icon", type: "button", "aria-label": "다음 근거", disabled: index === citations.length - 1, on: { click: () => move(1) } }, icon("next")),
      ),
    ),
    h(
      "article",
      { class: "evidence-doc" },
      h(
        "div",
        { class: "evidence-doc-head" },
        h("span", { class: "cite cite-static", "aria-hidden": "true", text: String(index + 1) }),
        h(
          "div",
          { class: "evidence-doc-title" },
          h("h3", { text: citation.title }),
          h(
            "p",
            { class: "evidence-doc-meta" },
            where ? h("span", { class: "evidence-article", text: where }) : null,
            doc ? badge(doc.access_level) : null,
            doc ? h("span", { class: "tag", text: docTypeLabel(doc.doc_type) }) : null,
          ),
        ),
      ),
      text === null
        ? h("p", { class: "evidence-missing", text: "이 인용의 원문을 받지 못했습니다." })
        : h("div", { class: "passage", text }),
      h(
        "dl",
        { class: "evidence-facts" },
        doc && doc.source ? [h("dt", { text: "출처" }), h("dd", { text: doc.source })] : null,
        h("dt", { text: "청크" }),
        h("dd", { text: citation.chunk_id }),
      ),
    ),
  );
  if (hadFocus) {
    const pressed = root.querySelector(".cite-nav[aria-pressed='true']");
    (pressed || root).focus();
  }
  root.scrollTop = 0;
}

export function mountEvidence() {
  const root = $("evidence");
  root.addEventListener("keydown", (event) => {
    if (!state.evidence) return;
    if (event.key === "Escape") {
      close();
      event.preventDefault();
    } else if (event.key === "ArrowLeft") {
      move(-1);
      event.preventDefault();
    } else if (event.key === "ArrowRight") {
      move(1);
      event.preventDefault();
    }
  });
  on("evidence", render);
  render();
}
