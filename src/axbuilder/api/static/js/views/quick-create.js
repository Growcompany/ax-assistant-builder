// 빠른 만들기 3단계: ① 이름 ② 모델 ③ 파일 → 워크스페이스 생성, 파일마다 업로드, 색인.
// <dialog>의 showModal()이 포커스 가둠·Esc 닫기·배경 비활성을 맡는다.
// 기본값은 가장 좁은 쪽이다: 역할은 모든 문서를 보는 "관리자" 하나, 파일 등급은 모두 internal.

import { loadModels, loadWorkspaces, selectWorkspace } from "../actions.js";
import { api } from "../api.js";
import { $, announce, badge, commandBox, errorNotice, h, icon, replace, setFieldError } from "../dom.js";
import { describeError } from "../errors.js";
import { docTypeLabel, formatBytes } from "../format.js";
import { assignDocIds, extensionLabel, guessDocType, splitCommand, suggestWorkspaceId } from "../naming.js";
import { emit, modelStatus, on, state } from "../state.js";
import { RULES, checkFile, checkLine, checkWorkspaceId, stemOf } from "../validate.js";

const MAX_ROLES = 50;
const STEP_HEADINGS = { 1: "qc-h1", 2: "qc-h2", 3: "qc-h3" };

let step = 1;
let phase = "form"; // form | running | done
let opener = null;
let idTouched = false; // 사용자가 id를 직접 고쳤으면 이름을 바꿔도 덮어쓰지 않는다
let chosenModel = null; // 고른 모델 이름. null이면 아직 고르지 않음
let files = []; // { file, problem, level }
let perFileLevels = false;
let afterClose = null; // 닫을 때 갈 곳(만든 워크스페이스)
let rowSequence = 0;

// ---- 1단계: 이름 ----

function takenIds() {
  return state.workspaces.map((workspace) => workspace.id);
}

function idProblem(id) {
  const problem = checkWorkspaceId(id);
  if (problem) return problem;
  return takenIds().includes(id) ? `"${id}"는 이미 있는 id입니다. 다른 id를 쓰세요.` : null;
}

function roleRow(name, levels) {
  const n = (rowSequence += 1);
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
    h("input", {
      class: "role-name-input",
      id: `qc-role-${n}`,
      type: "text",
      maxlength: "50",
      autocomplete: "off",
      placeholder: "역할 이름",
      "aria-label": "역할 이름",
      value: name,
    }),
    h("div", { class: "role-row-levels", role: "group", "aria-label": "볼 수 있는 등급" }, check("public"), check("internal")),
    h(
      "button",
      {
        class: "btn btn-quiet btn-icon",
        type: "button",
        "aria-label": "이 역할 삭제",
        on: {
          click: () => {
            const next = row.nextElementSibling || row.previousElementSibling;
            row.remove();
            syncRoleRows();
            (next ? next.querySelector("input") : $("qc-add-role")).focus();
          },
        },
      },
      icon("close"),
    ),
  );
  return row;
}

function syncRoleRows() {
  $("qc-add-role").disabled = $("qc-roles").children.length >= MAX_ROLES;
}

function readRoles() {
  const roles = {};
  const rows = [...$("qc-roles").children];
  if (!rows.length) return { problem: "역할을 하나 이상 두세요. 질문할 때 역할을 골라야 합니다." };
  for (const row of rows) {
    const name = row.querySelector(".role-name-input").value.trim();
    const levels = [...row.querySelectorAll("input[type='checkbox']")].filter((box) => box.checked).map((box) => box.value);
    if (checkLine("roles", name, 1, 50)) return { problem: RULES.roles };
    if (name in roles) return { problem: `역할 이름 "${name}"이 두 번 있습니다. 이름을 다르게 적으세요.` };
    if (!levels.length) return { problem: `역할 "${name}"이 볼 수 있는 등급을 하나 이상 고르세요.` };
    roles[name] = levels;
  }
  return { roles };
}

