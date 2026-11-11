// 시작점: 화면 조각을 붙이고 워크스페이스 목록을 불러온다.

import { checkServer, loadModels, loadWorkspaces } from "./actions.js";
import { $ } from "./dom.js";
import { startElapsedTicker } from "./format.js";
import { loadSavedRoles, on, state } from "./state.js";
import { mountEvidence } from "./views/evidence.js";
import { mountQuickCreate, openQuickCreate } from "./views/quick-create.js";
import { mountSidebar } from "./views/sidebar.js";
import { renderWelcome, renderWelcomeNotice } from "./views/welcome.js";
import { mountWorkspaceEvents, renderWorkspace, unmountWorkspace } from "./views/workspace.js";

// 서버·Ollama 상태 확인 간격. 탭이 숨겨져 있으면 쉰다(보이게 되면 바로 한 번 확인)
const STATUS_CHECK_MS = 30000;
let lastStatusCheck = 0;

function checkStatus() {
  if (document.hidden) return;
  lastStatusCheck = Date.now();
  checkServer();
  loadModels();
}

// 근거 패널 열은 질문 탭에서만 쓴다
function syncLayout() {
  const showEvidence = Boolean(state.currentId) && state.tab === "ask";
  $("app").dataset.evidence = showEvidence ? "on" : "off";
  const current = state.workspaces.find((workspace) => workspace.id === state.currentId);
  const name = current ? current.name || current.id : "";
  document.title = name ? `${name} | AX Assistant Builder` : "AX Assistant Builder";
}

function renderView() {
  const view = $("view");
  if (state.currentId) {
    view.classList.remove("view-welcome");
    renderWorkspace(view);
  } else {
    unmountWorkspace();
    view.classList.add("view-welcome");
    renderWelcome(view, openQuickCreate);
  }
  syncLayout();
}

loadSavedRoles();
mountSidebar(openQuickCreate);
mountQuickCreate();
mountEvidence();
mountWorkspaceEvents();
startElapsedTicker();

on("current", renderView);
on("tab", syncLayout);
on("models", () => {
  // 첫 화면의 모델 안내(Ollama 꺼짐, 모델 없음)를 최신으로
  if (!state.currentId && state.loaded && !state.listError) renderWelcomeNotice();
});

on("workspaces", () => {
  // 고른 워크스페이스가 없을 때만 첫 화면을 다시 그린다. 목록이 있으면 곧 하나가 선택된다
  if (!state.currentId && (state.workspaces.length === 0 || state.listError)) renderView();
  else syncLayout();
});

renderView();
loadWorkspaces();
checkStatus();
window.setInterval(checkStatus, STATUS_CHECK_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Date.now() - lastStatusCheck >= STATUS_CHECK_MS) checkStatus();
});
