// 질문 탭: 역할 선택, 질문 기록(질문·답변·근거 목록), 입력창.
// 기록은 말풍선이 아니라 "누가(어떤 역할로) 물었고 어디에 근거했는가"를 적는 대장 한 줄이다.

import { ask, buildIndex, cancelAsk, loadWorkspaces, openEvidence, setTab } from "../actions.js";
import { renderAnswer } from "../answer.js";
import { badge, errorNotice, h, icon, replace, roleTag, setFieldError } from "../dom.js";
import { describeError } from "../errors.js";
import { formatLatency } from "../format.js";
import { conversation, currentRole, documentOf, setRole, state } from "../state.js";
import { MAX_QUESTION_CHARS, RULES } from "../validate.js";
import { citationLabel, showEvidence } from "./evidence.js";

// README "데모"의 질문 4개. 같은 질문을 역할만 바꿔 묻는 쌍이 들어 있다.
const DEMO_EXAMPLES = [
  { role: "상담원", question: "AXB 든든 정기예금에 12개월 만기일시지급식으로 가입하면 기본금리는 몇 %인가요?" },
  { role: "상담원", question: "고령 고객의 대리인이 예금을 찾으려면 어떤 서류가 필요한가요?" },
  { role: "준법감시", question: "고령 고객의 대리인이 예금을 찾으려면 어떤 서류가 필요한가요?" },
  { role: "상담원", question: "AXB은행 신용카드의 연회비는 얼마인가요?" },
];

const wide = window.matchMedia("(min-width: 1180px)");