function validateStep1(focus) {
  const name = $("qc-name").value.trim();
  const id = $("qc-id").value.trim();
  const problems = {
    name: checkLine("name", name, 1, 100),
    id: idProblem(id),
    industry: checkLine("industry", $("qc-industry").value, 0, 50),
    roles: readRoles().problem || null,
  };
  for (const field of ["name", "id", "industry"]) {
    setFieldError($(`qc-${field}`), $(`qc-${field}-error`), problems[field] || "");
  }
  setFieldError(null, $("qc-roles-error"), problems.roles || "");
  if (problems.industry || problems.roles) $("qc-advanced").open = true;
  const first = ["name", "id", "industry", "roles"].find((field) => problems[field]);
  if (first && focus) (first === "roles" ? $("qc-roles").querySelector("input") || $("qc-add-role") : $(`qc-${first}`)).focus();
  return !first;
}

// ---- 2단계: 모델 ----

function modelMeta(model) {
  const parts = [];
  if (typeof model.size_bytes === "number") parts.push(`${(model.size_bytes / 1e9).toFixed(1)}GB`);
  if (model.parameter_size) parts.push(`파라미터 ${model.parameter_size}`);
  if (model.quantization) parts.push(`양자화 ${model.quantization}`);
  return parts;
}

function recheckButton() {
  return h(
    "button",
    { class: "btn", type: "button", disabled: state.modelsLoading, on: { click: () => loadModels() } },
    state.modelsLoading ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon("refresh"),
    state.modelsLoading ? "확인하는 중" : "다시 확인",
  );
}

// 서버의 안내(hint)를 문장과 복사할 명령으로 나눠 보여 준다. 첫 화면과 2단계가 함께 쓴다.
export function modelHintNotice(extra) {
  const status = modelStatus();
  if (state.modelsError && !status.known) {
    return errorNotice(describeError(state.modelsError, "models"), recheckButton());
  }
  if (!status.known || !status.hint || status.cloud) return null;
  const { text, command } = splitCommand(status.hint);
  return h(
    "div",
    { class: "notice notice-warn notice-stack", role: "status" },
    icon("alert"),
    h(
      "div",
      { class: "notice-body" },
      h("p", { class: "notice-title", text: status.available ? "기본 모델이 준비되지 않았습니다" : "Ollama에 연결하지 못했습니다" }),
      h("p", { class: "notice-text", text: `${text}.` }),
      command ? commandBox(command) : null,
      extra ? h("p", { class: "notice-text", text: extra }) : null,
      h("div", { class: "notice-actions" }, recheckButton()),
    ),
  );
}

function renderModels() {
  const slot = $("qc-models");
  const status = modelStatus();
  const hadFocus = slot.contains(document.activeElement);
  if (!status.known) {
    replace(
      slot,
      state.modelsError
        ? modelHintNotice()
        : h("p", { class: "progress-line" }, h("span", { class: "spinner", "aria-hidden": "true" }), "설치된 모델을 확인하는 중입니다."),
    );
    return;
  }
  if (status.cloud) {
    replace(
      slot,
      h(
        "div",
        { class: "notice", role: "status" },
        h("div", { class: "notice-body" }, h("p", { class: "notice-title", text: `Claude 모델 ${status.fallback}로 답합니다` }), h("p", { class: "notice-text", text: `${status.hint}.` })),
      ),
    );
    return;
  }
  const names = status.models.map((model) => model.name);
  if (chosenModel === null || !names.includes(chosenModel)) {
    chosenModel = names.includes(status.fallback) ? status.fallback : null;
  }
  const cards = status.models.map((model) =>
    h(
      "label",
      { class: "model-card" },
      h("input", {
        type: "radio",
        name: "qc-model",
        value: model.name,
        checked: model.name === chosenModel,
        on: {
          change: () => {
            chosenModel = model.name;
            setFieldError(null, modelError, "");
          },
        },
      }),
      h(
        "span",
        { class: "model-card-body" },
        h("span", { class: "model-name" }, model.name, model.name === status.fallback ? h("span", { class: "tag", text: "기본 모델" }) : null),
        h("span", { class: "model-meta" }, modelMeta(model).map((part) => h("span", { text: part }))),
      ),
    ),
  );
  const modelError = h("p", { class: "field-error", id: "qc-model-error", hidden: true });
  replace(
    slot,
    modelHintNotice("모델이 없어도 문서 올리기와 색인은 됩니다. 질문하려면 모델이 있어야 합니다."),
    cards.length
      ? h("fieldset", { class: "model-cards", "aria-describedby": "qc-model-error" }, h("legend", { class: "sr-only", text: "답을 쓸 모델" }), cards)
      : null,
    cards.length ? h("div", { class: "model-foot" }, h("span", { class: "field-hint", text: `설치된 모델 ${cards.length}개` }), status.hint ? null : recheckButton()) : null,
    modelError,
  );
  if (hadFocus) (slot.querySelector("input:checked") || slot.querySelector("input, button"))?.focus();
}

