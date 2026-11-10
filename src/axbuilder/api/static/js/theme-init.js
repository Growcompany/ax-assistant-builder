// 모듈보다 먼저 실행해 저장된 테마를 첫 그림 전에 적용한다(깜빡임 방지).
(function () {
  try {
    var saved = window.localStorage.getItem("axb.theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (error) {
    // 저장소를 못 쓰면 시스템 설정을 따른다
  }
})();
