// 서버 호출. 같은 출처의 상대 경로만 부른다.

export class ApiError extends Error {
  constructor(status, detail) {
    super(`API ${status}`);
    this.status = status; // 0 = 서버에 닿지 못함
    this.detail = detail; // 문자열, pydantic 오류 배열, 또는 null
  }
}

async function request(method, path, options = {}) {
  const init = { method, headers: { Accept: "application/json" } };
  if (options.json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.json);
  } else if (options.form) {
    init.body = options.form; // multipart 경계는 브라우저가 붙인다
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    throw new ApiError(0, null);
  }
  let body = null;
  try {
    const text = await response.text();
    body = text ? JSON.parse(text) : null;
  } catch (error) {
    body = null;
  }
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
    throw new ApiError(response.status, detail);
  }
  return body;
}

const workspacePath = (id) => `/workspaces/${encodeURIComponent(id)}`;

export const api = {
  health: () => request("GET", "/health"),
  listWorkspaces: () => request("GET", "/workspaces"),
  getWorkspace: (id) => request("GET", workspacePath(id)),
  createWorkspace: (workspace) => request("POST", "/workspaces", { json: workspace }),
  createDemo: () => request("POST", "/demo-workspace"),
  uploadDocument: (id, form) => request("POST", `${workspacePath(id)}/documents`, { form }),
  buildIndex: (id) => request("POST", `${workspacePath(id)}/index`),
  ask: (id, question, role) =>
    request("POST", `${workspacePath(id)}/ask`, { json: { question, role } }),
};