function validateStep2(focus) {
  const status = modelStatus();
  // 목록이 있는데 기본 모델이 설치돼 있지 않으면 하나를 골라야 한다(안 고르면 없는 모델로 답하려 한다)
  if (status.known && !status.cloud && status.models.length && chosenModel === null) {
    const node = $("qc-model-error");
    if (node) setFieldError(null, node, "답을 쓸 모델을 고르세요.");
    if (focus) $("qc-models").querySelector("input")?.focus();
    return false;
  }
  return true;
}

// ---- 3단계: 파일 ----

function validFiles() {
  return files.filter((entry) => !entry.problem);
}

function addFiles(list) {
  let added = 0;
  for (const file of list) {
    const same = files.some((entry) => entry.file.name === file.name && entry.file.size === file.size && entry.file.lastModified === file.lastModified);
    if (same) continue;
    files.push({ file, problem: checkFile(file), level: "internal" });
    added += 1;
  }
  renderFiles();
  if (added) announce(`파일 ${added}개를 넣었습니다. 모두 ${files.length}개.`);
}

function renderFiles() {
  const slot = $("qc-files");
  const good = validFiles();
  const ids = assignDocIds(good.map((entry) => entry.file.name));
  if (!files.length) {
    replace(slot, h("p", { class: "field-hint wizard-empty", text: "파일 없이 만들 수도 있습니다. 만든 뒤 문서 탭에서 올리면 됩니다." }));
    syncFoot();
    return;
  }
  const rows = files.map((entry) => {
    const index = good.indexOf(entry);
    const remove = h(
      "button",
      {
        class: "btn btn-quiet btn-icon",
        type: "button",
        "aria-label": `${entry.file.name} 빼기`,
        on: {
          click: () => {
            files = files.filter((other) => other !== entry);
            renderFiles();
            ($("qc-files").querySelector(".file-row button") || $("qc-file")).focus();
          },
        },
      },
      icon("close"),
    );
    const level = perFileLevels
      ? h(
          "select",
          {
            class: "file-level",
            "aria-label": `${entry.file.name}의 등급`,
            on: {
              change: (event) => {
                entry.level = event.target.value;
                syncFoot();
              },
            },
          },
          ["internal", "public"].map((value) => h("option", { value, text: value, selected: entry.level === value ? "selected" : null })),
        )
      : badge(entry.level);
    return h(
      "li",
      { class: "file-row", data: { state: entry.problem ? "bad" : "ok" } },
      h("span", { class: "tag file-ext", text: extensionLabel(entry.file.name) || "?" }),
      h(
        "span",
        { class: "file-main" },
        h("span", { class: "file-name", text: entry.file.name }),
        entry.problem
          ? h("span", { class: "file-problem", text: `올리지 않습니다: ${entry.problem}` })
          : h("span", { class: "file-sub", text: `${formatBytes(entry.file.size)}, 문서 id ${ids[index]}, 종류 ${docTypeLabel(guessDocType(entry.file.name))}` }),
      ),
      entry.problem ? null : level,
      remove,
    );
  });
  replace(
    slot,
    h("ul", { class: "file-rows", "aria-label": "넣은 파일" }, rows),
    h(
      "label",
      { class: "check file-advanced" },
      h("input", {
        type: "checkbox",
        checked: perFileLevels,
        on: {
          change: (event) => {
            perFileLevels = event.target.checked;
            renderFiles();
            $("qc-files").querySelector(".file-advanced input").focus();
          },
        },
      }),
      h("span", { text: "세부 설정: 파일별로 등급 바꾸기" }),
    ),
  );
  syncFoot();
}

