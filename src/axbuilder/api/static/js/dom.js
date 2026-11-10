// DOM을 만드는 작은 도우미. 글자는 언제나 텍스트 노드로 들어간다(마크업으로 해석되지 않는다).

const BOOLEAN_PROPS = new Set(["disabled", "hidden", "checked", "required", "open", "readOnly"]);

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else if (child instanceof Node) node.appendChild(child);
    else node.appendChild(document.createTextNode(String(child)));
  }
}

export function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "value") node.value = value;
    else if (key === "on") {
      for (const [type, listener] of Object.entries(value)) node.addEventListener(type, listener);
    } else if (key === "data") {
      for (const [name, item] of Object.entries(value)) node.dataset[name] = String(item);
    } else if (BOOLEAN_PROPS.has(key)) node[key] = true;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  append(node, children);
  return node;
}

export function replace(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

export function icon(name) {
  return h("span", { class: `icon icon-${name}`, "aria-hidden": "true" });
}

export function $(id) {
  return document.getElementById(id);
}

let announceTimer = 0;
// 화면 낭독기에 알릴 문장. 같은 문장을 다시 알릴 수 있게 한 번 비웠다가 넣는다.
export function announce(message) {
  const live = $("live");
  if (!live) return;
  live.textContent = "";
  window.clearTimeout(announceTimer);
  announceTimer = window.setTimeout(() => {
    live.textContent = message;
  }, 60);
}

// 입력 옆 오류 문장. 입력의 aria-describedby에 오류 요소 id가 들어 있어야 한다.
export function setFieldError(input, errorNode, message) {
  if (message) {
    errorNode.textContent = message;
    errorNode.hidden = false;
    if (input) input.setAttribute("aria-invalid", "true");
  } else {
    errorNode.textContent = "";
    errorNode.hidden = true;
    if (input) input.removeAttribute("aria-invalid");
  }
}

// 오류 설명(describeError 결과)을 영역 안의 문장 묶음으로 만든다.
export function errorNotice(info, ...actions) {
  return h(
    "div",
    { class: "notice notice-error", role: "alert" },
    icon("alert"),
    h(
      "div",
      { class: "notice-body" },
      h("p", { class: "notice-title", text: info.message }),
      info.hint ? h("p", { class: "notice-text", text: info.hint }) : null,
      info.detail ? h("p", { class: "notice-detail", text: `서버가 보낸 설명: ${info.detail}` }) : null,
      actions.some(Boolean) ? h("div", { class: "notice-actions" }, actions) : null,
    ),
  );
}

export function badge(level) {
  const internal = level === "internal";
  return h(
    "span",
    { class: `badge ${internal ? "badge-internal" : "badge-public"}` },
    internal ? icon("lock") : h("span", { class: "badge-ring", "aria-hidden": "true" }),
    String(level),
  );
}

export function roleTag(role, levels) {
  return h(
    "span",
    { class: "role-tag" },
    h("span", { class: "role-name", text: role }),
    h("span", { class: "role-levels" }, (levels || []).map(badge)),
  );
}
