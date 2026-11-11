// 연결 탭: 이 어시스턴트를 다른 프로그램에서 쓰기 위한 API 키와 복사해 쓰는 예시.
// 키 원문은 만든 직후 대화상자에서 한 번만 보여 준다. 대화상자를 닫으면 화면(DOM)에서 지우고,
// 이 탭의 메모리에만 잠깐 들고 있다가(예시를 복사할 때 채워 넣으려고) 다른 화면으로 가면 버린다.
// 브라우저 저장소 어디에도 저장하지 않는다.

import { api } from "../api.js";
import { $, announce, copyButton, errorNotice, h, icon, replace, roleTag, setFieldError } from "../dom.js";
import { describeError } from "../errors.js";
import { currentRole, state } from "../state.js";

const PLACEHOLDER_KEY = "axb_…";
const QUESTION = "질문을 여기에 적으세요";
// 방금 만든 키를 메모리에 들고 있는 시간. 지나면 잊고, 탭이 숨겨진 채로는 더 빨리 잊는다
const FRESH_KEY_MS = 5 * 60 * 1000;
const FRESH_KEY_HIDDEN_MS = 60 * 1000;

// 지금 키를 들고 있는 연결 탭의 "잊기". 화면에는 연결 탭이 하나뿐이라 하나만 둔다
let forgetFreshKey = null;
let freshTimer = 0;
let hiddenTimer = 0;
let hiddenSince = 0;

function dropFreshKey() {
  window.clearTimeout(freshTimer);
  window.clearTimeout(hiddenTimer);
  const forget = forgetFreshKey;
  forgetFreshKey = null;
  if (forget) forget();
}

document.addEventListener("visibilitychange", () => {
  window.clearTimeout(hiddenTimer);
  if (document.hidden) {
    hiddenSince = Date.now();
    hiddenTimer = window.setTimeout(dropFreshKey, FRESH_KEY_HIDDEN_MS);
  } else if (hiddenSince && Date.now() - hiddenSince >= FRESH_KEY_HIDDEN_MS) {
    // 숨겨진 동안 타이머가 늦게 돌았어도, 다시 보일 때 시간이 지났으면 잊는다
    dropFreshKey();
  }
});
const DATA_DIR_PLACEHOLDER = "<데이터 폴더의 절대 경로>";

function when(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ko-KR", { dateStyle: "medium", timeStyle: "short" });
}

// 예시 코드. key 자리에 무엇을 넣을지는 부르는 쪽이 정한다(화면에는 자리 표시, 복사에는 실제 키).
function examples(origin, workspaceId, role, key) {
  const body = JSON.stringify({ model: workspaceId, messages: [{ role: "user", content: QUESTION }] });
  return {
    curl: [
      `curl ${origin}/v1/chat/completions \\`,
      `  -H "Authorization: Bearer ${key}" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '${body}'`,
    ].join("\n"),
    python: [
      "from openai import OpenAI",
      "",
      `client = OpenAI(base_url="${origin}/v1", api_key="${key}")`,
      "reply = client.chat.completions.create(",
      `    model="${workspaceId}",`,
      `    messages=[{"role": "user", "content": "${QUESTION}"}],`,
      ")",
      "print(reply.choices[0].message.content)",
    ].join("\n"),
    js: [
      `const response = await fetch("${origin}/v1/ask", {`,
      '  method: "POST",',
      "  headers: {",
      `    Authorization: "Bearer ${key}",`,
      '    "Content-Type": "application/json",',
      "  },",
      `  body: JSON.stringify({ question: "${QUESTION}" }),`,
      "});",
      "const data = await response.json();",
      "console.log(data.answer, data.citations);",
    ].join("\n"),
    mcp: JSON.stringify(
      {
        mcpServers: {
          [`axb-${workspaceId}`]: {
            command: "axb",
            args: ["mcp", "--data-dir", DATA_DIR_PLACEHOLDER, "--workspace", workspaceId, "--role", role],
          },
        },
      },
      null,
      2,
    ),
  };
}