// ---- 단계 이동 ----

function syncFoot() {
  const note = $("qc-foot-note");
  const good = validFiles();
  const skipped = files.length - good.length;
  if (phase !== "form") {
    $("qc-back").hidden = true;
    note.textContent = "";
    return;
  }
  $("qc-back").hidden = step === 1;
  $("qc-next").textContent = step === 3 ? "만들기" : "다음";
  if (step !== 3) {
    note.textContent = "";
    return;
  }
  const publicCount = good.filter((entry) => entry.level === "public").length;
  const levels = publicCount ? `internal ${good.length - publicCount}개, public ${publicCount}개` : "모두 internal 등급";
  note.textContent = good.length
    ? `워크스페이스를 만들고 파일 ${good.length}개(${levels})를 올린 뒤 색인합니다.${skipped ? ` ${skipped}개는 올리지 않습니다.` : ""}`
    : "파일 없이 워크스페이스만 만듭니다.";
}

function showStep(next, focus) {
  step = next;
  for (const n of [1, 2, 3]) $(`qc-step-${n}`).hidden = n !== step || phase !== "form";
  $("qc-run").hidden = phase === "form";
  for (const button of $("qc-rail").querySelectorAll(".rail-step")) {
    const n = Number(button.dataset.step);
    const current = phase === "form" && n === step;
    button.dataset.state = phase !== "form" || n < step ? "done" : current ? "current" : "todo";
    if (current) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
    button.disabled = phase !== "form";
  }
  if (step === 2) renderModels();
  if (step === 3) renderFiles();
  syncFoot();
  $("qc-body").scrollTop = 0;
  if (focus) {
    const target = step === 1 ? $("qc-name") : $(STEP_HEADINGS[step]);
    target.focus();
  }
}

// 앞 단계가 모두 맞아야 뒤 단계로 간다. 틀린 단계에서 멈춘다.
function goTo(target) {
  if (phase !== "form") return;
  if (target > 1 && !validateStep1(false)) {
    showStep(1, false);
    validateStep1(true);
    return;
  }
  if (target > 2 && !validateStep2(false)) {
    showStep(2, false);
    validateStep2(true);
    return;
  }
  showStep(target, true);
}

// ---- 만들기 ----

function stepLine(stateName, title, detail) {
  const mark =
    stateName === "active"
      ? h("span", { class: "spinner", "aria-hidden": "true" })
      : h("span", { class: `run-mark ${stateName === "done" ? "icon icon-check" : stateName === "failed" ? "icon icon-alert" : ""}`, "aria-hidden": "true" });
  const words = { todo: "대기", active: "진행 중", done: "완료", failed: "실패", skipped: "건너뜀" };
  return h(
    "li",
    { class: "run-step", data: { state: stateName } },
    mark,
    h("span", { class: "run-title", text: title }),
    h("span", { class: "run-state", text: words[stateName] }),
    detail ? h("div", { class: "run-detail" }, detail) : null,
  );
}

