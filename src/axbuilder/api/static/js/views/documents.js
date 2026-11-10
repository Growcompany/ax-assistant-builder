// 문서 탭: 올린 문서 표와 업로드 양식.
// 등급(access_level)은 기본값을 두지 않는다. 빠뜨린 내부 문서가 모두에게 보이면 안 된다.

import { uploadDocument } from "../actions.js";
import { announce, badge, errorNotice, h, icon, replace, setFieldError } from "../dom.js";
import { describeError } from "../errors.js";
import { DOC_TYPES, docTypeLabel, formatBytes } from "../format.js";
import { state } from "../state.js";
import { RULES, checkDocId, checkFile, checkLine, stemOf, suggestDocId } from "../validate.js";

export function createDocumentsView(workspaceId) {
  const tableSlot = h("div", { class: "docs-table-slot" });

  // ---- 업로드 양식 ----
  const fileInput = h("input", { class: "drop-input", id: "up-file", type: "file", accept: ".pdf,.hwpx,.txt,.md", "aria-describedby": "up-file-error" });
  const dropText = h("span", { class: "drop-text" });
  const drop = h("label", { class: "drop", for: "up-file" }, icon("upload"), dropText);
  const errors = {};
  const errorNode = (name) => (errors[name] = h("p", { class: "field-error", id: `up-${name}-error`, hidden: true }));

  const docId = h("input", { id: "up-doc_id", type: "text", autocomplete: "off", spellcheck: "false", maxlength: "64", "aria-describedby": "up-doc_id-hint up-doc_id-error" });
  const title = h("input", { id: "up-title", type: "text", autocomplete: "off", maxlength: "200", "aria-describedby": "up-title-error" });
  const source = h("input", { id: "up-source", type: "text", autocomplete: "off", maxlength: "500", placeholder: "예: 준법감시부, 2026. 3. 개정", "aria-describedby": "up-source-error" });
  const docType = h(
    "select",
    { id: "up-doc_type", "aria-describedby": "up-doc_type-error" },
    h("option", { value: "", text: "고르세요" }),
    DOC_TYPES.map(([value, label]) => h("option", { value, text: label })),
  );
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
    h("legend", { text: "등급" }),
    h("div", { class: "level-options" }, level("public", "모든 역할이 볼 수 있습니다"), level("internal", "internal 권한이 있는 역할만 봅니다")),
    errorNode("access_level"),
  );
  const submitButton = h("button", { class: "btn btn-primary", type: "submit" }, icon("upload"), "문서 올리기");
  const progress = h("p", { class: "progress-line", role: "status", hidden: true });
  const result = h("div", { class: "upload-result" });

  const form = h(
    "form",
    { class: "upload", novalidate: true, "aria-labelledby": "upload-title" },
    h("h2", { class: "section-title", id: "upload-title", text: "문서 올리기" }),
    result,
    h("div", { class: "field" }, drop, fileInput, errorNode("file")),
    h(
      "div",
      { class: "field-row" },
      h(
        "div",
        { class: "field" },
        h("label", { for: "up-doc_id", text: "문서 id" }),
        docId,
        h("p", { class: "field-hint", id: "up-doc_id-hint", text: "소문자·숫자를 하이픈(-)으로 이은 이름. 파일 이름에서 제안합니다." }),
        errorNode("doc_id"),
      ),
      h("div", { class: "field" }, h("label", { for: "up-title", text: "제목" }), title, errorNode("title")),
    ),
    h(
      "div",
      { class: "field-row" },
      h("div", { class: "field" }, h("label", { for: "up-source" }, "출처 ", h("span", { class: "optional", text: "선택" })), source, errorNode("source")),
      h("div", { class: "field" }, h("label", { for: "up-doc_type", text: "문서 종류" }), docType, errorNode("doc_type")),
    ),
    levels,
    h("div", { class: "upload-actions" }, submitButton, progress),
  );

  let file = null;
  let suggestedId = "";
  let suggestedTitle = "";
  let uploading = false;
  const inputs = { doc_id: docId, title, source, doc_type: docType, file: fileInput, access_level: null };

  function showFile() {
    if (file) {
      replace(dropText, h("strong", { class: "drop-file", text: file.name }), h("span", { class: "drop-hint", text: `${formatBytes(file.size)}. 다른 파일로 바꾸려면 누르거나 끌어다 놓으세요.` }));
    } else {
      replace(dropText, h("strong", { text: "파일을 여기에 끌어다 놓거나 눌러서 고르세요" }), h("span", { class: "drop-hint", text: "PDF, HWPX, TXT, MD. 파일 하나에 50MB까지." }));
    }
    drop.classList.toggle("has-file", Boolean(file));
  }

  function chooseFile(next, extraNote) {
    file = next || null;
    showFile();
    setFieldError(fileInput, errors.file, file ? checkFile(file) || "" : "");
    if (!file) return;
    // 사용자가 직접 고친 값은 덮어쓰지 않는다
    if (!docId.value || docId.value === suggestedId) {
      suggestedId = suggestDocId(file.name);
      docId.value = suggestedId;
    }
    if (!title.value || title.value === suggestedTitle) {
      suggestedTitle = stemOf(file.name).slice(0, 200);
      title.value = suggestedTitle;
    }
    if (extraNote) announce(extraNote);
  }

  fileInput.addEventListener("change", () => chooseFile(fileInput.files[0]));
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
    const dropped = event.dataTransfer ? [...event.dataTransfer.files] : [];
    if (!dropped.length) return;
    fileInput.value = "";
    chooseFile(dropped[0]);
    if (dropped.length > 1) {
      setFieldError(fileInput, errors.file, checkFile(dropped[0]) || `한 번에 하나씩 올립니다. 첫 파일(${dropped[0].name})만 골랐습니다.`);
    }
  });

  function clearErrors() {
    for (const [name, node] of Object.entries(errors)) setFieldError(inputs[name], node, "");
    replace(result);
  }

  function showFieldErrors(fields) {
    let first = null;
    for (const [name, node] of Object.entries(errors)) {
      setFieldError(inputs[name], node, fields[name] || "");
      if (fields[name] && !first) first = inputs[name] || levels.querySelector("input");
    }
    if (first) first.focus();
  }

  function selectedLevel() {
    const checked = levels.querySelector("input:checked");
    return checked ? checked.value : "";
  }

  function validate() {
    const fields = {};
    const fileProblem = checkFile(file);
    if (fileProblem) fields.file = fileProblem;
    const id = docId.value.trim();
    const idProblem = checkDocId(id);
    if (idProblem) fields.doc_id = idProblem;
    else if ((state.detail?.documents_list || []).some((doc) => doc.doc_id === id)) {
      fields.doc_id = `문서 id "${id}"가 이미 있습니다. 다른 id를 쓰세요.`;
    }
    const titleProblem = checkLine("title", title.value, 1, 200);
    if (titleProblem) fields.title = titleProblem;
    const sourceProblem = checkLine("source", source.value, 0, 500);
    if (sourceProblem) fields.source = sourceProblem;
    if (!docType.value) fields.doc_type = RULES.doc_type;
    if (!selectedLevel()) fields.access_level = RULES.access_level;
    return fields;
  }

  function setUploading(next) {
    uploading = next;
    submitButton.disabled = next;
    if (next) {
      replace(progress, h("span", { class: "spinner", "aria-hidden": "true" }), "올리고 문서를 읽어 보는 중입니다. 경과 ", h("span", { class: "elapsed", data: { since: Date.now() }, text: "0초" }));
    } else {
      replace(progress);
    }
    progress.hidden = !next;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (uploading) return;
    clearErrors();
    const fields = validate();
    if (Object.keys(fields).length) {
      showFieldErrors(fields);
      return;
    }
    const data = new FormData();
    data.append("doc_id", docId.value.trim());
    data.append("title", title.value.trim());
    data.append("source", source.value.trim());
    data.append("doc_type", docType.value);
    data.append("access_level", selectedLevel());
    data.append("file", file, file.name);
    const uploadedTitle = title.value.trim();
    setUploading(true);
    try {
      await uploadDocument(workspaceId, data);
      form.reset();
      suggestedId = "";
      suggestedTitle = "";
      chooseFile(null);
      replace(
        result,
        h(
          "div",
          { class: "notice notice-ok", role: "status" },
          icon("check"),
          h(
            "div",
            { class: "notice-body" },
            h("p", { class: "notice-title", text: `"${uploadedTitle}"을 올렸습니다.` }),
            h("p", { class: "notice-text", text: "답에 반영하려면 위의 색인 버튼으로 색인을 다시 만드세요." }),
          ),
        ),
      );
      announce(`${uploadedTitle} 문서를 올렸습니다. 색인을 다시 만들어야 답에 반영됩니다.`);
    } catch (error) {
      const info = describeError(error, "upload");
      const shown = { ...info.fields };
      const text = [info.message, info.hint, info.detail].filter(Boolean).join(" ");
      if (error.status === 409) shown.doc_id = text;
      if (error.status === 413 || error.status === 415) shown.file = text;
      showFieldErrors(shown);
      const unplaced = Object.keys(shown).filter((name) => !(name in errors));
      if (!Object.keys(shown).length || unplaced.length) {
        if (unplaced.length) info.detail = unplaced.map((name) => `${name}: ${shown[name]}`).join(" ");
        replace(result, errorNotice(info));
      }
    } finally {
      setUploading(false);
      if (result.firstChild) result.scrollIntoView({ block: "nearest" });
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
    const cell = (label, className, ...children) => h("td", { class: className, data: { label } }, children);
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
                cell("종류", "", docTypeLabel(doc.doc_type)),
                cell("형식", "", String(doc.extension || "").replace(/^\./, "").toUpperCase()),
                cell("등급", "", badge(doc.access_level)),
                cell("크기", "num", formatBytes(doc.size_bytes)),
                cell(
                  "색인",
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

  showFile();
  const root = h(
    "section",
    { class: "panel panel-documents", id: "panel-documents", role: "tabpanel", "aria-labelledby": "tab-documents", hidden: true },
    h("div", { class: "docs-column" }, tableSlot, form),
  );

  return { root, sync: renderTable };
}
