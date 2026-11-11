// 화면 상태 한 곳. 바꾼 쪽이 emit(이벤트)을 부르면 구독한 화면이 다시 그린다.
// 대화는 메모리에만 둔다(서버에 저장하지 않으며 새로 고치면 사라진다).

export const state = {
  loaded: false, // 워크스페이스 목록을 한 번이라도 받아 봤는가
  workspaces: [],
  listError: null,
  currentId: null,
  detail: null,
  detailError: null,
  tab: "ask",
  roles: {}, // 워크스페이스 id -> 고른 역할
  conversations: {}, // 워크스페이스 id -> 질문 기록 배열
  asking: {}, // 워크스페이스 id -> 요청 중인가
  indexing: {}, // 워크스페이스 id -> { startedAt }
  indexError: {}, // 워크스페이스 id -> ApiError
  evidence: null, // { recordId, index } 근거 패널에 열린 인용
  server: "unknown", // unknown | up | down
  models: null, // GET /models 결과 { available, default, models, hint }
  modelsError: null,
  modelsLoading: false,
};

const listeners = new Map();

export function on(event, listener) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(listener);
}

export function emit(event, payload) {
  for (const listener of listeners.get(event) || []) listener(payload);
}

export const prefs = {
  get(key) {
    try {
      return window.localStorage.getItem(`axb.${key}`);
    } catch (error) {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value === null) window.localStorage.removeItem(`axb.${key}`);
      else window.localStorage.setItem(`axb.${key}`, value);
    } catch (error) {
      // 사생활 보호 모드 등: 저장하지 못해도 화면은 그대로 동작한다
    }
  },
};

export function conversation(workspaceId) {
  if (!state.conversations[workspaceId]) state.conversations[workspaceId] = [];
  return state.conversations[workspaceId];
}

export function findRecord(recordId) {
  for (const records of Object.values(state.conversations)) {
    const found = records.find((record) => record.id === recordId);
    if (found) return found;
  }
  return null;
}

// 지금 워크스페이스에서 질문에 쓸 역할: 고른 역할이 아직 있으면 그것, 아니면 첫 역할.
export function currentRole() {
  const roles = state.detail ? Object.keys(state.detail.roles || {}) : [];
  const chosen = state.roles[state.currentId];
  if (chosen && roles.includes(chosen)) return chosen;
  return roles.length ? roles[0] : null;
}

export function loadSavedRoles() {
  try {
    const parsed = JSON.parse(prefs.get("roles") || "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      for (const [id, role] of Object.entries(parsed)) {
        if (typeof role === "string") state.roles[id] = role;
      }
    }
  } catch (error) {
    // 깨진 값은 버린다
  }
}

export function setRole(workspaceId, role) {
  state.roles[workspaceId] = role;
  prefs.set("roles", JSON.stringify(state.roles));
  emit("role");
}

// 문서 id -> 문서 정보. 인용(Citation)에는 등급이 없어서 문서 목록에서 찾는다.
export function documentOf(docId) {
  const documents = state.detail ? state.detail.documents_list || [] : [];
  const found = documents.find((doc) => doc.doc_id === docId);
  return found || null;
}

// 문서 id -> 쪽 번호가 뜻이 있는 문서인가. txt·md·hwpx는 쪽이 없어 늘 1이라 보여 주지 않는다.
export function hasPages(docId) {
  const doc = documentOf(docId);
  return Boolean(doc) && String(doc.extension || "").replace(/^\./, "").toLowerCase() === "pdf";
}

// 모델 목록 해석. cloud 모드는 "목록은 읽혔고 안내가 있는데 모델이 기본 모델 하나뿐"인 경우다
// (로컬에서 기본 모델이 설치돼 있으면 서버가 안내를 주지 않는다).
export function modelStatus() {
  const list = state.models;
  if (!list) return { known: false, cloud: false, available: false, models: [], fallback: "", hint: "", defaultInstalled: false };
  const models = Array.isArray(list.models) ? list.models : [];
  const fallback = String(list.default || "");
  const hint = typeof list.hint === "string" ? list.hint : "";
  const cloud = Boolean(list.available) && Boolean(hint) && models.length === 1 && models[0].name === fallback;
  const listed = models.some((model) => model.name === fallback || model.name === `${fallback}:latest`);
  return {
    known: true,
    cloud,
    available: Boolean(list.available),
    models,
    fallback,
    hint,
    defaultInstalled: Boolean(list.available) && (cloud || !hint || listed),
  };
}
