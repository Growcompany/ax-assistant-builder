// 새 워크스페이스 대화상자. <dialog>의 showModal()이 포커스 가둠·Esc 닫기·배경 비활성을 맡는다.

import { createWorkspace } from "../actions.js";
import { $, h, icon, replace, setFieldError } from "../dom.js";
import { describeError } from "../errors.js";
import { RULES, checkLine, checkWorkspaceId } from "../validate.js";

const MAX_ROLES = 50;
let rowSequence = 0;
let submitting = false;
let opener = null;

function roleRow(name, levels) {
  const n = (rowSequence += 1);
  const nameInput = h("input", {
    class: "role-name-input",
    id: `nw-role-${n}`,
    type: "text",
    maxlength: "50",
    autocomplete: "off",
    placeholder: "역할 이름",
    "aria-label": "역할 이름",
    value: name,
  });
  const check = (level) =>
    h(
      "label",
      { class: "check" },
      h("input", { type: "checkbox", value: level, checked: levels.includes(level) }),
      h("span", { text: level }),
    );
  const row = h(
    "li",
    { class: "role-row" },
    nameInput,
    h("div", { class: "role-row-levels", role: "group", "aria-label": "볼 수 있는 등급" }, check("public"), check("internal")),
    h(
      "button",
      {
        class: "btn btn-quiet btn-icon",
        type: "button",
        "aria-label": "이 역할 삭제",
        on: {
          click: () => {
            const list = $("nw-roles");
            const next = row.nextElementSibling || row.previousElementSibling;
            row.remove();
            syncRows();
            const target = next ? next.querySelector("input") : $("nw-add-role");
            target.focus();
            if (!list.children.length) $("nw-add-role").focus();
          },
        },
      },
      icon("close"),
    ),
  );
  return row;
}

function syncRows() {
  $("nw-add-role").disabled = $("nw-roles").children.length >= MAX_ROLES;
}

function resetForm() {
  $("nw-form").reset();
  replace($("nw-roles"), roleRow("직원", ["public"]), roleRow("관리자", ["public", "internal"]));
  syncRows();
  clearErrors();
}

function clearErrors() {
  for (const field of ["id", "name", "industry"]) {
    setFieldError($(`nw-${field}`), $(`nw-${field}-error`), "");
  }
  setFieldError(null, $("nw-roles-error"), "");
  replace($("nw-error"));
  $("nw-error").hidden = true;
}

function showErrors(fields) {
  let first = null;
  for (const field of ["id", "name", "industry"]) {
    setFieldError($(`nw-${field}`), $(`nw-${field}-error`), fields[field] || "");
    if (fields[field] && !first) first = $(`nw-${field}`);
  }
  setFieldError(null, $("nw-roles-error"), fields.roles || "");
  if (fields.roles && !first) first = $("nw-roles").querySelector("input") || $("nw-add-role");
  if (first) first.focus();
}

function readForm() {
  const fields = {};
  const id = $("nw-id").value.trim();
  const name = $("nw-name").value.trim();
  const industry = $("nw-industry").value.trim();
  const idProblem = checkWorkspaceId(id);
  if (idProblem) fields.id = idProblem;
  const nameProblem = checkLine("name", name, 1, 100);
  if (nameProblem) fields.name = nameProblem;
  const industryProblem = checkLine("industry", industry, 0, 50);
  if (industryProblem) fields.industry = industryProblem;

  const roles = {};
  const rows = [...$("nw-roles").children];
  if (!rows.length) fields.roles = "역할을 하나 이상 만드세요. 질문할 때 역할을 골라야 합니다.";
  for (const row of rows) {
    const roleName = row.querySelector(".role-name-input").value.trim();
    const levels = [...row.querySelectorAll("input[type='checkbox']")]
      .filter((box) => box.checked)
      .map((box) => box.value);
    if (checkLine("roles", roleName, 1, 50)) fields.roles = RULES.roles;
    else if (roleName in roles) fields.roles = `역할 이름 "${roleName}"이 두 번 있습니다. 이름을 다르게 적으세요.`;
    else if (!levels.length) fields.roles = `역할 "${roleName}"이 볼 수 있는 등급을 하나 이상 고르세요.`;
    else roles[roleName] = levels;
  }
  return { fields, workspace: { id, name, industry, roles } };
}

async function submit(event) {
  event.preventDefault();
  if (submitting) return;
  clearErrors();
  const { fields, workspace } = readForm();
  if (Object.keys(fields).length) {
    showErrors(fields);
    return;
  }
  submitting = true;
  const button = $("nw-submit");
  button.disabled = true;
  replace(button, h("span", { class: "spinner", "aria-hidden": "true" }), "만드는 중");
  try {
    await createWorkspace(workspace);
    $("new-workspace").close();
  } catch (error) {
    const info = describeError(error, "create");
    const shown = { ...info.fields };
    if (error.status === 409) shown.id = `${info.message} ${info.hint}`;
    showErrors(shown);
    // 입력 옆에 붙이지 못한 오류(연결 실패, 모르는 필드)는 아래에 문장으로
    const leftover = Object.entries(info.fields).filter(([field]) => !["id", "name", "industry", "roles"].includes(field));
    if (error.status !== 409 && (error.status !== 422 || leftover.length || !Object.keys(shown).length)) {
      const text = [info.message, info.hint, info.detail, ...leftover.map(([field, message]) => `${field}: ${message}`)]
        .filter(Boolean)
        .join(" ");
      replace($("nw-error"), text);
      $("nw-error").hidden = false;
    }
  } finally {
    submitting = false;
    button.disabled = false;
    replace(button, "워크스페이스 만들기");
  }
}

export function openNewWorkspace() {
  const dialog = $("new-workspace");
  if (dialog.open) return;
  opener = document.activeElement;
  resetForm();
  dialog.showModal();
  $("nw-id").focus();
}

export function mountNewWorkspace() {
  const dialog = $("new-workspace");
  $("nw-form").addEventListener("submit", submit);
  $("nw-cancel").addEventListener("click", () => dialog.close());
  $("nw-close").addEventListener("click", () => dialog.close());
  $("nw-add-role").addEventListener("click", () => {
    const row = roleRow("", ["public"]);
    $("nw-roles").appendChild(row);
    syncRows();
    row.querySelector("input").focus();
  });
  // 요청 중에는 Esc로 닫지 않는다(결과를 놓치지 않게)
  dialog.addEventListener("cancel", (event) => {
    if (submitting) event.preventDefault();
  });
  dialog.addEventListener("close", () => {
    if (opener && document.contains(opener)) opener.focus();
    opener = null;
  });
}
