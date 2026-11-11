// 왼쪽 사이드바: 워크스페이스 목록, 새로 만들기, 서버 상태, 테마 전환.

import { isDemoPending, loadDemo, loadWorkspaces, selectWorkspace } from "../actions.js";
import { $, h, icon, replace } from "../dom.js";
import { describeError } from "../errors.js";
import { modelStatus, on, prefs, state } from "../state.js";

const INDEX_LABELS = { none: "색인 없음", ready: "색인 최신", stale: "다시 색인 필요" };

// 색인 상태 표시: 모양(빈 원·찬 원·반 원)과 글자를 함께 쓴다(색만으로 구분하지 않는다).
export function indexMark(index, long) {
  const known = index && INDEX_LABELS[index.state] ? index.state : "none";
  let label = INDEX_LABELS[known];
  if (long && known !== "none" && index && typeof index.chunks === "number") {
    label = known === "ready" ? `색인 최신, 청크 ${index.chunks}개` : `색인 오래됨, 청크 ${index.chunks}개`;
  }
  return h(
    "span",
    { class: "index-mark", data: { state: known } },
    h("span", { class: "index-dot", "aria-hidden": "true" }),
    h("span", { class: "index-label", text: label }),
  );
}

const narrow = window.matchMedia("(max-width: 860px)");
let demoError = null; // 사이드바의 "예시 은행 불러오기"가 실패한 이유

function setDrawer(open) {
  const sidebar = $("sidebar");
  const opener = $("open-sidebar");
  document.body.classList.toggle("drawer-open", open);
  $("backdrop").hidden = !open;
  opener.setAttribute("aria-expanded", String(open));
  // 좁은 화면에서 닫힌 서랍은 키보드·낭독기에서도 빠져야 한다
  sidebar.inert = narrow.matches && !open;
  if (open) {
    const target = sidebar.querySelector(".ws-item[aria-current='true']") || $("open-new-workspace");
    target.focus();
  }
}

export function closeDrawer(returnFocus) {
  if (!document.body.classList.contains("drawer-open")) return;
  setDrawer(false);
  if (returnFocus) $("open-sidebar").focus();
}

function renderList() {
  const list = $("ws-list");
  const note = $("ws-note");
  replace(
    list,
    state.workspaces.map((workspace) => {
      const current = workspace.id === state.currentId;
      const busy = Boolean(state.indexing[workspace.id]);
      return h(
        "li",
        null,
        h(
          "button",
          {
            class: "ws-item",
            type: "button",
            "aria-current": current ? "true" : null,
            on: {
              click: () => {
                closeDrawer(false);
                if (!current) selectWorkspace(workspace.id);
                $("main").focus();
              },
            },
          },
          h("span", { class: "ws-name", text: workspace.name || workspace.id }),
          h(
            "span",
            { class: "ws-meta" },
            h("span", { text: `문서 ${workspace.documents ?? 0}개` }),
            busy
              ? h("span", { class: "index-mark", data: { state: "busy" } }, h("span", { class: "spinner", "aria-hidden": "true" }), "색인 만드는 중")
              : indexMark(workspace.index, false),
          ),
        ),
      );
    }),
  );

  if (state.listError) {
    const info = describeError(state.listError, "load");
    replace(
      note,
      h("span", { class: "side-note-text", text: `${info.message} ${info.hint}` }),
      h("button", { class: "btn btn-quiet", type: "button", on: { click: () => loadWorkspaces() } }, icon("refresh"), "다시 불러오기"),
    );
    note.hidden = false;
  } else if (demoError) {
    const info = describeError(demoError, "demo");
    replace(note, h("span", { class: "side-note-text", role: "alert", text: `${info.message} ${info.hint}` }));
    note.hidden = false;
  } else if (state.loaded && state.workspaces.length === 0) {
    replace(note, "아직 워크스페이스가 없습니다.");
    note.hidden = false;
  } else {
    note.hidden = true;
  }

  // 예시 워크스페이스가 아직 없을 때만 불러오기 버튼을 보인다
  const demo = $("side-demo");
  const hasDemo = state.workspaces.some((workspace) => workspace.id === "demo");
  demo.hidden = !state.loaded || Boolean(state.listError) || hasDemo || state.workspaces.length === 0;
  demo.disabled = isDemoPending();
}

function renderServer() {
  const texts = {
    unknown: "서버 확인 중",
    up: "서버 연결됨",
    down: "서버에 연결할 수 없음",
  };
  $("server").dataset.state = state.server;
  $("server-text").textContent = texts[state.server];
  renderModelStatus();
}

// Ollama 연결과 기본 모델. 서버가 꺼져 있으면 알 수 없다.
function renderModelStatus() {
  const status = modelStatus();
  const ollama = $("ollama");
  const model = $("default-model");
  const set = (node, textNode, stateName, text) => {
    node.dataset.state = stateName;
    textNode.textContent = text;
  };
  if (state.server === "down" || !status.known) {
    const text = state.server === "down" ? "알 수 없음" : "확인 중";
    set(ollama, $("ollama-text"), "unknown", `Ollama ${text}`);
    set(model, $("default-model-text"), "unknown", `기본 모델 ${text}`);
    return;
  }
  if (status.cloud) {
    set(ollama, $("ollama-text"), "up", "클라우드 모드(Claude)");
    set(model, $("default-model-text"), "up", `모델 ${status.fallback}`);
    return;
  }
  set(ollama, $("ollama-text"), status.available ? "up" : "down", status.available ? "Ollama 연결됨" : "Ollama 꺼짐");
  if (!status.available) set(model, $("default-model-text"), "unknown", `기본 모델 ${status.fallback}`);
  else if (status.defaultInstalled) set(model, $("default-model-text"), "up", `기본 모델 ${status.fallback}`);
  else set(model, $("default-model-text"), "down", `기본 모델 ${status.fallback} 없음`);
}

function effectiveTheme() {
  // 밝은 화면이 기본이다. 어두운 화면은 사용자가 골랐을 때만
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function renderThemeToggle() {
  const dark = effectiveTheme() === "dark";
  const button = $("theme-toggle");
  button.setAttribute("aria-label", dark ? "밝은 화면으로 바꾸기" : "어두운 화면으로 바꾸기");
  button.title = dark ? "밝은 화면" : "어두운 화면";
  replace(button, icon(dark ? "sun" : "moon"));
}

export function mountSidebar(openNewWorkspace) {
  $("open-new-workspace").addEventListener("click", () => {
    closeDrawer(false);
    openNewWorkspace();
  });
  $("side-demo").addEventListener("click", () => {
    demoError = null;
    loadDemo().catch((error) => {
      demoError = error;
      renderList();
    });
  });
  $("theme-toggle").addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    if (next === "dark") document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");
    prefs.set("theme", next);
    renderThemeToggle();
  });

  $("open-sidebar").addEventListener("click", () => setDrawer(true));
  $("backdrop").addEventListener("click", () => closeDrawer(true));
  $("sidebar").addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer(true);
  });
  const syncDrawer = () => {
    if (!narrow.matches) {
      document.body.classList.remove("drawer-open");
      $("backdrop").hidden = true;
    }
    $("sidebar").inert = narrow.matches && !document.body.classList.contains("drawer-open");
  };
  narrow.addEventListener("change", syncDrawer);
  syncDrawer();

  on("workspaces", renderList);
  on("current", renderList);
  on("index", renderList);
  on("demo", renderList);
  on("server", renderServer);
  on("models", renderModelStatus);
  renderList();
  renderServer();
  renderThemeToggle();
}
