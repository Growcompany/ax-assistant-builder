// 문서 탭: 올린 문서 표와 업로드 양식(여러 파일).
// 등급(access_level)은 기본값을 두지 않는다. 빠뜨린 내부 문서가 모두에게 보이면 안 된다.
// 문서 id는 파일 이름에서 제안하고(naming.js), 한글 이름처럼 만들 수 없으면 doc-001식 번호를 준다.

import { refreshDetail } from "../actions.js";
import { api } from "../api.js";
import { announce, badge, errorNotice, h, icon, replace, setFieldError } from "../dom.js";
import { describeError } from "../errors.js";
import { DOC_TYPES, docTypeLabel, formatBytes } from "../format.js";
import { assignDocIds, extensionLabel, guessDocType } from "../naming.js";
import { state } from "../state.js";
import { RULES, checkDocId, checkFile, checkLine, stemOf } from "../validate.js";

export function createDocumentsView(workspaceId) {
  const tableSlot = h("div", { class: "docs-table-slot" });

  // ---- 업로드 양식 ----
  const fileInput = h("input", { class: "drop-input", id: "up-file", type: "file", multiple: true, accept: ".pdf,.hwpx,.txt,.md", "aria-describedby": "up-file-error" });
  const drop = h(
    "label",
    { class: "drop", for: "up-file" },
    icon("upload"),
    h(
      "span",
      { class: "drop-text" },
      h("strong", { text: "파일을 여기에 끌어다 놓거나 눌러서 고르세요" }),
      h("span", { class: "drop-hint", text: "여러 개를 한 번에 넣을 수 있습니다. PDF, HWPX, TXT, MD. 파일 하나에 50MB까지." }),
    ),
  );
  const fileError = h("p", { class: "field-error", id: "up-file-error", hidden: true });
  const rowsSlot = h("div", { class: "batch-slot" });
  const source = h("input", { id: "up-source", type: "text", autocomplete: "off", maxlength: "500", placeholder: "예: 준법감시부, 2026. 3. 개정", "aria-describedby": "up-source-hint up-source-error" });
  const sourceError = h("p", { class: "field-error", id: "up-source-error", hidden: true });
  const levelError = h("p", { class: "field-error", id: "up-access_level-error", hidden: true });
  const level = (value, description) =>
    h(
      "label",
      { class: "level-option" },
      h("input", { type: "radio", name: "up-access", value }),
      h("span", { class: "level-option-body" }, badge(value), h("span", { class: "level-desc", text: description })),
    );
  const levels = h(
    "fieldset",
    { class: "level-field", "aria-describedby": "up-access_level-error" },
    h("legend", { text: "등급 (이번에 올리는 파일 모두에 적용)" }),
    h("div", { class: "level-options" }, level("public", "모든 역할이 볼 수 있습니다"), level("internal", "internal 권한이 있는 역할만 봅니다")),
    levelError,
  );
  const submitLabel = h("span", { text: "문서 올리기" });
  const submitButton = h("button", { class: "btn btn-primary", type: "submit" }, icon("upload"), submitLabel);
  const progress = h("p", { class: "progress-line", role: "status", hidden: true });
  const result = h("div", { class: "upload-result" });

  const form = h(
    "form",
    { class: "upload", novalidate: true, "aria-labelledby": "upload-title" },
    h("h2", { class: "section-title", id: "upload-title", text: "문서 올리기" }),
    result,
    h("div", { class: "field" }, drop, fileInput, fileError),
    rowsSlot,
    h(
      "div",
      { class: "field" },
      h("label", { for: "up-source" }, "출처 ", h("span", { class: "optional", text: "선택" })),
      source,
      h("p", { class: "field-hint", id: "up-source-hint", text: "이번에 올리는 파일 모두에 적습니다. 비워 두면 출처 없이 올립니다." }),
      sourceError,
    ),
    levels,
    h("div", { class: "upload-actions" }, submitButton, progress),
  );

  // 올릴 파일 한 줄: { file, problem, id, idTouched, title, docType, error, state, reason }
  let batch = [];
  let uploading = false;
  let rowSequence = 0;

  function existingIds() {
    return (state.detail?.documents_list || []).map((doc) => doc.doc_id);
  }

  // 사용자가 고치지 않은 줄의 id를 다시 제안한다(이미 있는 문서, 직접 적은 id와 겹치지 않게).
  function suggestIds() {
    const open = batch.filter((row) => !row.problem && !row.idTouched);
    const taken = [...existingIds(), ...batch.filter((row) => row.idTouched).map((row) => row.id)];
    const ids = assignDocIds(open.map((row) => row.file.name), taken);
    open.forEach((row, index) => {
      row.id = ids[index];
    });
  }

  function addFiles(list) {
    setFieldError(fileInput, fileError, "");
    for (const file of list) {
      const same = batch.some((row) => row.file.name === file.name && row.file.size === file.size && row.file.lastModified === file.lastModified);
      if (same) continue;
      batch.push({
        key: (rowSequence += 1),
        file,
        problem: checkFile(file),
        id: "",
        idTouched: false,
        title: stemOf(file.name).trim().slice(0, 200),
        docType: guessDocType(file.name),
        error: {},
        state: "todo",
        reason: "",
      });
    }
    suggestIds();
    renderRows();
    if (list.length) announce(`올릴 파일이 ${batch.length}개 있습니다.`);
  }

  function renderRows() {
    const good = batch.filter((row) => !row.problem);
    submitLabel.textContent = good.length > 1 ? `문서 ${good.length}개 올리기` : "문서 올리기";
    if (!batch.length) {
      replace(rowsSlot);
      return;
    }
    replace(
      rowsSlot,
      h(
        "ul",
        { class: "batch", "aria-label": "올릴 파일" },
        batch.map((row) => {
          const remove = h(
            "button",
            {
              class: "btn btn-quiet btn-icon",
              type: "button",
              disabled: uploading,
              "aria-label": `${row.file.name} 빼기`,
              on: {
                click: () => {
                  batch = batch.filter((other) => other !== row);
                  suggestIds();
                  renderRows();
                  (rowsSlot.querySelector("input, button") || fileInput).focus();
                },
              },
            },
            icon("close"),
          );
          const head = h(
            "div",
            { class: "batch-head" },
            h("span", { class: "tag file-ext", text: extensionLabel(row.file.name) || "?" }),
            h("span", { class: "file-name", text: row.file.name }),
            h("span", { class: "file-sub", text: formatBytes(row.file.size) }),
            row.state === "active" ? h("span", { class: "spinner", "aria-hidden": "true" }) : null,
            remove,
          );
          if (row.problem) {
            return h("li", { class: "batch-row", data: { state: "bad" } }, head, h("p", { class: "file-problem", text: `올리지 않습니다: ${row.problem}` }));
          }
          const n = row.key;
          const idInput = h("input", { id: `up-id-${n}`, type: "text", autocomplete: "off", spellcheck: "false", maxlength: "64", value: row.id, disabled: uploading, "aria-describedby": `up-id-${n}-error` });
          const titleInput = h("input", { id: `up-title-${n}`, type: "text", autocomplete: "off", maxlength: "200", value: row.title, disabled: uploading, "aria-describedby": `up-title-${n}-error` });
          const typeSelect = h(
            "select",
            { id: `up-type-${n}`, disabled: uploading },
            DOC_TYPES.map(([value, label]) => h("option", { value, text: label })),
          );
          typeSelect.value = row.docType;
          const idError = h("p", { class: "field-error", id: `up-id-${n}-error`, hidden: true });
          const titleError = h("p", { class: "field-error", id: `up-title-${n}-error`, hidden: true });
          setFieldError(idInput, idError, row.error.doc_id || "");
          setFieldError(titleInput, titleError, row.error.title || "");
          idInput.addEventListener("input", () => {
            row.id = idInput.value.trim();
            row.idTouched = true;
            row.error.doc_id = row.id ? checkDocId(row.id) || "" : "";
            setFieldError(idInput, idError, row.error.doc_id);
          });
          titleInput.addEventListener("input", () => {
            row.title = titleInput.value;
          });
          typeSelect.addEventListener("change", () => {
            row.docType = typeSelect.value;
          });
          return h(
            "li",
            { class: "batch-row", data: { state: row.state } },
            head,
            h(
              "div",
              { class: "batch-fields" },
              h("div", { class: "field" }, h("label", { for: `up-id-${n}`, text: "문서 id" }), idInput, idError),
              h("div", { class: "field batch-title" }, h("label", { for: `up-title-${n}`, text: "제목" }), titleInput, titleError),
              h("div", { class: "field" }, h("label", { for: `up-type-${n}`, text: "문서 종류" }), typeSelect),
            ),
            row.reason ? h("p", { class: "file-problem", role: "alert", text: `올리지 못했습니다: ${row.reason}` }) : null,
          );
        }),
      ),
      h("p", { class: "field-hint", text: "문서 id는 파일 이름에서 제안했습니다(소문자·숫자를 하이픈으로 이은 이름). 한글 이름처럼 만들 수 없으면 doc-001식 번호를 줍니다. 문서 종류도 이름에서 추정한 값이니 확인하세요." }),
    );
  }

  fileInput.addEventListener("change", () => {
    addFiles([...fileInput.files]);
    fileInput.value = "";
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
    if (event.dataTransfer && !uploading) addFiles([...event.dataTransfer.files]);
  });

  function selectedLevel() {
    const checked = levels.querySelector("input:checked");
    return checked ? checked.value : "";
  }

  // 올리기 전 검사. 틀린 곳이 있으면 false(오류는 각 줄과 입력 옆에 보인다).
  function validate(good) {
    let ok = true;
    const seen = new Set(existingIds());
    for (const row of good) {
      row.error = {};
      row.reason = "";
      const idProblem = checkDocId(row.id);
      if (idProblem) row.error.doc_id = idProblem;
      else if (seen.has(row.id)) row.error.doc_id = `문서 id "${row.id}"가 이미 있거나 위 줄과 겹칩니다. 다른 id를 쓰세요.`;
      seen.add(row.id);
      const titleProblem = checkLine("title", row.title, 1, 200);
      if (titleProblem) row.error.title = titleProblem;
      if (Object.keys(row.error).length) ok = false;
    }
    const sourceProblem = checkLine("source", source.value, 0, 500);
    setFieldError(source, sourceError, sourceProblem || "");
    setFieldError(null, levelError, selectedLevel() ? "" : RULES.access_level);
    setFieldError(fileInput, fileError, good.length ? "" : RULES.file);
    return ok && !sourceProblem && Boolean(selectedLevel()) && good.length > 0;
  }

  function showProgress(done, total, startedAt) {
    replace(
      progress,
      h("span", { class: "spinner", "aria-hidden": "true" }),
      `${total}개 중 ${done + 1}번째 파일을 올리고 읽어 보는 중입니다. 경과 `,
      h("span", { class: "elapsed", data: { since: startedAt }, text: "0초" }),
    );
    progress.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (uploading) return;
    replace(result);
    const good = batch.filter((row) => !row.problem);
    if (!validate(good)) {
      renderRows();
      const invalid = form.querySelector("[aria-invalid='true']") || (good.length && !selectedLevel() ? levels.querySelector("input") : fileInput);
      invalid.focus();
      return;
    }
    const accessLevel = selectedLevel();
    const sourceText = source.value.trim();
    const startedAt = Date.now();
    uploading = true;
    submitButton.disabled = true;
    let uploaded = 0;
    let lastFailure = null;
    for (const [index, row] of good.entries()) {
      row.state = "active";
      renderRows();
      showProgress(index, good.length, startedAt);
      const data = new FormData();
      data.append("doc_id", row.id);
      data.append("title", row.title.trim());
      data.append("source", sourceText);
      data.append("doc_type", row.docType);
      data.append("access_level", accessLevel);
      data.append("file", row.file, row.file.name);
      try {
        await api.uploadDocument(workspaceId, data);
        row.state = "done";
        uploaded += 1;
      } catch (error) {
        const info = describeError(error, "upload");
        row.state = "failed";
        row.error = { doc_id: info.fields.doc_id || "", title: info.fields.title || "" };
        if (error.status === 409) row.error.doc_id = `${info.message} ${info.hint}`;
        const other = Object.entries(info.fields)
          .filter(([name]) => name !== "doc_id" && name !== "title")
          .map(([, message]) => message);
        row.reason = [info.message, info.detail || info.hint, ...other].filter(Boolean).join(" ");
        lastFailure = error;
      }
    }
    uploading = false;
    submitButton.disabled = false;
    progress.hidden = true;
    replace(progress);
    const failed = good.length - uploaded;
    // 올라간 줄은 지우고, 실패한 줄은 이유와 함께 남겨 고쳐서 다시 올릴 수 있게 한다
    batch = batch.filter((row) => row.state !== "done");
    for (const row of batch) if (row.state === "failed") row.state = "todo";
    if (uploaded) await refreshDetail();
    suggestIds();
    renderRows();
    if (uploaded) {
      replace(
        result,
        h(
          "div",
          { class: `notice ${failed ? "notice-warn" : "notice-ok"} notice-stack`, role: "status" },
          icon(failed ? "alert" : "check"),
          h(
            "div",
            { class: "notice-body" },
            h("p", { class: "notice-title", text: failed ? `${good.length}개 중 ${uploaded}개를 올렸습니다. ${failed}개는 올리지 못했습니다.` : `문서 ${uploaded}개를 올렸습니다.` }),
            h("p", { class: "notice-text", text: `답에 반영하려면 위의 색인 버튼으로 색인을 다시 만드세요.${failed ? " 실패한 파일은 아래에 이유가 있습니다." : ""}` }),
          ),
        ),
      );
      announce(`문서 ${uploaded}개를 올렸습니다.${failed ? ` ${failed}개는 실패했습니다.` : ""} 색인을 다시 만들어야 답에 반영됩니다.`);
    } else {
      const info = describeError(lastFailure, "upload");
      replace(result, errorNotice(good.length > 1 ? { ...info, message: `${good.length}개 모두 올리지 못했습니다. 아래 파일마다 이유가 있습니다.`, hint: "", detail: "" } : info));
      announce("문서를 올리지 못했습니다.");
    }
    result.scrollIntoView({ block: "nearest" });
    if (!failed) {
      form.reset();
      setFieldError(null, levelError, "");
    }
  });

  // ---- 문서 표 ----
  function renderTable() {
    const detail = state.detail;
    if (!detail) {
      replace(tableSlot, state.detailError ? null : h("p", { class: "boot-note", text: "문서 목록을 불러오는 중입니다." }));
      return;
    }
    const documents = detail.documents_list || [];
    if (!documents.length) {
      replace(
        tableSlot,
        h("div", { class: "docs-empty" }, h("p", { class: "gate-title", text: "아직 올린 문서가 없습니다" }), h("p", { class: "gate-text", text: "아래에서 문서를 올리세요. 올린 뒤 색인을 만들면 질문할 수 있습니다." })),
      );
      return;
    }
    const cell = (className, ...children) => h("td", { class: className }, children);
    replace(
      tableSlot,
      h(
        "div",
        { class: "table-wrap" },
        h(
          "table",
          { class: "docs-table" },
          h("caption", { class: "sr-only", text: "올린 문서" }),
          h(
            "thead",
            null,
            h(
              "tr",
              null,
              h("th", { scope: "col", text: "제목" }),
              h("th", { scope: "col", text: "종류" }),
              h("th", { scope: "col", text: "형식" }),
              h("th", { scope: "col", text: "등급" }),
              h("th", { scope: "col", class: "num", text: "크기" }),
              h("th", { scope: "col", text: "색인" }),
            ),
          ),
          h(
            "tbody",
            null,
            documents.map((doc) =>
              h(
                "tr",
                null,
                h(
                  "th",
                  { scope: "row", class: "doc-title-cell" },
                  h("span", { class: "doc-title", text: doc.title }),
                  h("span", { class: "doc-sub", text: doc.source ? `${doc.doc_id}, ${doc.source}` : doc.doc_id }),
                ),
                cell("", docTypeLabel(doc.doc_type)),
                cell("", String(doc.extension || "").replace(".", "").toUpperCase()),
                cell("", badge(doc.access_level)),
                cell("num", formatBytes(doc.size_bytes)),
                cell(
                  "",
                  h(
                    "span",
                    { class: "index-mark", data: { state: doc.indexed ? "ready" : "none" } },
                    h("span", { class: "index-dot", "aria-hidden": "true" }),
                    h("span", { class: "index-label", text: doc.indexed ? "색인됨" : "색인 전" }),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  const root = h(
    "section",
    { class: "panel panel-documents", id: "panel-documents", role: "tabpanel", "aria-labelledby": "tab-documents", hidden: true },
    h("div", { class: "docs-column" }, tableSlot, form),
  );

  return { root, sync: renderTable };
}
