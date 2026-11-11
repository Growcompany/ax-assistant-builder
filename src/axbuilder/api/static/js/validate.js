// 서버(app.py)의 검증 규칙을 옮긴 입력 검사. 서버가 최종 판단하고, 여기서는 빨리 알려 준다.

const WORKSPACE_ID = /^[a-z0-9-]{2,32}$/;
const DOC_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const WINDOWS_RESERVED = new Set(["con", "prn", "aux", "nul"]);
for (let i = 0; i < 10; i += 1) {
  WINDOWS_RESERVED.add(`com${i}`);
  WINDOWS_RESERVED.add(`lpt${i}`);
}
const CONTROL = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;
const FORMAT_CHARS = /\p{Cf}/u;

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const MAX_QUESTION_CHARS = 2000;
export const SUPPORTED_SUFFIXES = [".pdf", ".hwpx", ".txt", ".md"];

export const RULES = {
  id: "소문자·숫자·하이픈(-)만 2~32자로 적으세요. con, nul 같은 Windows 장치 이름은 쓸 수 없습니다.",
  doc_id:
    "소문자·숫자 낱말을 하이픈(-)으로 이어 64자 안으로 적으세요(예: loan-policy-2026). Windows 장치 이름은 쓸 수 없습니다.",
  title: "제목은 한 줄, 1~200자입니다. 줄바꿈과 보이지 않는 제어 문자는 쓸 수 없습니다.",
  source: "출처는 한 줄, 500자까지입니다. 줄바꿈과 보이지 않는 제어 문자는 쓸 수 없습니다.",
  name: "이름은 한 줄, 1~100자입니다.",
  industry: "업종은 한 줄, 50자까지입니다.",
  roles: "역할 이름은 한 줄 1~50자, 등급은 public·internal 중에서 고릅니다. 역할은 50개까지입니다.",
  doc_type: "문서 종류를 고르세요.",
  access_level: "등급을 public과 internal 중에서 직접 고르세요.",
  file: "올릴 파일을 고르세요.",
  question: `질문은 1~${MAX_QUESTION_CHARS}자입니다.`,
  role: "역할을 고르세요.",
};

export function checkWorkspaceId(value) {
  return WORKSPACE_ID.test(value) && !WINDOWS_RESERVED.has(value) ? null : RULES.id;
}

export function checkDocId(value) {
  const ok = value.length <= 64 && DOC_ID.test(value) && !WINDOWS_RESERVED.has(value);
  return ok ? null : RULES.doc_id;
}

// 한 줄 입력(제목·출처·이름 등): 앞뒤 공백을 뺀 길이와 제어·형식 문자를 본다.
export function checkLine(field, value, min, max) {
  const text = value.trim();
  const bad =
    text.length < min || text.length > max || CONTROL.test(text) || FORMAT_CHARS.test(text);
  return bad ? RULES[field] : null;
}

export function suffixOf(filename) {
  const name = String(filename || "");
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

export function stemOf(filename) {
  const name = String(filename || "");
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

export function checkFile(file) {
  if (!file) return RULES.file;
  if (!SUPPORTED_SUFFIXES.includes(suffixOf(file.name))) {
    return `지원하지 않는 형식입니다. 올릴 수 있는 형식: ${SUPPORTED_SUFFIXES.join(", ")}`;
  }
  if (file.size === 0) return "빈 파일입니다.";
  if (file.size > MAX_UPLOAD_BYTES) return "파일이 50MB를 넘습니다. 나눠서 올리세요.";
  return null;
}
