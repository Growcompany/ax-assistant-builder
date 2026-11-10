// 시작점: 화면 조각을 붙이고 워크스페이스 목록을 불러온다.

import { checkServer, loadWorkspaces } from "./actions.js";
import { $ } from "./dom.js";
import { startElapsedTicker } from "./format.js";
import { loadSavedRoles, on, state } from "./state.js";
import { mountEvidence } from "./views/evidence.js";
import { mountNewWorkspace, openNewWorkspace } from "./views/new-workspace.js";
import { mountSidebar } from "./views/sidebar.js";
import { renderWelcome } from "./views/welcome.js";
import { mountWorkspaceEvents, renderWorkspace, unmountWorkspace } from "./views/workspace.js";

const SERVER_CHECK_MS = 15000;

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
    renderWelcome(view, openNewWorkspace);
  }
  syncLayout();
}

loadSavedRoles();
mountSidebar(openNewWorkspace);
mountNewWorkspace();
mountEvidence();
mountWorkspaceEvents();
startElapsedTicker();

on("current", renderView);
on("tab", syncLayout);
on("workspaces", () => {
  // 고른 워크스페이스가 없을 때만 첫 화면을 다시 그린다. 목록이 있으면 곧 하나가 선택된다
  if (!state.currentId && (state.workspaces.length === 0 || state.listError)) renderView();
  else syncLayout();
});

renderView();
loadWorkspaces();
checkServer();
window.setInterval(checkServer, SERVER_CHECK_MS);
