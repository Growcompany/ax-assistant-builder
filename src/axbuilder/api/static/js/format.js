export function formatBytes(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

export function formatElapsed(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}초`;
  return `${Math.floor(seconds / 60)}분 ${String(seconds % 60).padStart(2, "0")}초`;
}

export function formatLatency(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}초`;
  return formatElapsed(ms);
}

// data-since(시작 시각, ms)가 붙은 요소의 글자를 1초마다 경과 시간으로 바꾼다.
export function startElapsedTicker() {
  window.setInterval(() => {
    const now = Date.now();
    for (const node of document.querySelectorAll("[data-since]")) {
      node.textContent = formatElapsed(now - Number(node.dataset.since));
    }
  }, 1000);
}

const DOC_TYPE_LABELS = {
  law: "법령",
  regulation: "감독규정",
  terms: "약관",
  product: "상품설명서",
  internal: "내부 문서",
  other: "기타",
};

export const DOC_TYPES = Object.entries(DOC_TYPE_LABELS);

export function docTypeLabel(value) {
  return DOC_TYPE_LABELS[value] || String(value || "");
}
