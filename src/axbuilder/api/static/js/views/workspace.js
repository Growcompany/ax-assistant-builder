// 워크스페이스 화면: 머리(이름·업종·색인 상태·색인 버튼)와 탭 두 개(질문, 문서).

import { buildIndex, loadModels, loadWorkspaces, refreshDetail, setModel, setTab } from "../actions.js";
import { announce, errorNotice, h, icon, replace } from "../dom.js";
import { describeError } from "../errors.js";
import { modelStatus, on, state } from "../state.js";
import { createAskView } from "./ask.js";
import { createConnectView } from "./connect.js";
import { createDocumentsView } from "./documents.js";
import { indexMark } from "./sidebar.js";

let mounted = null; // { id, head, tabs, ask, documents }

const TABS = [
  { key: "ask", label: "질문" },
  { key: "documents", label: "문서" },
  { key: "connect", label: "연결" },
];

const DEFAULT_OPTION = ""; // 모델 선택 상자에서 "서버 기본"을 뜻하는 값
let modelPending = false;
let modelFailure = null;

// 머리의 모델 선택 상자. 목록은 GET /models, 지금 값은 워크스페이스의 llm_model(없으면 서버 기본).
function modelPicker(id, info) {
  const status = modelStatus();
  const current = info && typeof info.llm_model === "string" ? info.llm_model : null;
  if (status.cloud) {
    return h("p", { class: "model-pick" }, h("span", { class: "model-pick-label", text: "답하는 모델" }), h("span", { class: "tag", text: status.fallback }), h("span", { class: "field-hint", text: "클라우드 모드에서는 바꿀 수 없습니다" }));
  }
  const names = status.models.map((model) => model.name);
  const options = [h("option", { value: DEFAULT_OPTION, text: status.fallback ? `서버 기본 (${status.fallback})` : "서버 기본" })];
  for (const name of names) options.push(h("option", { value: name, text: name }));
  // 고른 모델이 지금 설치 목록에 없어도 값은 보여 준다
  if (current && !names.includes(current)) options.push(h("option", { value: current, text: `${current} (설치되어 있지 않음)` }));
  const select = h("select", { class: "model-select", id: "ws-model", disabled: modelPending || !info }, options);
  select.value = current || DEFAULT_OPTION;
  select.addEventListener("change", async () => {
    const next = select.value === DEFAULT_OPTION ? null : select.value;
    modelPending = true;
    modelFailure = null;
    renderHead();
    try {
      await setModel(id, next);
      announce(`답하는 모델을 ${next || `서버 기본(${status.fallback})`}로 바꿨습니다.`);
    } catch (error) {
      modelFailure = error; // 상태를 바꾸지 않았으므로 다시 그리면 이전 값으로 돌아간다
    }
    modelPending = false;
    renderHead();
    mounted?.head.querySelector("#ws-model")?.focus();
  });
  const missing = status.known && status.available && !(current ? names.includes(current) : status.defaultInstalled);
  return h(
    "div",
    { class: "model-pick" },
    h("label", { class: "model-pick-label", for: "ws-model", text: "답하는 모델" }),
    select,
    modelPending ? h("span", { class: "spinner", "aria-hidden": "true" }) : null,
    !status.known || !status.available
      ? h("button", { class: "btn btn-quiet", type: "button", disabled: state.modelsLoading, on: { click: () => loadModels() } }, icon("refresh"), status.known ? "Ollama 꺼짐, 다시 확인" : "모델 목록 다시 확인")
      : null,
    missing ? h("span", { class: "model-missing" }, icon("alert"), "이 모델이 설치되어 있지 않습니다") : null,
  );
}

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
    h(
      "div",
      { class: "ws-head-row ws-head-sub" },
      modelPicker(id, state.detail),
    ),
    modelFailure
      ? errorNotice(describeError(modelFailure, "model"))
      : null,
    busy
      ? h(
          "div",
          { class: "progress-block", role: "status" },
          h("div", { class: "progress-bar", "aria-hidden": "true" }, h("span")),
          h(
            "p",
            { class: "progress-text" },
            "색인을 만드는 중입니다. 경과 ",
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
  if (hadFocus && !mounted.head.contains(document.activeElement)) {
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
  mounted.connect.sync();
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
    const index = TABS.findIndex((tab) => tab.key === state.tab);
    const next = TABS[(index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length].key;
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
    connect: createConnectView(id),
  };
  modelPending = false;
  modelFailure = null;
  replace(container, head, problem, tablist, mounted.ask.root, mounted.documents.root, mounted.connect.root);
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
  on("tab", whenMounted(() => {
    renderTabs();
    mounted.connect.sync(); // 연결 탭을 처음 열 때 키 목록을 읽는다
  }));
  // 30초마다 오는 상태 확인으로 선택 상자가 닫히지 않게, 목록이 바뀌었을 때만 머리를 다시 그린다
  let modelsSignature = "";
  on("models", whenMounted(() => {
    const signature = JSON.stringify([state.models, state.modelsLoading && !state.models]);
    if (signature === modelsSignature) return;
    modelsSignature = signature;
    renderHead();
  }));
  on("evidence", whenMounted(() => mounted.ask.syncPressed()));
  on("conversation", (payload) => {
    if (mounted && payload.record.workspaceId === mounted.id) mounted.ask.onConversation(payload);
  });
}
