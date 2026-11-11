// 이름에서 id를 만드는 순수 함수들(DOM을 쓰지 않는다. node로도 실행해 확인할 수 있다).
//   assignDocIds      파일 이름 여러 개 -> 서로 겹치지 않는 문서 id
//   suggestWorkspaceId 워크스페이스 이름 -> 영문 id 제안
//   guessDocType      파일 이름 -> 문서 종류 추정
//   splitCommand      안내 문장에서 터미널 명령(ollama pull …) 떼어 내기

import { checkDocId, checkWorkspaceId, stemOf, suffixOf } from "./validate.js";

const MAX_DOC_ID = 64;
const MAX_WORKSPACE_ID = 32;

// 파일 이름에서 영문·숫자만 남겨 "-"로 잇는다. 규칙에 맞지 않으면 빈 문자열.
//   "Loan Policy (2026) v2.PDF" -> "loan-policy-2026-v2"
//   "2026년 여신규정 v3.pdf"     -> "2026-v3"
//   "인사 규정(2026).md"         -> ""   (숫자만 남으면 뜻이 없어 번호를 준다)
//   "대출 규정.pdf"              -> ""   (남는 글자가 없다)
//   "con.txt"                    -> ""   (Windows 장치 이름은 서버가 거절한다)
export function docIdFromName(filename) {
  const slug = stemOf(filename)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, MAX_DOC_ID)
    .replace(/-+$/, "");
  // 영문자가 하나도 없으면(숫자만 남으면) 쓰지 않는다
  return /[a-z]/.test(slug) && checkDocId(slug) === null ? slug : "";
}

// 쓰지 않은 가장 작은 번호의 "doc-001"식 id.
export function nextNumberedDocId(used) {
  for (let number = 1; ; number += 1) {
    const candidate = `doc-${String(number).padStart(3, "0")}`;
    if (!used.has(candidate)) return candidate;
  }
}

// 파일 이름 목록 -> 문서 id 목록(같은 순서). taken은 이미 쓰고 있는 id.
// 이름에서 만든 id가 비거나 이미 쓰였으면 doc-001식 번호를 준다. 결과끼리도, taken과도 겹치지 않는다.
//   ["대출 규정.pdf", "여신 지침.hwpx", "민원 절차.md"]     -> ["doc-001", "doc-002", "doc-003"]
//   ["report.pdf", "report.md", "Report.txt"]              -> ["report", "doc-001", "doc-002"]
//   ["내규 v2.pdf", "지침 v2.pdf"]                          -> ["v2", "doc-001"]
//   ["a b.txt", "a_b.txt", "a-b.txt"]                      -> ["a-b", "doc-001", "doc-002"]
//   ["한글.txt"] (taken = ["doc-001", "doc-002"])          -> ["doc-003"]
//   ["doc-001.txt", "한글.txt"]                             -> ["doc-001", "doc-002"]
export function assignDocIds(filenames, taken = []) {
  const used = new Set(taken);
  return filenames.map((filename) => {
    let id = docIdFromName(filename);
    if (!id || used.has(id)) id = nextNumberedDocId(used);
    used.add(id);
    return id;
  });
}

// ---- 워크스페이스 id ----

const HANGUL_BASE = 0xac00;
const HANGUL_LAST = 0xd7a3;
const INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"];
const VOWELS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"];
const FINALS = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"];

// 한글 음절을 로마자로 옮긴다(글자 단위. 발음 변화 규칙은 적용하지 않는다: 제안값일 뿐이다).
//   "은행" -> "eunhaeng", "준법감시" -> "junbeopgamsi"
export function romanize(text) {
  let out = "";
  for (const char of String(text)) {
    const code = char.codePointAt(0);
    if (code < HANGUL_BASE || code > HANGUL_LAST) {
      out += char;
      continue;
    }
    const index = code - HANGUL_BASE;
    out += INITIALS[Math.floor(index / 588)] + VOWELS[Math.floor((index % 588) / 28)] + FINALS[index % 28];
  }
  return out;
}