function renderRun(run) {
  const total = run.items.length;
  const finished = run.items.filter((item) => item.state === "done" || item.state === "failed").length;
  const failed = run.items.filter((item) => item.state === "failed");
  const uploading = run.items.find((item) => item.state === "active");
  const fileRows = total
    ? h(
        "ul",
        { class: "run-files" },
        run.items.map((item) =>
          h(
            "li",
            { class: "run-file", data: { state: item.state } },
            h("span", { class: "run-file-name", text: item.name }),
            h("span", { class: "run-file-state", text: { todo: "대기", active: "올리는 중", done: "올림", failed: "실패" }[item.state] }),
            item.reason ? h("span", { class: "run-file-reason", text: item.reason }) : null,
          ),
        ),
      )
    : null;
  const uploadTitle = total
    ? uploading
      ? `문서 올리기 ${finished + 1} / ${total}`
      : `문서 올리기 ${finished} / ${total}${failed.length ? `, 실패 ${failed.length}개` : ""}`
    : "문서 올리기 (파일 없음)";
  const indexDetail =
    run.index === "active"
      ? h(
          "p",
          { class: "progress-text" },
          "문서를 읽고 검색할 수 있게 만드는 중입니다. 처음에는 임베딩 모델을 불러오느라 더 걸립니다. 경과 ",
          h("span", { class: "elapsed", data: { since: run.indexStartedAt }, text: "0초" }),
        )
      : run.indexFailure
        ? errorNotice(describeError(run.indexFailure, "index"))
        : null;
  replace(
    $("qc-progress"),
    h(
      "ol",
      { class: "run-steps", role: "status" },
      stepLine(run.create, `워크스페이스 "${run.name}" 만들기`),
      stepLine(run.upload, uploadTitle, fileRows),
      stepLine(run.index, "색인 만들기", indexDetail),
    ),
    run.summary
      ? h(
          "div",
          { class: `notice ${run.summary.ok ? "notice-ok" : "notice-warn"} notice-stack run-summary` },
          icon(run.summary.ok ? "check" : "alert"),
          h("div", { class: "notice-body" }, h("p", { class: "notice-title", text: run.summary.title }), h("p", { class: "notice-text", text: run.summary.text })),
        )
      : null,
  );
}

function setPhase(next, run) {
  phase = next;
  $("qc-close").hidden = phase === "running";
  $("qc-back").hidden = true;
  $("qc-foot-note").textContent = "";
  const button = $("qc-next");
  button.hidden = phase === "running";
  if (phase === "done") button.textContent = run.summary.button;
  $("qc-hr").textContent = phase === "done" ? run.summary.heading : "만드는 중입니다";
  showStep(step, false);
}

function backToForm(targetStep) {
  phase = "form";
  $("qc-close").hidden = false;
  $("qc-next").hidden = false;
  showStep(targetStep, false);
}

