// 워크스페이스 화면: 머리(이름·업종·색인 상태·색인 버튼)와 탭 두 개(질문, 문서).

import { buildIndex, loadWorkspaces, refreshDetail, setTab } from "../actions.js";
import { errorNotice, h, icon, replace } from "../dom.js";
import { describeError } from "../errors.js";
import { on, state } from "../state.js";
import { createAskView } from "./ask.js";
import { createDocumentsView } from "./documents.js";
import { indexMark } from "./sidebar.js";

let mounted = null; // { id, head, tabs, ask, documents }

const TABS = [
  { key: "ask", label: "질문" },
  { key: "documents", label: "문서" },
];

function summary() {
  return state.detail || state.workspaces.find((workspace) => workspace.id === state.currentId) || null;
}

export function indexButton(id, variant) {
  const info = summary();
  const indexState = info && info.index ? info.index.state : "none";
  const busy = Boolean(state.indexing[id]);
  const noDocuments = !info || !info.documents;
  const primary = variant === "primary" || indexState !== "ready";
  return h(
    "button",
    {
      class: `btn ${primary ? "btn-primary" : ""} index-button`,
      type: "button",
      disabled: busy || noDocuments,
      title: noDocuments ? "문서를 먼저 올리세요" : null,
      on: { click: () => buildIndex(id) },
    },
    busy ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon("refresh"),
    busy ? "색인 만드는 중" : indexState === "none" ? "색인 만들기" : "색인 다시 만들기",
  );
}

function renderHead() {
  if (!mounted) return;
  const id = mounted.id;
  const info = summary();
  const hadFocus = mounted.head.contains(document.activeElement);
  const busy = state.indexing[id];
  const failure = state.indexError[id];

  replace(
    mounted.head,
    h(
      "div",
      { class: "ws-head-row" },
      h(
        "div",
        { class: "ws-head-title" },
        h("h1", { class: "ws-title", text: info ? info.name || id : id }),
        info && info.industry ? h("span", { class: "tag", text: info.industry }) : null,
        h("span", { class: "ws-id", text: id }),
      ),
      h(
        "div",
        { class: "ws-head-index" },
        info ? indexMark(info.index, true) : null,
        indexButton(id, "auto"),
      ),
    ),
    busy
      ? h(
          "div",
          { class: "progress-block", role: "status" },
          h("div", { class: "progress-bar", "aria-hidden": "true" }, h("span")),
          h(
            "p",
            { class: "progress-text" },
            "문서를 읽고 색인을 만드는 중입니다. 문서가 많으면 수십 초가 걸립니다. 경과 ",
            h("span", { class: "elapsed", data: { since: busy.startedAt }, text: "0초" }),
          ),
        )
      : null,
    failure && !busy
      ? errorNotice(
          describeError(failure, "index"),
          h("button", { class: "btn", type: "button", on: { click: () => buildIndex(id) } }, icon("refresh"), "다시 시도"),
        )
      : null,
  );
  if (hadFocus) {
    const button = mounted.head.querySelector(".index-button:not(:disabled)");
    if (button) button.focus();
  }
}

function renderTabs() {
  if (!mounted) return;
  const info = summary();
  for (const tab of TABS) {
    const button = mounted.tabButtons[tab.key];
    const selected = state.tab === tab.key;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    mounted[tab.key].root.hidden = !selected;
  }
  const count = info ? info.documents ?? 0 : 0;
  mounted.docCount.textContent = String(count);
  mounted.docCount.setAttribute("aria-label", `${count}개`);
}

function renderDetailError() {
  if (!mounted) return;
  if (state.detailError && !state.detail) {
    replace(
      mounted.problem,
      errorNotice(
        describeError(state.detailError, "load"),
        h(
          "button",
          {
            class: "btn",
            type: "button",
            on: { click: () => (state.detailError.status === 404 ? loadWorkspaces() : refreshDetail()) },
          },
          icon("refresh"),
          "다시 불러오기",
        ),
      ),
    );
    mounted.problem.hidden = false;
  } else {
    replace(mounted.problem);
    mounted.problem.hidden = true;
  }
}

function syncAll() {
  renderHead();
  renderTabs();
  renderDetailError();
  mounted.ask.sync();
  mounted.documents.sync();
}

export function renderWorkspace(container) {
  const id = state.currentId;
  const head = h("header", { class: "ws-head" });
  const problem = h("div", { class: "ws-problem", hidden: true });
  const docCount = h("span", { class: "tab-count", text: "0" });
  const tabButtons = {};
  const tablist = h("div", { class: "tabs", role: "tablist", "aria-label": "워크스페이스 화면" });
  for (const tab of TABS) {
    const button = h(
      "button",
      {
        class: "tab",
        type: "button",
        role: "tab",
        id: `tab-${tab.key}`,
        "aria-controls": `panel-${tab.key}`,
        on: { click: () => setTab(tab.key) },
      },
      tab.label,
      tab.key === "documents" ? docCount : null,
    );
    tabButtons[tab.key] = button;
    tablist.appendChild(button);
  }
  tablist.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const next = state.tab === "ask" ? "documents" : "ask";
    setTab(next);
    tabButtons[next].focus();
    event.preventDefault();
  });

  mounted = {
    id,
    head,
    problem,
    docCount,
    tabButtons,
    ask: createAskView(id),
    documents: createDocumentsView(id),
  };
  replace(container, head, problem, tablist, mounted.ask.root, mounted.documents.root);
  syncAll();
}

export function unmountWorkspace() {
  mounted = null;
}

export function mountWorkspaceEvents() {
  const whenMounted = (run) => () => {
    if (mounted && mounted.id === state.currentId) run();
  };
  on("detail", whenMounted(syncAll));
  on("index", whenMounted(syncAll));
  on("role", whenMounted(() => mounted.ask.sync()));
  on("tab", whenMounted(renderTabs));
  on("evidence", whenMounted(() => mounted.ask.syncPressed()));
  on("conversation", (payload) => {
    if (mounted && payload.record.workspaceId === mounted.id) mounted.ask.onConversation(payload);
  });
}