// 워크스페이스 이름 -> 규칙(^[a-z0-9-]{2,32}$)에 맞고 taken과 겹치지 않는 id 제안. 사용자가 고칠 수 있다.
//   "AXB은행(가상)"      -> "axb-eunhaeng-gasang"
//   "우리 회사"          -> "uri-hoesa"
//   "Acme Corp."        -> "acme-corp"
//   "A"                 -> "workspace"   (두 글자가 안 된다)
//   "!!!"               -> "workspace"
//   "demo" (taken demo) -> "demo-2"
export function suggestWorkspaceId(name, taken = []) {
  // 영문·숫자와 한글이 붙어 있으면 사이를 띄운다("AXB은행" -> "AXB 은행")
  const spaced = String(name).replace(/([A-Za-z0-9])(?=[가-힣])|([가-힣])(?=[A-Za-z0-9])/g, "$1$2 ");
  let base = romanize(spaced)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, MAX_WORKSPACE_ID)
    .replace(/-+$/, "");
  if (checkWorkspaceId(base) !== null) base = "workspace";
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let number = 2; ; number += 1) {
    const tail = `-${number}`;
    const candidate = base.slice(0, MAX_WORKSPACE_ID - tail.length).replace(/-+$/, "") + tail;
    if (!used.has(candidate)) return candidate;
  }
}

// ---- 문서 종류 ----

// 확장자만으로는 종류를 알 수 없어서 파일 이름의 낱말을 먼저 보고, 모르면 "other".
// 값은 서버 DocMeta.doc_type의 허용값(law, regulation, terms, product, internal, other)만 쓴다.
//   "예금거래기본약관.hwpx" -> "terms",   "정기예금 상품설명서.pdf" -> "product"
//   "전자금융거래법.pdf"   -> "law",     "전자금융감독규정.pdf"     -> "regulation"
//   "고령 고객 응대 지침.md" -> "internal", "notes.txt"             -> "other"
const DOC_TYPE_WORDS = [
  ["terms", /약관|terms/i],
  ["product", /상품\s*설명서|설명서|상품\s*안내|product/i],
  ["regulation", /감독\s*규정|고시|시행\s*세칙|regulation/i],
  ["law", /법률|시행령|시행\s*규칙|법$|법\s*\(|act$/i],
  ["internal", /지침|내규|규정|규칙|매뉴얼|절차|가이드|guide|manual|policy/i],
];

export function guessDocType(filename) {
  const stem = stemOf(filename).trim();
  for (const [type, pattern] of DOC_TYPE_WORDS) {
    if (pattern.test(stem)) return type;
  }
  return "other";
}

export function extensionLabel(filename) {
  return suffixOf(filename).replace(/^\./, "").toUpperCase();
}

// ---- 안내 문장 속 명령 ----

const COMMAND = /ollama (?:pull [A-Za-z0-9][A-Za-z0-9._/:-]*|serve)/;

// 서버가 준 안내 문장에서 터미널 명령을 떼어 낸다. 명령은 복사 버튼과 함께 따로 보여 준다.
//   "설치된 모델이 없습니다. 터미널에서 모델을 받으세요: ollama pull qwen3.5:4b"
//     -> { text: "설치된 모델이 없습니다. 터미널에서 모델을 받으세요", command: "ollama pull qwen3.5:4b" }
//   "명령이 없는 문장" -> { text: "명령이 없는 문장", command: "" }
export function splitCommand(message) {
  const text = String(message || "");
  const match = COMMAND.exec(text);
  if (!match) return { text, command: "" };
  const rest = (text.slice(0, match.index) + text.slice(match.index + match[0].length))
    .replace(/[\s:]+$/, "")
    .replace(/:\s*$/, "")
    .trim();
  return { text: rest, command: match[0] };
}