const EXAMPLE_TABS = [
  { key: "curl", label: "curl", note: "OpenAI 호환 주소입니다. PowerShell에서는 curl.exe를 쓰세요." },
  { key: "python", label: "Python", note: "openai 패키지의 base_url만 바꿉니다. 인용과 원문은 응답의 axb 필드에 있습니다." },
  { key: "js", label: "JavaScript", note: "다른 사이트의 브라우저 화면에서 직접 부르려면 서버의 AXB_CORS_ORIGINS에 그 사이트 주소를 넣습니다." },
  { key: "mcp", label: "MCP 설정", note: "--data-dir에는 서버가 쓰는 데이터 폴더(기본은 서버를 띄운 폴더의 data)의 절대 경로를 넣습니다. 먼저 pip install -e \".[mcp]\"가 필요합니다. axb serve가 같은 워크스페이스를 쓰는 동안에는 MCP 쪽 질문이 실패할 수 있습니다(색인은 한 프로세스만 엽니다)." },
];

export function createConnectView(workspaceId) {
  let keys = null; // null = 아직 안 읽음
  let loadError = null;
  let loading = false;
  let creating = false;
  let freshKey = null; // { key, prefix, name } 방금 만든 키(메모리에만)
  let exampleTab = "curl";
  let confirming = null; // 폐기 확인 중인 key_id
  let revoking = null;

  const listSlot = h("div", { class: "keys-slot" });
  const freshSlot = h("div", { class: "fresh-key-slot" });
  const exampleSlot = h("div", { class: "examples-slot" });

  const nameInput = h("input", { id: "key-name", type: "text", autocomplete: "off", maxlength: "100", placeholder: "예: 사내 포털", "aria-describedby": "key-name-error" });
  const roleSelect = h("select", { id: "key-role", "aria-describedby": "key-role-hint key-role-error" });
  const nameError = h("p", { class: "field-error", id: "key-name-error", hidden: true });
  const roleError = h("p", { class: "field-error", id: "key-role-error", hidden: true });
  const createButton = h("button", { class: "btn btn-primary", type: "submit" }, icon("key"), "키 만들기");
  const createResult = h("div", { class: "key-create-result" });
  const form = h(
    "form",
    { class: "key-form", novalidate: true, "aria-labelledby": "key-form-title" },
    h("h3", { class: "section-title", id: "key-form-title", text: "새 키" }),
    h(
      "div",
      { class: "field-row" },
      h("div", { class: "field" }, h("label", { for: "key-name", text: "이름" }), nameInput, nameError),
      h(
        "div",
        { class: "field" },
        h("label", { for: "key-role", text: "역할" }),
        roleSelect,
        h("p", { class: "field-hint", id: "key-role-hint", text: "이 키의 질문은 늘 이 역할로 답합니다." }),
        roleError,
      ),
    ),
    h("div", { class: "upload-actions" }, createButton),
    createResult,
  );

  // ---- 키 목록 ----

  async function load() {
    if (loading) return;
    loading = true;
    renderKeys();
    try {
      const list = await api.listKeys(workspaceId);
      keys = Array.isArray(list) ? list : [];
      loadError = null;
    } catch (error) {
      loadError = error;
    }
    loading = false;
    renderKeys();
  }

  async function revoke(key) {
    if (revoking) return;
    revoking = key.key_id;
    renderKeys();
    let failure = null;
    try {
      await api.revokeKey(workspaceId, key.key_id);
    } catch (error) {
      if (error.status !== 404) failure = error; // 이미 없는 키면 폐기된 것으로 본다
    }
    revoking = null;
    confirming = null;
    if (failure) {
      replace(createResult, errorNotice(describeError(failure, "keys")));
    } else {
      if (freshKey && freshKey.keyId === key.key_id) forget();
      announce(`키 ${key.name}을 폐기했습니다.`);
    }
    await load();
    (listSlot.querySelector("button") || nameInput).focus();
  }

  function keyRow(key) {
    const levels = state.detail && state.detail.roles ? state.detail.roles[key.role] || [] : [];
    const asking = confirming === key.key_id;
    const busy = revoking === key.key_id;
    const actions = asking
      ? [
          h("span", { class: "revoke-ask", text: "폐기하면 이 키를 쓰는 곳이 바로 끊깁니다." }),
          h(
            "button",
            { class: "btn btn-danger", type: "button", disabled: busy, on: { click: () => revoke(key) } },
            busy ? h("span", { class: "spinner", "aria-hidden": "true" }) : null,
            busy ? "폐기하는 중" : "폐기",
          ),
          h(
            "button",
            {
              class: "btn",
              type: "button",
              disabled: busy,
              on: {
                click: () => {
                  confirming = null;
                  renderKeys();
                  listSlot.querySelector(`[data-key="${key.key_id}"] button`)?.focus();
                },
              },
            },
            "취소",
          ),
        ]
      : h(
          "button",
          {
            class: "btn btn-quiet revoke-button",
            type: "button",
            "aria-label": `키 ${key.name} 폐기`,
            on: {
              click: () => {
                confirming = key.key_id;
                renderKeys();
                listSlot.querySelector(`[data-key="${key.key_id}"] .btn-danger`)?.focus();
              },
            },
          },
          "폐기",
        );
    return h(
      "tr",
      { data: { key: key.key_id } },
      h("th", { scope: "row", class: "doc-title-cell" }, h("span", { class: "doc-title", text: key.name }), h("span", { class: "doc-sub key-prefix", text: key.prefix || "" })),
      h("td", null, roleTag(key.role, levels)),
      h("td", { text: when(key.created_at) }),
      h("td", { text: key.last_used_at ? when(key.last_used_at) : "아직 쓰지 않음" }),
      h("td", { class: "key-actions" }, actions),
    );
  }

  function renderKeys() {
    if (keys === null) {
      replace(
        listSlot,
        loadError
          ? errorNotice(describeError(loadError, "keys"), h("button", { class: "btn", type: "button", on: { click: load } }, icon("refresh"), "다시 불러오기"))
          : h("p", { class: "progress-line" }, h("span", { class: "spinner", "aria-hidden": "true" }), "키 목록을 불러오는 중입니다."),
      );
      return;
    }
    if (!keys.length) {
      replace(listSlot, h("div", { class: "docs-empty" }, h("p", { class: "gate-title", text: "아직 키가 없습니다" }), h("p", { class: "gate-text", text: "키가 있어야 이 화면 밖에서 질문할 수 있습니다." })));
      return;
    }
    replace(
      listSlot,
      loadError ? errorNotice(describeError(loadError, "keys")) : null,
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          { class: "docs-table keys-table" },
          h("caption", { class: "sr-only", text: "만든 키" }),
          h("thead", null, h("tr", null, ["이름", "역할", "만든 때", "마지막 사용", "폐기"].map((label) => h("th", { scope: "col", text: label })))),
          h("tbody", null, keys.map(keyRow)),
        ),
      ),
    );
  }

  // ---- 키 만들기 ----

  function renderRoleOptions() {
    const roles = state.detail ? Object.entries(state.detail.roles || {}) : [];
    const signature = JSON.stringify(roles);
    if (roleSelect.dataset.signature === signature) return;
    roleSelect.dataset.signature = signature;
    const previous = roleSelect.value;
    replace(
      roleSelect,
      roles.map(([role, levels]) => h("option", { value: role, text: `${role} (${levels.join(", ")})` })),
    );
    const names = roles.map(([role]) => role);
    roleSelect.value = names.includes(previous) ? previous : names[0] || "";
    createButton.disabled = creating || !names.length;
  }

  function forget() {
    freshKey = null;
    if (forgetFreshKey === forget) {
      forgetFreshKey = null;
      window.clearTimeout(freshTimer);
    }
    renderFresh();
    renderExamples();
  }

  function remember(key) {
    dropFreshKey(); // 다른 워크스페이스의 연결 탭이 들고 있던 키도 함께 잊는다
    freshKey = key;
    forgetFreshKey = forget;
    freshTimer = window.setTimeout(dropFreshKey, FRESH_KEY_MS);
  }

  function showKeyDialog(created) {
    const dialog = $("key-dialog");
    $("key-lead").textContent = `"${created.name}" 키입니다. ${created.role} 역할로 답합니다.`;
    $("key-value").textContent = created.key;
    replace($("key-copy"), copyButton(() => created.key, "키 복사"));
    const closeButton = $("key-close");
    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      // 원문은 필요한 동안만 화면에 둔다. 닫는 순간 바로 지운다(close 이벤트를 기다리지 않는다)
      $("key-value").textContent = "";
      replace($("key-copy"));
      dialog.removeEventListener("close", cleanup);
      dialog.removeEventListener("cancel", cleanup);
      closeButton.removeEventListener("click", closeNow);
      (freshSlot.querySelector("button") || nameInput).focus();
    };
    const closeNow = () => {
      dialog.close();
      cleanup();
    };
    dialog.addEventListener("close", cleanup);
    dialog.addEventListener("cancel", cleanup);
    closeButton.addEventListener("click", closeNow);
    dialog.showModal();
    $("key-copy").querySelector("button").focus();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (creating) return;
    replace(createResult);
    const name = nameInput.value.trim();
    const role = roleSelect.value;
    const nameProblem = !name || name.length > 100 || /[\u0000-\u001f\u007f]/.test(name) ? "이름을 한 줄, 1~100자로 적으세요." : "";
    setFieldError(nameInput, nameError, nameProblem);
    setFieldError(roleSelect, roleError, role ? "" : "역할을 고르세요.");
    if (nameProblem || !role) {
      (nameProblem ? nameInput : roleSelect).focus();
      return;
    }
    creating = true;
    createButton.disabled = true;
    try {
      const created = await api.createKey(workspaceId, name, role);
      if (!created || typeof created.key !== "string") throw new Error("no key in response");
      remember({ key: created.key, keyId: created.key_id, name: created.name || name, prefix: `${created.key.slice(0, 8)}…` });
      nameInput.value = "";
      renderFresh();
      renderExamples();
      showKeyDialog({ key: created.key, name: created.name || name, role: created.role || role });
      load();
    } catch (error) {
      const info = describeError(error, "keys");
      setFieldError(nameInput, nameError, info.fields.name || "");
      setFieldError(roleSelect, roleError, info.fields.role || "");
      if (!info.fields.name && !info.fields.role) replace(createResult, errorNotice(info));
      else (info.fields.name ? nameInput : roleSelect).focus();
    } finally {
      creating = false;
      createButton.disabled = false;
    }
  });

  function renderFresh() {
    if (!freshKey) {
      replace(freshSlot);
      return;
    }
    replace(
      freshSlot,
      h(
        "div",
        { class: "notice notice-ok", role: "status" },
        icon("key"),
        h(
          "div",
          { class: "notice-body" },
          h("p", { class: "notice-title", text: `예시를 복사하면 방금 만든 키 "${freshKey.name}"(${freshKey.prefix})가 채워집니다` }),
          h("p", { class: "notice-text", text: "키는 화면에 보이지 않고, 5분이 지나거나 탭을 1분 넘게 가려 두거나 새로 고치면 잊습니다." }),
        ),
        h("button", { class: "btn", type: "button", on: { click: () => { forget(); nameInput.focus(); } } }, "지금 잊기"),
      ),
    );
  }

  // ---- 예시 ----

  function renderExamples() {
    const role = roleSelect.value || currentRole() || "역할";
    const origin = window.location.origin;
    const shown = examples(origin, workspaceId, role, PLACEHOLDER_KEY);
    const tab = EXAMPLE_TABS.find((item) => item.key === exampleTab) || EXAMPLE_TABS[0];
    const hadFocus = exampleSlot.contains(document.activeElement);
    const tablist = h(
      "div",
      { class: "tabs code-tabs", role: "tablist", "aria-label": "예시 종류" },
      EXAMPLE_TABS.map((item) =>
        h(
          "button",
          {
            class: "tab",
            type: "button",
            role: "tab",
            id: `example-tab-${item.key}`,
            "aria-selected": String(item.key === tab.key),
            "aria-controls": "example-panel",
            tabindex: item.key === tab.key ? "0" : "-1",
            on: {
              click: () => {
                exampleTab = item.key;
                renderExamples();
              },
            },
          },
          item.label,
        ),
      ),
    );
    tablist.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      const index = EXAMPLE_TABS.findIndex((item) => item.key === exampleTab);
      const next = (index + (event.key === "ArrowRight" ? 1 : EXAMPLE_TABS.length - 1)) % EXAMPLE_TABS.length;
      exampleTab = EXAMPLE_TABS[next].key;
      renderExamples();
      event.preventDefault();
    });
    const usesKey = tab.key !== "mcp";
    replace(
      exampleSlot,
      tablist,
      h(
        "div",
        { class: "code-panel", id: "example-panel", role: "tabpanel", "aria-labelledby": `example-tab-${tab.key}` },
        h(
          "div",
          { class: "code-head" },
          h("span", {
            class: "field-hint",
            text: !usesKey
              ? "키 없이 이 PC에서 직접 실행합니다."
              : freshKey
                ? `복사하면 방금 만든 키(${freshKey.prefix})가 채워집니다.`
                : `${PLACEHOLDER_KEY} 자리에 키를 넣으세요.`,
          }),
          // 누르는 순간에 만든다: 실제 키는 화면에 두지 않고 복사할 때만 넣는다
          copyButton(() => examples(origin, workspaceId, role, freshKey ? freshKey.key : PLACEHOLDER_KEY)[tab.key], "예시 복사"),
        ),
        h("pre", { class: "code", tabindex: "0" }, h("code", { text: shown[tab.key] })),
        h("p", { class: "code-note", text: tab.note }),
      ),
    );
    if (hadFocus) exampleSlot.querySelector("[role='tab'][aria-selected='true']").focus();
  }

  roleSelect.addEventListener("change", renderExamples);

  const root = h(
    "section",
    { class: "panel panel-connect", id: "panel-connect", role: "tabpanel", "aria-labelledby": "tab-connect", hidden: true },
    h(
      "div",
      { class: "docs-column connect-column" },
      h(
        "div",
        { class: "connect-intro" },
        h("h2", { class: "section-title", text: "다른 프로그램에서 쓰기" }),
        h("p", { class: "gate-text", text: "키 하나는 역할 하나에 묶이고, 그 역할이 볼 수 있는 문서에서만 답합니다." }),
      ),
      listSlot,
      form,
      freshSlot,
      h("div", { class: "connect-examples" }, h("h3", { class: "section-title", text: "예시" }), exampleSlot),
      h(
        "div",
        { class: "connect-remote" },
        h("h3", { class: "section-title", text: "다른 PC에서 쓰려면" }),
        h("p", { class: "gate-text", text: "서버를 바깥 주소로 띄우고, 접속에 쓸 이름이나 IP를 허용 목록에 넣습니다." }),
        h("pre", { class: "code" }, h("code", { text: "AXB_ALLOWED_HOSTS=127.0.0.1,localhost,<이 PC의 IP 또는 이름> axb serve --host 0.0.0.0" })),
        h(
          "ul",
          { class: "plain-list" },
          h("li", { text: "다른 PC에서는 키로 /v1/ 주소만 쓸 수 있습니다. 이 화면과 관리 기능은 이 PC에서만 열립니다." }),
          h("li", { text: "예시의 주소는 이 PC의 IP나 이름으로 바꿔 쓰세요. PowerShell에서는 환경 변수를 $env:AXB_ALLOWED_HOSTS로 먼저 정합니다." }),
        ),
      ),
    ),
  );

  function sync() {
    renderRoleOptions();
    if (state.tab === "connect" && keys === null && !loading && !loadError) load();
    else renderKeys();
    renderExamples();
  }

  dropFreshKey(); // 새 연결 탭이 생기면(다른 워크스페이스로 이동) 앞 탭이 들고 있던 키를 잊는다
  renderFresh();
  return { root, sync };
}