export function createAskView(workspaceId) {
  const banner = h("div", { class: "ask-banner" });
  const emptyState = h("div", { class: "ask-empty" });
  const records = h("ol", { class: "records", "aria-label": "질문 기록" });
  // 질문할 수 없는 상태(색인 없음 등)의 안내. 입력창 대신 보인다
  const gate = h("div", { class: "gate", hidden: true });
  const scroller = h("div", { class: "ask-scroll" }, h("div", { class: "ask-column" }, banner, gate, emptyState, records));

  const roleOptions = h("div", { class: "role-options" });
  const textarea = h("textarea", {
    class: "composer-input",
    id: "question",
    rows: "2",
    maxlength: String(MAX_QUESTION_CHARS),
    placeholder: "문서에 대해 질문하세요",
    "aria-describedby": "question-hint question-error",
  });
  const sendButton = h("button", { class: "btn btn-primary composer-send", type: "submit" }, icon("send"), "질문하기");
  const counter = h("span", { class: "composer-counter", text: `0 / ${MAX_QUESTION_CHARS}` });
  const questionError = h("p", { class: "field-error", id: "question-error", hidden: true });
  const composer = h(
    "form",
    { class: "composer", novalidate: true },
    h("fieldset", { class: "role-picker" }, h("legend", { text: "묻는 역할" }), roleOptions),
    h(
      "div",
      { class: "composer-box" },
      h("label", { class: "sr-only", for: "question", text: "질문" }),
      textarea,
      sendButton,
    ),
    h(
      "div",
      { class: "composer-meta" },
      h("span", { id: "question-hint", text: "Enter로 보내기, Shift+Enter로 줄바꿈" }),
      counter,
    ),
    questionError,
  );
  const foot = h("div", { class: "ask-foot" }, h("div", { class: "ask-column" }, composer));
  const root = h(
    "section",
    { class: "panel panel-ask", id: "panel-ask", role: "tabpanel", "aria-labelledby": "tab-ask" },
    scroller,
    foot,
  );

  const elements = new Map(); // 기록 id -> li
  let roleSignature = "";

  // ---- 보내기 ----

  function submit(question, role, retryRecord) {
    if (state.asking[workspaceId]) return;
    ask(workspaceId, question, role, retryRecord);
  }

  function submitComposer() {
    const question = textarea.value.trim();
    const role = currentRole();
    if (!question) {
      setFieldError(textarea, questionError, "질문을 적으세요.");
      textarea.focus();
      return;
    }
    if (question.length > MAX_QUESTION_CHARS) {
      setFieldError(textarea, questionError, RULES.question);
      return;
    }
    if (!role || state.asking[workspaceId]) return;
    setFieldError(textarea, questionError, "");
    textarea.value = "";
    updateCounter();
    submit(question, role);
  }

  function updateCounter() {
    counter.textContent = `${textarea.value.length} / ${MAX_QUESTION_CHARS}`;
  }

  composer.addEventListener("submit", (event) => {
    event.preventDefault();
    submitComposer();
  });
  textarea.addEventListener("input", () => {
    updateCounter();
    if (textarea.value.trim()) setFieldError(textarea, questionError, "");
  });
  textarea.addEventListener("keydown", (event) => {
    // 한글 조합 중의 Enter는 글자 확정이다. 보내지 않는다
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    submitComposer();
  });

  // ---- 기록 한 건 ----

  function citeChip(record, number) {
    const citation = record.response.citations[number - 1];
    return h(
      "button",
      {
        class: "cite",
        type: "button",
        "aria-pressed": "false",
        "aria-label": `근거 ${number} 원문 보기: ${citation.title} ${citationLabel(citation)}`,
        data: { record: record.id, cite: number - 1 },
        on: { click: (event) => showEvidence(record.id, number - 1, event.currentTarget) },
      },
      String(number),
    );
  }

  function citationRow(record, citation, index) {
    const doc = documentOf(citation.doc_id);
    const where = citationLabel(citation);
    return h(
      "li",
      null,
      h(
        "button",
        {
          class: "source",
          type: "button",
          "aria-pressed": "false",
          data: { record: record.id, cite: index },
          on: { click: (event) => showEvidence(record.id, index, event.currentTarget) },
        },
        h("span", { class: "source-number", "aria-hidden": "true", text: String(index + 1) }),
        h("span", { class: "sr-only", text: `근거 ${index + 1}` }),
        h(
          "span",
          { class: "source-main" },
          h("span", { class: "source-title", text: citation.title }),
          where ? h("span", { class: "source-article", text: where }) : null,
        ),
        doc ? badge(doc.access_level) : null,
        h("span", { class: "source-open" }, "원문", icon("next")),
      ),
    );
  }

  // 같은 질문을 다른 역할로 물은 앞선 기록(이 제품의 핵심 시연: 역할에 따라 답이 달라진다).
  function earlierWithOtherRole(record) {
    const list = conversation(workspaceId);
    for (let i = list.indexOf(record) - 1; i >= 0; i -= 1) {
      const other = list[i];
      if (other.status === "done" && other.role !== record.role && other.question === record.question) {
        return other;
      }
    }
    return null;
  }

  // 결과 한 줄 요약. 인용한 문서의 등급을 세어 역할에 따른 차이가 보이게 한다.
  function outcome(record) {
    if (record.response.refused) return "답하지 않음";
    const citations = record.response.citations;
    if (!citations.length) return "근거 없이 답함";
    const internal = citations.filter((citation) => {
      const doc = documentOf(citation.doc_id);
      return doc && doc.access_level === "internal";
    }).length;
    const mix = internal ? `그중 internal 문서 ${internal}건` : "모두 public 문서";
    return `근거 ${citations.length}건, ${mix}`;
  }

  function compareStrip(record) {
    const other = earlierWithOtherRole(record);
    if (!other) return null;
    const side = (item, note) =>
      h(
        "li",
        { class: "compare-side" },
        roleTag(item.role, item.levels),
        h("span", { class: "compare-result", text: outcome(item) }),
        note,
      );
    return h(
      "div",
      { class: "compare" },
      h("p", { class: "compare-title", text: "같은 질문을 다른 역할로 물었습니다" }),
      h(
        "ul",
        { class: "compare-sides" },
        side(
          other,
          h(
            "button",
            {
              class: "btn btn-quiet compare-jump",
              type: "button",
              on: {
                click: () => {
                  const target = elements.get(other.id);
                  if (!target) return;
                  target.scrollIntoView({ block: "start" });
                  target.focus();
                },
              },
            },
            "그 답 보기",
          ),
        ),
        side(record, h("span", { class: "compare-here", text: "이 답" })),
      ),
    );
  }

  function reaskButtons(record) {
    const roles = state.detail ? Object.keys(state.detail.roles || {}) : [];
    const others = roles.filter((role) => role !== record.role).slice(0, 3);
    if (!others.length) return null;
    return h(
      "div",
      { class: "reask" },
      h("span", { class: "reask-label", text: "다른 역할로 다시 묻기" }),
      others.map((role) =>
        h(
          "button",
          {
            class: "btn btn-quiet reask-button",
            type: "button",
            disabled: Boolean(state.asking[workspaceId]),
            on: {
              click: () => {
                setRole(workspaceId, role); // 이 뒤 질문도 이 역할로 간다. 선택 표시를 함께 바꾼다
                submit(record.question, role);
              },
            },
          },
          role,
        ),
      ),
    );
  }

  function answerBody(record) {
    const response = record.response;
    const count = response.citations.length;
    const parts = [compareStrip(record)];
    if (response.refused) {
      parts.push(
        h(
          "div",
          { class: "refusal" },
          h("p", { class: "refusal-label" }, h("span", { class: "refusal-mark", "aria-hidden": "true" }), "근거 없음 — 답하지 않음"),
          renderAnswer(response.answer, count, (number) => citeChip(record, number)),
          h("p", { class: "refusal-note", text: `${record.role} 역할이 볼 수 있는 문서에서 근거를 찾지 못했습니다.` }),
        ),
      );
    } else {
      parts.push(renderAnswer(response.answer, count, (number) => citeChip(record, number)));
    }
    if (count) {
      parts.push(
        h(
          "div",
          { class: "sources" },
          h("h4", { class: "sources-title", text: `근거 ${count}건` }),
          h("ol", { class: "sources-list" }, response.citations.map((citation, index) => citationRow(record, citation, index))),
        ),
      );
    } else if (!response.refused) {
      parts.push(h("p", { class: "no-sources" }, icon("alert"), "인용이 붙지 않은 답입니다. 어느 문서에서 왔는지 확인할 수 없으니 그대로 믿지 마세요."));
    }
    const latency = formatLatency(response.latency_ms);
    parts.push(
      h(
        "div",
        { class: "record-foot" },
        h(
          "span",
          { class: "answer-meta" },
          latency ? h("span", { class: "latency", text: `응답 ${latency}` }) : null,
          response.model ? h("span", { class: "answer-model" }, "모델 ", h("span", { class: "tag", text: response.model })) : null,
        ),
        reaskButtons(record),
      ),
    );
    return parts;
  }

  function errorBody(record) {
    const info = describeError(record.error, "ask");
    const retry = h(
      "button",
      { class: "btn retry-button", type: "button", disabled: Boolean(state.asking[workspaceId]), on: { click: () => submit(record.question, record.role, record) } },
      icon("refresh"),
      "다시 시도",
    );
    const reindex =
      info.action === "reindex"
        ? h("button", { class: "btn btn-primary", type: "button", on: { click: () => buildIndex(workspaceId) } }, "색인 다시 만들기")
        : null;
    const reload =
      info.action === "reload"
        ? h("button", { class: "btn", type: "button", on: { click: () => loadWorkspaces() } }, "목록 다시 불러오기")
        : null;
    return errorNotice(info, reindex, reload, retry);
  }

  function pendingBody(record) {
    return h(
      "div",
      { class: "pending", role: "status" },
      h("div", { class: "progress-bar", "aria-hidden": "true" }, h("span")),
      h(
        "p",
        { class: "progress-text" },
        `${record.role} 역할이 볼 수 있는 문서에서 근거를 찾아 답을 쓰는 중입니다. 경과 `,
        h("span", { class: "elapsed", data: { since: record.startedAt }, text: "0초" }),
      ),
      h(
        "div",
        null,
        h("button", { class: "btn cancel-button", type: "button", on: { click: () => cancelAsk(record) } }, icon("close"), "질문 취소"),
      ),
    );
  }

  function cancelledBody(record) {
    return h(
      "div",
      { class: "cancelled" },
      h("p", { class: "cancelled-title", text: "질문을 취소했습니다" }),
      h("p", { class: "notice-text", text: "화면은 답을 기다리지 않습니다. 서버가 이미 쓰기 시작한 답은 끝날 때까지 계속될 수 있습니다." }),
      h(
        "div",
        null,
        h(
          "button",
          { class: "btn retry-button", type: "button", disabled: Boolean(state.asking[workspaceId]), on: { click: () => submit(record.question, record.role, record) } },
          icon("refresh"),
          "다시 묻기",
        ),
      ),
    );
  }

  function renderRecord(record) {
    let body;
    if (record.status === "done") body = answerBody(record);
    else if (record.status === "cancelled") body = cancelledBody(record);
    else if (record.status === "error") body = errorBody(record);
    else body = pendingBody(record);
    return h(
      "li",
      { class: "record", id: `record-${record.id}`, tabindex: "-1", data: { status: record.status } },
      h("div", { class: "record-gutter" }, roleTag(record.role, record.levels)),
      h(
        "div",
        { class: "record-main" },
        h("h3", { class: "record-question", text: record.question }),
        h("div", { class: "record-answer" }, body),
      ),
    );
  }

  function putRecord(record) {
    const next = renderRecord(record);
    const previous = elements.get(record.id);
    const hadFocus = Boolean(previous) && previous.contains(document.activeElement);
    if (previous && previous.parentNode === records) previous.replaceWith(next);
    else records.appendChild(next);
    elements.set(record.id, next);
    // 취소·다시 시도 버튼을 누르면 그 기록이 통째로 바뀐다. 포커스를 잃지 않게 기록으로 옮긴다
    if (hadFocus) (next.querySelector(".cancel-button, .retry-button") || next).focus();
    return next;
  }

  // ---- 상태에 맞춰 다시 그리기 ----

  function syncPressed() {
    const open = state.evidence;
    for (const node of root.querySelectorAll("[data-cite]")) {
      const pressed = Boolean(open) && Number(node.dataset.record) === open.recordId && Number(node.dataset.cite) === open.index;
      node.setAttribute("aria-pressed", String(pressed));
    }
  }

  function renderRoles(detail) {
    const roles = Object.entries(detail.roles || {});
    const selected = currentRole();
    const signature = JSON.stringify([roles, selected]);
    if (signature === roleSignature) return;
    roleSignature = signature;
    const hadFocus = roleOptions.contains(document.activeElement);
    replace(
      roleOptions,
      roles.map(([role, levels]) =>
        h(
          "label",
          { class: "role-option" },
          h("input", {
            type: "radio",
            name: "ask-role",
            value: role,
            checked: role === selected,
            on: { change: () => setRole(workspaceId, role) },
          }),
          h("span", { class: "role-option-body" }, h("span", { class: "role-name", text: role }), h("span", { class: "role-levels" }, levels.map(badge))),
        ),
      ),
    );
    if (hadFocus) {
      const checked = roleOptions.querySelector("input:checked");
      if (checked) checked.focus();
    }
  }

  function exampleList(detail) {
    if (workspaceId !== "demo") return null;
    const roles = detail.roles || {};
    return h(
      "div",
      { class: "examples" },
      h("p", { class: "examples-title", text: "예시 질문" }),
      h(
        "ul",
        { class: "examples-list" },
        DEMO_EXAMPLES.map((example) => {
          const role = example.role in roles ? example.role : currentRole();
          return h(
            "li",
            null,
            h(
              "button",
              {
                class: "example",
                type: "button",
                disabled: Boolean(state.asking[workspaceId]),
                on: {
                  click: () => {
                    setRole(workspaceId, role);
                    submit(example.question, role);
                  },
                },
              },
              roleTag(role, roles[role] || []),
              h("span", { class: "example-question", text: example.question }),
            ),
          );
        }),
      ),
    );
  }

  function renderGate(detail) {
    const id = workspaceId;
    if (!detail) {
      replace(gate, state.detailError ? null : h("p", { class: "gate-text", text: "워크스페이스를 불러오는 중입니다." }));
      return true;
    }
    if (!Object.keys(detail.roles || {}).length) {
      replace(gate, h("p", { class: "gate-title", text: "이 워크스페이스에는 역할이 없어 질문할 수 없습니다" }), h("p", { class: "gate-text", text: "역할이 있는 워크스페이스를 새로 만드세요." }));
      return true;
    }
    if (detail.index && detail.index.state !== "none") return false;
    if (!detail.documents) {
      replace(
        gate,
        h("p", { class: "gate-title", text: "먼저 문서를 올리세요" }),
        h("p", { class: "gate-text", text: "문서를 올리고 색인을 만들면 질문할 수 있습니다." }),
        h("button", { class: "btn btn-primary", type: "button", on: { click: () => setTab("documents") } }, "문서 탭으로 가기"),
      );
      return true;
    }
    const busy = state.indexing[id];
    replace(
      gate,
      h("p", { class: "gate-title", text: "먼저 색인을 만드세요" }),
      h("p", { class: "gate-text", text: `올린 문서 ${detail.documents}개를 검색할 수 있게 색인을 만들어야 질문할 수 있습니다. 수십 초가 걸릴 수 있습니다.` }),
      h(
        "button",
        { class: "btn btn-primary", type: "button", disabled: Boolean(busy), on: { click: () => buildIndex(id) } },
        busy ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon("refresh"),
        busy ? "색인 만드는 중" : "색인 만들기",
      ),
    );
    return true;
  }

  function renderBanner(detail) {
    if (!detail || !detail.index || detail.index.state !== "stale") {
      replace(banner);
      return;
    }
    const waiting = (detail.documents_list || []).filter((doc) => !doc.indexed).length;
    const busy = state.indexing[workspaceId];
    replace(
      banner,
      h(
        "div",
        { class: "notice notice-warn", role: "status" },
        h("span", { class: "index-mark", data: { state: "stale" } }, h("span", { class: "index-dot", "aria-hidden": "true" })),
        h(
          "div",
          { class: "notice-body" },
          h("p", {
            class: "notice-title",
            text: waiting
              ? `색인 뒤 올린 문서 ${waiting}개는 아직 답에 반영되지 않습니다.`
              : "색인이 지금 문서와 맞는지 확인할 수 없습니다. 다시 만들면 모든 문서가 답에 반영됩니다.",
          }),
          h("p", { class: "notice-text", text: "질문은 지금도 할 수 있습니다. 답은 마지막으로 색인한 문서에서만 찾습니다." }),
        ),
        h(
          "button",
          { class: "btn btn-primary", type: "button", disabled: Boolean(busy), on: { click: () => buildIndex(workspaceId) } },
          busy ? "색인 만드는 중" : "색인 다시 만들기",
        ),
      ),
    );
  }

  function sync() {
    const detail = state.detail;
    const gated = renderGate(detail);
    const hadFocus = composer.contains(document.activeElement);
    gate.hidden = !gated;
    foot.hidden = gated;
    renderBanner(detail);
    if (detail && !gated) renderRoles(detail);

    const asking = Boolean(state.asking[workspaceId]);
    sendButton.disabled = asking;
    for (const button of records.querySelectorAll(".reask-button, .retry-button")) button.disabled = asking;

    const empty = conversation(workspaceId).length === 0;
    if (empty && detail && !gated) {
      replace(
        emptyState,
        h("h2", { class: "ask-empty-title", text: "질문하면 문서의 조항을 인용해 답합니다" }),
        h("p", { class: "ask-empty-text", text: "아래에서 역할을 고르세요. 역할이 볼 수 있는 등급의 문서에서만 답을 찾고, 근거가 없으면 답하지 않습니다." }),
        exampleList(detail),
      );
      emptyState.hidden = false;
    } else {
      replace(emptyState);
      emptyState.hidden = true;
    }
    if (hadFocus && gated) gate.querySelector("button")?.focus();
  }

  function onConversation({ record, added }) {
    const element = putRecord(record);
    sync();
    syncPressed();
    if (added) {
      scroller.scrollTop = scroller.scrollHeight;
      return;
    }
    if (record.status === "pending") return;
    element.scrollIntoView({ block: "nearest" });
    // 넓은 화면에서는 답이 오면 첫 근거의 원문을 바로 옆에 펼친다(포커스는 옮기지 않는다)
    const visible = state.currentId === workspaceId && state.tab === "ask";
    if (record.status === "done" && record.response.citations.length && visible && wide.matches) {
      openEvidence(record.id, 0);
    }
  }

  for (const record of conversation(workspaceId)) putRecord(record);

  return { root, sync, syncPressed, onConversation };
}
