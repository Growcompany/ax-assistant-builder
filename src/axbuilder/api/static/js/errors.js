// 서버 오류(ApiError)를 사람이 읽을 문장으로 바꾼다.
// 결과: { message, hint, detail, fields, action }
//   fields = { 입력 이름: 문장 } (422의 pydantic 오류 배열을 풀어낸 것)
//   action = "retry" | "reindex" | "reload" | null (화면이 어떤 버튼을 보일지)

import { RULES } from "./validate.js";

const BY_STATUS = {
  403: {
    message: "서버가 이 요청을 받지 않았습니다.",
    hint: "주소창의 주소가 서버 주소(127.0.0.1 또는 localhost)인지 확인하세요. 다른 사이트에 끼워 넣은 화면에서는 동작하지 않습니다.",
  },
  404: {
    message: "찾을 수 없습니다.",
    hint: "지워졌거나 주소가 바뀌었을 수 있습니다. 목록을 다시 불러오세요.",
  },
  409: { message: "지금 상태와 맞지 않는 요청입니다.", hint: "" },
  413: { message: "보낸 내용이 너무 큽니다.", hint: "파일은 하나에 50MB까지 올릴 수 있습니다." },
  415: {
    message: "지원하지 않는 파일 형식입니다.",
    hint: "PDF, HWPX, TXT, MD 파일만 올릴 수 있습니다. 확장자와 실제 내용이 같아야 합니다.",
  },
  422: { message: "입력한 내용을 확인하세요.", hint: "" },
  500: {
    message: "서버 안에서 오류가 났습니다.",
    hint: "서버를 띄운 터미널의 로그를 확인하세요.",
  },
  503: { message: "필요한 모델을 쓸 수 없습니다.", hint: "", action: "retry" },
};

const WORKSPACE_GONE = {
  message: "워크스페이스를 찾을 수 없습니다.",
  hint: "지워졌을 수 있습니다. 목록을 다시 불러오세요.",
  action: "reload",
};

const BY_CONTEXT = {
  ask: {
    400: {
      message: "이 역할로는 질문할 수 없습니다.",
      hint: "워크스페이스의 역할 목록이 바뀌었을 수 있습니다. 역할을 다시 고르세요.",
      action: "reload",
    },
    404: WORKSPACE_GONE,
    409: {
      message: "색인이 없거나 지금 서버 설정과 맞지 않아 답할 수 없습니다.",
      hint: "색인을 다시 만든 뒤 질문하세요.",
      action: "reindex",
    },
    422: { message: "질문을 확인하세요.", hint: RULES.question },
    503: {
      message: "답을 쓰는 LLM에 연결하지 못했습니다.",
      hint: "Ollama가 켜져 있는지 확인하세요(터미널에서 ollama serve, 받은 모델은 ollama list). 켠 뒤 다시 시도하면 됩니다.",
      action: "retry",
    },
  },
  index: {
    404: WORKSPACE_GONE,
    503: {
      message: "임베딩 모델을 불러오지 못해 색인을 만들지 못했습니다.",
      hint: "",
      action: "retry",
    },
  },
  upload: {
    404: WORKSPACE_GONE,
    409: { message: "같은 문서 id가 이미 있습니다.", hint: "문서 id를 바꿔서 다시 올리세요." },
    422: { message: "문서를 올리지 못했습니다.", hint: "" },
  },
  create: {
    409: { message: "같은 id의 워크스페이스가 이미 있습니다.", hint: "다른 id를 쓰세요." },
  },
  demo: {
    404: {
      message: "예시 문서를 찾지 못했습니다.",
      hint: "예시 워크스페이스는 저장소 루트에서 서버를 띄웠을 때만 불러올 수 있습니다(corpus/bank 폴더가 필요합니다). 직접 만들기로 시작할 수 있습니다.",
    },
  },
  load: { 404: WORKSPACE_GONE },
  model: {
    404: WORKSPACE_GONE,
    422: { message: "모델 이름이 형식에 맞지 않습니다.", hint: "목록에서 다시 고르세요." },
  },
  keys: {
    404: { message: "워크스페이스나 키를 찾을 수 없습니다.", hint: "목록을 다시 불러오세요." },
    409: { message: "이 워크스페이스에 만들 수 있는 키 수를 넘었습니다.", hint: "쓰지 않는 키를 폐기한 뒤 다시 만드세요." },
    422: { message: "키 이름과 역할을 확인하세요.", hint: "" },
  },
};

function fieldMessage(field, item) {
  const limit = item.ctx && item.ctx.max_length;
  switch (item.type) {
    case "missing":
      return RULES[field] && ["access_level", "doc_type", "file"].includes(field)
        ? RULES[field]
        : "필수 항목입니다.";
    case "string_too_long":
      return typeof limit === "number"
        ? `너무 깁니다. ${limit}자까지 쓸 수 있습니다.`
        : RULES[field] || "너무 깁니다.";
    case "string_too_short":
      return "비어 있습니다. 내용을 적으세요.";
    default:
      return RULES[field] || String(item.msg || "값이 올바르지 않습니다.");
  }
}

// pydantic 오류 배열 -> { 필드: 문장 }. loc는 ["body", "roles", "상담원", 0]처럼 온다.
function fieldErrors(detail) {
  const fields = {};
  for (const item of detail) {
    if (!item || typeof item !== "object") continue;
    const loc = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body") : [];
    const field = loc.length ? String(loc[0]) : "_";
    if (!(field in fields)) fields[field] = fieldMessage(field, item);
  }
  return fields;
}

export function describeError(error, context) {
  const status = error && typeof error.status === "number" ? error.status : -1;
  if (status === 0) {
    return {
      message: "서버에 연결할 수 없습니다.",
      hint: "서버가 켜져 있는지 확인하세요(uvicorn axbuilder.api.main:app). 켠 뒤 다시 시도하면 됩니다.",
      detail: "",
      fields: {},
      action: "retry",
    };
  }
  if (status === -1) {
    return {
      message: "화면에서 예상하지 못한 오류가 났습니다.",
      hint: "페이지를 새로 고친 뒤 다시 해 보세요.",
      detail: "",
      fields: {},
      action: null,
    };
  }
  const known = (BY_CONTEXT[context] || {})[status] ||
    BY_STATUS[status] || { message: `요청이 실패했습니다(상태 코드 ${status}).`, hint: "" };
  const detail = error.detail;
  // 모델이 설치되어 있지 않은 503: 서버 설명에 "ollama pull <이름>"이 들어 있다
  if (status === 503 && typeof detail === "string" && detail.includes("ollama pull ")) {
    return {
      message: "답을 쓸 모델이 Ollama에 설치되어 있지 않습니다.",
      hint: "터미널에서 아래 명령으로 모델을 받은 뒤 다시 시도하세요. 화면 위의 '답하는 모델'에서 설치된 다른 모델로 바꿔도 됩니다.",
      detail,
      fields: {},
      action: "retry",
    };
  }
  return {
    message: known.message,
    hint: known.hint || "",
    // 서버가 문장으로 준 설명은 그대로 덧붙인다(글자로만 표시한다)
    detail: typeof detail === "string" ? detail : "",
    fields: Array.isArray(detail) ? fieldErrors(detail) : {},
    action: known.action || null,
  };
}