async function create() {
  const { roles } = readRoles();
  const status = modelStatus();
  const name = $("qc-name").value.trim();
  const id = $("qc-id").value.trim();
  const good = validFiles();
  const ids = assignDocIds(good.map((entry) => entry.file.name));
  const workspace = { id, name, industry: $("qc-industry").value.trim(), roles };
  // 기본 모델을 골랐으면 값을 비워 서버 기본을 따르게 한다(서버 설정이 바뀌면 함께 바뀐다)
  if (!status.cloud && chosenModel && chosenModel !== status.fallback) workspace.llm_model = chosenModel;

  const run = {
    name,
    create: "active",
    upload: "todo",
    index: "todo",
    items: good.map((entry, index) => ({ name: entry.file.name, id: ids[index], entry, state: "todo", reason: "" })),
    summary: null,
  };
  setPhase("running", run);
  renderRun(run);
  $("qc-hr").focus();

  try {
    await api.createWorkspace(workspace);
  } catch (error) {
    // 만들지 못했으면 1단계로 돌아가 이유를 보여 준다(넣은 파일과 고른 모델은 그대로 둔다)
    const info = describeError(error, "create");
    backToForm(1);
    if (error.status === 409) setFieldError($("qc-id"), $("qc-id-error"), `${info.message} ${info.hint}`);
    for (const field of ["id", "name", "industry"]) {
      if (info.fields[field]) setFieldError($(`qc-${field}`), $(`qc-${field}-error`), info.fields[field]);
    }
    if (info.fields.roles) {
      setFieldError(null, $("qc-roles-error"), info.fields.roles);
      $("qc-advanced").open = true;
    }
    const placed = error.status === 409 || ["id", "name", "industry", "roles"].some((field) => info.fields[field]);
    replace($("qc-error-1"), placed ? null : errorNotice(info));
    ($("qc-step-1").querySelector("[aria-invalid='true']") || $("qc-name")).focus();
    loadWorkspaces();
    return;
  }
  run.create = "done";
  afterClose = { id, tab: "documents" };

  let uploaded = 0;
  run.upload = run.items.length ? "active" : "skipped";
  for (const item of run.items) {
    item.state = "active";
    renderRun(run);
    const data = new FormData();
    data.append("doc_id", item.id);
    data.append("title", stemOf(item.name).trim().slice(0, 200) || item.name.slice(0, 200));
    data.append("source", item.name.slice(0, 500));
    data.append("doc_type", guessDocType(item.name));
    data.append("access_level", item.entry.level);
    data.append("file", item.entry.file, item.name);
    try {
      await api.uploadDocument(id, data);
      item.state = "done";
      uploaded += 1;
    } catch (error) {
      const info = describeError(error, "upload");
      const fields = Object.values(info.fields);
      item.state = "failed";
      item.reason = [info.message, info.detail || info.hint, ...fields].filter(Boolean).join(" ");
    }
  }
  if (run.items.length) run.upload = uploaded ? "done" : "failed";

  if (uploaded) {
    run.index = "active";
    run.indexStartedAt = Date.now();
    state.indexing[id] = { startedAt: run.indexStartedAt };
    renderRun(run);
    try {
      await api.buildIndex(id);
      run.index = "done";
    } catch (error) {
      run.index = "failed";
      run.indexFailure = error;
      state.indexError[id] = error; // 워크스페이스 화면 머리에서도 다시 시도할 수 있게
    }
    delete state.indexing[id];
    emit("index");
  } else {
    run.index = "skipped";
  }

  const failed = run.items.length - uploaded;
  const ready = run.index === "done";
  afterClose = { id, tab: ready ? "ask" : "documents" };
  await loadWorkspaces();
  if (ready && !failed) {
    // 모두 됐으면 바로 질문 화면으로
    closeDialog();
    announce(`${name}을 만들었습니다. 문서 ${uploaded}개를 색인했습니다. 질문할 수 있습니다.`);
    return;
  }
  if (ready) {
    run.summary = {
      ok: true,
      heading: "만들었습니다",
      title: `파일 ${run.items.length}개 중 ${uploaded}개를 올리고 색인했습니다`,
      text: `실패한 ${failed}개는 위에 이유가 있습니다. 문서 탭에서 다시 올릴 수 있습니다.`,
      button: "질문 화면으로 가기",
    };
  } else if (!run.items.length) {
    run.summary = {
      ok: true,
      heading: "만들었습니다",
      title: "워크스페이스를 만들었습니다",
      text: "문서 탭에서 문서를 올리고 색인을 만들면 질문할 수 있습니다.",
      button: "문서 올리러 가기",
    };
  } else if (!uploaded) {
    run.summary = {
      ok: false,
      heading: "문서를 올리지 못했습니다",
      title: "워크스페이스는 만들었지만 올라간 문서가 없습니다",
      text: "위의 이유를 확인한 뒤 문서 탭에서 다시 올리세요.",
      button: "워크스페이스로 가기",
    };
  } else {
    run.summary = {
      ok: false,
      heading: "색인을 만들지 못했습니다",
      title: `문서 ${uploaded}개는 올렸지만 색인을 만들지 못했습니다`,
      text: "워크스페이스 화면의 색인 버튼으로 다시 시도할 수 있습니다. 올린 문서는 그대로 있습니다.",
      button: "워크스페이스로 가기",
    };
  }
  setPhase("done", run);
  renderRun(run);
  $("qc-next").focus();
  announce(run.summary.title);
}

// ---- 열고 닫기 ----

