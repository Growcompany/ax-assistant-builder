// 서버를 부르고 상태를 바꾸는 동작들. 화면(views)은 여기 함수만 부른다.
// 같은 요청이 이미 가고 있으면 다시 보내지 않는다.

import { api } from "./api.js";
import { announce } from "./dom.js";
import { conversation, emit, prefs, state } from "./state.js";

let recordSequence = 0;
let demoPending = false;

function pickWorkspace() {
  const ids = state.workspaces.map((workspace) => workspace.id);
  if (state.currentId && ids.includes(state.currentId)) return state.currentId;
  const saved = prefs.get("workspace");
  if (saved && ids.includes(saved)) return saved;
  return ids.length ? ids[0] : null;
}

export async function loadWorkspaces() {
  try {
    const list = await api.listWorkspaces();
    state.workspaces = Array.isArray(list) ? list : [];
    state.listError = null;
  } catch (error) {
    state.listError = error;
  }
  state.loaded = true;
  emit("workspaces");
  if (state.listError) return;
  const next = pickWorkspace();
  if (next !== state.currentId) await selectWorkspace(next);
}

export async function selectWorkspace(id) {
  state.currentId = id;
  state.detail = null;
  state.detailError = null;
  state.evidence = null;
  if (id) prefs.set("workspace", id);
  emit("current");
  emit("evidence");
  if (id) await refreshDetail();
}

export async function refreshDetail() {
  const id = state.currentId;
  if (!id) return;
  try {
    const detail = await api.getWorkspace(id);
    if (state.currentId !== id) return; // 기다리는 사이 다른 워크스페이스로 옮겼다
    state.detail = detail;
    state.detailError = null;
    // 목록의 요약(문서 수·색인 상태)도 같은 값으로 맞춘다
    state.workspaces = state.workspaces.map((workspace) =>
      workspace.id === id ? { ...workspace, ...detail } : workspace,
    );
  } catch (error) {
    if (state.currentId !== id) return;
    state.detailError = error;
  }
  emit("detail");
  emit("workspaces");
}

async function refreshAfterChange(id) {
  if (state.currentId === id) await refreshDetail();
  else await loadWorkspaces();
}

export async function createWorkspace(workspace) {
  const created = await api.createWorkspace(workspace);
  await loadWorkspaces();
  state.tab = "documents"; // 새 워크스페이스는 문서 올리기가 다음 할 일
  await selectWorkspace(created && created.id ? created.id : workspace.id);
  announce(`워크스페이스 ${workspace.name}을 만들었습니다. 문서를 올리세요.`);
}

// 예시 은행 워크스페이스. 이미 있으면(409) 있는 것을 연다.
export async function loadDemo() {
  if (demoPending) return;
  demoPending = true;
  emit("demo");
  let failure = null;
  let id = "demo";
  try {
    const created = await api.createDemo();
    if (created && typeof created.id === "string") id = created.id;
  } catch (error) {
    if (error.status !== 409) failure = error;
  }
  demoPending = false;
  if (!failure) {
    await loadWorkspaces();
    state.tab = "ask";
    await selectWorkspace(id);
    announce("예시 은행 워크스페이스를 불러왔습니다. 색인을 만들면 질문할 수 있습니다.");
  }
  emit("demo");
  if (failure) throw failure;
}

export function isDemoPending() {
  return demoPending;
}

export async function uploadDocument(id, form) {
  const result = await api.uploadDocument(id, form);
  await refreshAfterChange(id);
  return result;
}

export async function buildIndex(id) {
  if (state.indexing[id]) return;
  state.indexing[id] = { startedAt: Date.now() };
  delete state.indexError[id];
  emit("index");
  try {
    const result = await api.buildIndex(id);
    const chunks = result && typeof result.chunks === "number" ? result.chunks : null;
    announce(chunks === null ? "색인을 만들었습니다." : `색인을 만들었습니다. 청크 ${chunks}개.`);
  } catch (error) {
    state.indexError[id] = error;
    announce("색인을 만들지 못했습니다.");
  }
  delete state.indexing[id];
  await refreshAfterChange(id);
  emit("index");
}

// 질문을 보낸다. retryRecord를 주면 실패한 그 기록을 다시 보낸다.
export async function ask(id, question, role, retryRecord) {
  if (state.asking[id]) return null;
  const levels = state.detail && state.detail.roles ? state.detail.roles[role] || [] : [];
  const record = retryRecord || {
    id: (recordSequence += 1),
    workspaceId: id,
    question,
    role,
    levels: [...levels], // 물은 시점에 그 역할이 볼 수 있던 등급
  };
  record.status = "pending";
  record.startedAt = Date.now();
  record.error = null;
  record.response = null;
  if (!retryRecord) conversation(id).push(record);
  state.asking[id] = true;
  emit("conversation", { record, added: !retryRecord });
  try {
    const response = await api.ask(id, record.question, record.role);
    record.response = {
      answer: String((response && response.answer) || ""),
      citations: Array.isArray(response && response.citations) ? response.citations : [],
      passages: Array.isArray(response && response.passages) ? response.passages : [],
      refused: Boolean(response && response.refused),
      latency_ms: response ? response.latency_ms : null,
    };
    record.status = "done";
  } catch (error) {
    record.error = error;
    record.status = "error";
  }
  state.asking[id] = false;
  emit("conversation", { record, added: false });
  if (record.status === "done") {
    const count = record.response.citations.length;
    announce(
      record.response.refused
        ? "답변이 도착했습니다. 근거가 없어 답하지 않았습니다."
        : `답변이 도착했습니다. 근거 ${count}건.`,
    );
  } else {
    announce("답변을 받지 못했습니다.");
    // 색인·역할·워크스페이스 상태가 화면과 달라졌을 수 있다
    if ([400, 404, 409].includes(record.error.status)) await refreshAfterChange(id);
  }
  return record;
}

export function openEvidence(recordId, index) {
  state.evidence = { recordId, index };
  emit("evidence");
}

export function closeEvidence() {
  state.evidence = null;
  emit("evidence");
}

export function setTab(tab) {
  if (state.tab === tab) return;
  state.tab = tab;
  emit("tab");
}

export async function checkServer() {
  let next = "down";
  try {
    const body = await api.health();
    if (body && body.status === "ok") next = "up";
  } catch (error) {
    next = "down";
  }
  if (next !== state.server) {
    const recovered = state.server === "down" && next === "up";
    state.server = next;
    emit("server");
    // 서버가 다시 켜졌으면 못 받았던 목록·상세를 다시 받아 본다
    if (recovered && state.listError) await loadWorkspaces();
    else if (recovered && state.detailError) await refreshDetail();
  }
}
