// 첫 화면: 워크스페이스가 하나도 없을 때(또는 목록을 못 받았을 때).
// 제목, 한 문장, 주 버튼 하나와 보조 링크, 그리고 제품이 하는 일을 보여 주는 예시 한 건만 둔다.

import { isDemoPending, loadDemo, loadWorkspaces } from "../actions.js";
import { badge, errorNotice, h, icon, replace } from "../dom.js";
import { describeError } from "../errors.js";
import { state } from "../state.js";
import { modelHintNotice } from "./quick-create.js";

// 고정 예시(예시 은행 문서의 조항을 그대로 인용): 답 한 줄과 그 근거 원문.
function specimen() {
  const chip = () => h("span", { class: "cite cite-static", "aria-hidden": "true", text: "1" });
  return h(
    "figure",
    { class: "specimen", "aria-label": "답변과 근거의 예시" },
    h("p", { class: "specimen-q", text: "고령 고객의 대리인이 예금을 찾으려면 어떤 서류가 필요한가요?" }),
    h("p", { class: "specimen-a" }, "위임장, 인감증명서 또는 본인서명사실확인서, 대리인의 실명확인증표를 모두 내야 합니다 ", chip(), "."),
    h(
      "div",
      { class: "specimen-source" },
      h(
        "p",
        { class: "specimen-source-head" },
        chip(),
        h("span", { class: "specimen-title", text: "고령 고객 응대 지침" }),
        h("span", { class: "specimen-article", text: "제11조" }),
        badge("internal"),
      ),
      h("p", { class: "passage", text: "제11조(대리인 거래) ① 고령 고객의 대리인이 영업점에서 거래하려면 다음 각 호의 서류를 모두 내야 한다." }),
    ),
    h("figcaption", { text: "예시입니다. 답변의 번호를 누르면 근거 원문이 열립니다." }),
  );
}

let noticeSlot = null;

// Ollama가 꺼져 있거나 모델이 없으면 첫 화면에서 무엇을 해야 하는지 알려 준다.
export function renderWelcomeNotice() {
  if (!noticeSlot || !document.contains(noticeSlot)) return;
  const hadFocus = noticeSlot.contains(document.activeElement);
  replace(noticeSlot, modelHintNotice("모델 없이도 만들 수 있습니다. 질문할 때는 모델이 필요합니다."));
  if (hadFocus) noticeSlot.querySelector("button")?.focus();
}

export function renderWelcome(container, openQuickCreate) {
  if (!state.loaded) {
    replace(container, h("p", { class: "boot-note", text: "불러오는 중입니다." }));
    return;
  }
  if (state.listError) {
    replace(
      container,
      h(
        "section",
        { class: "welcome" },
        h("h1", { class: "welcome-title", text: "목록을 불러오지 못했습니다" }),
        errorNotice(
          describeError(state.listError, "load"),
          h("button", { class: "btn", type: "button", on: { click: () => loadWorkspaces() } }, icon("refresh"), "다시 불러오기"),
        ),
      ),
    );
    return;
  }

  const errorSlot = h("div", { class: "welcome-error" });
  const demoButton = h("button", { class: "btn-link", type: "button" }, "예시 은행으로 먼저 써 보기");
  const progress = h("p", { class: "progress-line", hidden: true }, h("span", { class: "spinner", "aria-hidden": "true" }), "예시 문서 6개를 등록하는 중입니다.");
  noticeSlot = h("div", { class: "welcome-notice" });
  demoButton.addEventListener("click", async () => {
    if (isDemoPending()) return;
    demoButton.disabled = true;
    progress.hidden = false;
    replace(errorSlot);
    try {
      await loadDemo();
    } catch (error) {
      // 성공하면 이 화면이 워크스페이스 화면으로 바뀐다. 실패했을 때만 여기 남는다
      demoButton.disabled = false;
      progress.hidden = true;
      replace(errorSlot, errorNotice(describeError(error, "demo")));
    }
  });

  replace(
    container,
    h(
      "section",
      { class: "welcome" },
      h("h1", { class: "welcome-title", text: "회사 문서를 넣으면, 조항을 인용해 답합니다" }),
      h("p", { class: "welcome-lead", text: "근거가 없으면 답하지 않고, 역할마다 볼 수 있는 문서가 다릅니다." }),
      h(
        "div",
        { class: "welcome-actions" },
        h("button", { class: "btn btn-primary btn-large", type: "button", on: { click: openQuickCreate } }, "어시스턴트 만들기"),
        demoButton,
      ),
      progress,
      errorSlot,
      noticeSlot,
      specimen(),
    ),
  );
  renderWelcomeNotice();
}