// 닫은 뒤 갈 곳으로 간다: 만든 워크스페이스가 있으면 그 화면, 없으면 열기 전 자리.
// close 이벤트는 늦게 올 수 있어서 닫는 쪽에서 직접 부르고, 두 번 불려도 한 번만 움직인다.
function leave() {
  const target = afterClose;
  const back = opener;
  afterClose = null;
  opener = null;
  if (target) {
    state.tab = target.tab;
    selectWorkspace(target.id);
    $("main").focus();
  } else if (back && document.contains(back)) {
    back.focus();
  }
}

function closeDialog() {
  $("quick-create").close();
  leave();
}

function reset() {
  step = 1;
  phase = "form";
  idTouched = false;
  chosenModel = null;
  files = [];
  perFileLevels = false;
  afterClose = null;
  $("qc-form").reset();
  $("qc-advanced").open = false;
  replace($("qc-roles"), roleRow("관리자", ["public", "internal"]));
  syncRoleRows();
  replace($("qc-error-1"));
  replace($("qc-progress"));
  for (const field of ["name", "id", "industry"]) setFieldError($(`qc-${field}`), $(`qc-${field}-error`), "");
  setFieldError(null, $("qc-roles-error"), "");
  $("qc-close").hidden = false;
  $("qc-next").hidden = false;
}

export function openQuickCreate() {
  const dialog = $("quick-create");
  if (dialog.open) return;
  opener = document.activeElement;
  reset();
  loadModels();
  dialog.showModal();
  showStep(1, true);
}

export function mountQuickCreate() {
  const dialog = $("quick-create");
  const nameInput = $("qc-name");
  const idInput = $("qc-id");

  nameInput.addEventListener("input", () => {
    if (nameInput.value.trim()) setFieldError(nameInput, $("qc-name-error"), "");
    if (idTouched) return;
    idInput.value = nameInput.value.trim() ? suggestWorkspaceId(nameInput.value, takenIds()) : "";
    setFieldError(idInput, $("qc-id-error"), "");
  });
  idInput.addEventListener("input", () => {
    idTouched = idInput.value !== "";
    // 규칙은 치는 동안 바로 검사한다
    setFieldError(idInput, $("qc-id-error"), idInput.value ? idProblem(idInput.value.trim()) || "" : "");
  });
  $("qc-add-role").addEventListener("click", () => {
    const row = roleRow("", ["public"]);
    $("qc-roles").appendChild(row);
    syncRoleRows();
    row.querySelector("input").focus();
  });

  for (const button of $("qc-rail").querySelectorAll(".rail-step")) {
    button.addEventListener("click", () => goTo(Number(button.dataset.step)));
  }
  $("qc-back").addEventListener("click", () => showStep(step - 1, true));
  $("qc-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (phase === "running") return;
    if (phase === "done") {
      closeDialog();
      return;
    }
    if (step === 1) {
      if (validateStep1(true)) showStep(2, true);
    } else if (step === 2) {
      if (validateStep2(true)) showStep(3, true);
    } else if (validateStep1(false) && validateStep2(false)) {
      create();
    } else {
      goTo(3);
    }
  });

  const drop = $("qc-drop");
  const fileInput = $("qc-file");
  fileInput.addEventListener("change", () => {
    addFiles([...fileInput.files]);
    fileInput.value = ""; // 같은 파일을 뺐다가 다시 고를 수 있게
  });
  for (const type of ["dragenter", "dragover"]) {
    drop.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.add("is-over");
    });
  }
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("is-over");
    if (event.dataTransfer) addFiles([...event.dataTransfer.files]);
  });

  $("qc-close").addEventListener("click", closeDialog);
  // 만드는 중에는 Esc로 닫지 않는다(진행과 결과를 놓치지 않게)
  dialog.addEventListener("cancel", (event) => {
    if (phase === "running") event.preventDefault();
  });
  dialog.addEventListener("close", leave); // Esc로 닫았을 때
  on("models", () => {
    if (dialog.open && phase === "form" && step === 2) renderModels();
  });
}
