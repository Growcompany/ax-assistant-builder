// 밝은 화면이 기본이다(시스템 설정을 따르지 않는다). 사용자가 어두운 화면을 골라 둔 경우에만
// 모듈보다 먼저 실행해 첫 그림 전에 적용한다(깜빡임 방지).
(function () {
  try {
    var saved = window.localStorage.getItem("axb.theme");
    if (saved === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  } catch (error) {
    // 저장소를 못 쓰면 밝은 화면으로 둔다
  }
})();
