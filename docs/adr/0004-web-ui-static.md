# ADR 0004 — 화면은 Streamlit 대신 FastAPI가 직접 내주는 빌드 없는 정적 파일이다

- 상태: 채택 (Task 14~16, 기록 2026-11-11)
- 근거: 계획 [웹 화면](../superpowers/plans/2026-11-10-web-ui.md)의 Global Constraints·Review Focus, 설계 문서 [4.1·7장·9장](../superpowers/specs/2026-11-09-company-assistant-builder-design.md)(원래 계획은 Streamlit, 9장 로드맵의 M-6), 학습 노트 [Task 14](../learning/task-14-web-ui.md)·[Task 15](../learning/task-15-models-quick-create.md)

## 맥락

- 설계 문서는 화면을 Streamlit으로, 일정의 마지막(M-6)에 두었다. 사용자가 2026-11-10에 화면을 앞당기라고 했다.
- 이미 FastAPI 서버가 있다(워크스페이스 만들기, 업로드, 색인, 질문). 화면은 이 API를 부르기만 하면 된다.
- 대상 고객은 금융·공공이다. 폐쇄망에서 떠야 하므로 화면이 CDN·웹 폰트·분석 스크립트를 불러오면 안 된다.
- 화면에 보이는 글자의 대부분은 **남이 쓴 글**이다. 올린 문서의 제목과 본문, LLM이 만든 답변이 그대로 나온다. 문서 제목에 `<img onerror=…>`가 들어 있을 수 있다(Review Focus 1).
- 이 서버는 PR #19에서 Host 허용 목록, `Origin`·`Sec-Fetch-Site` 검사를 넣었다. 화면 때문에 이 검사를 느슨하게 하지 않는다.
- 단위 테스트는 네트워크·GPU·브라우저 없이 돈다(CI).

## 결정

- 화면은 HTML 하나, CSS 하나, 브라우저가 그대로 읽는 ES 모듈 JS다. Node·번들러·프레임워크·새 파이썬 의존성을 쓰지 않는다.
- 파일은 `src/axbuilder/api/static/`에 두고 패키지 데이터(`pyproject.toml`의 `"axbuilder.api" = ["static/**"]`)로 넣는다. `pip install` 뒤에도 뜬다.
- 같은 FastAPI 앱이 내준다. `GET /`은 `index.html`, `GET /static/…`은 그 폴더의 파일이다([`app.py`](../../src/axbuilder/api/app.py)의 `create_app` 끝부분). 화면과 API가 같은 출처라서 CORS 설정이 필요 없고, 기존 Host·`Origin`·`Sec-Fetch-Site` 검사를 그대로 통과한다.
- XSS는 세 겹으로 막는다.
  1. **코드 규칙**: 서버에서 온 글자는 텍스트 노드로만 넣는다. DOM 도우미 `h()`([`dom.js`](../../src/axbuilder/api/static/js/dom.js))가 문자열 자식을 `createTextNode`로 붙인다. 답변의 `**굵게**`, `- 목록`, `[n]` 인용은 문자열을 토큰으로 잘라 노드를 하나씩 만든다([`answer.js`](../../src/axbuilder/api/static/js/answer.js)). 마크업으로 해석하는 단계가 없다.
  2. **CSP**: 화면 응답(`/`, `/static/…`)에 `default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'`를 붙인다(`_SecurityHeaders`). `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Cross-Origin-Resource-Policy`는 API 응답을 포함한 모든 응답에 붙는다. 규칙 1이 깨져도 인라인 스크립트와 외부 출처 스크립트는 실행되지 않는다. 그래서 `index.html`에도 인라인 `<script>`·`<style>`·`style=""`이 없다.
  3. **정적 검사 테스트**: [`tests/api/test_static_ui.py`](../../tests/api/test_static_ui.py)가 `static/`의 모든 텍스트 파일에서 `innerHTML`·`insertAdjacentHTML`·`outerHTML`·`document.write`·`eval(`·`new Function`·외부 URL·`javascript:`·`<style`·`style="`·인라인 이벤트 속성을 찾아 있으면 실패한다. 규칙 1을 사람이 기억하는 대신 CI가 지킨다.
- 상태 관리는 직접 쓴다. 상태 객체 하나와 `on(이벤트)`/`emit(이벤트)`([`state.js`](../../src/axbuilder/api/static/js/state.js))이 전부다. 서버 호출은 `api.js`, 상태를 바꾸는 동작은 `actions.js`, 화면 조각은 `views/`에 둔다.
- 프런트엔드 단위 테스트 프레임워크는 넣지 않는다. 대신 (1) 위 정적 검사, (2) 화면이 쓰는 API의 파이썬 테스트([`test_ui_api.py`](../../tests/api/test_ui_api.py)), (3) 브라우저에서 사람이 하는 확인으로 대신한다.

## 대안

| 대안 | 장점 | 고르지 않은 이유 |
|---|---|---|
| Streamlit (설계 문서의 원래 계획) | 파이썬만으로 빨리 만든다. 채팅 위젯이 있다 | 프로세스가 하나 더 뜬다(Streamlit 서버 + API 서버). 의존성이 늘고, 프런트 자산을 자기 방식으로 내주므로 "외부 요청 0건"과 CSP를 우리가 통제하기 어렵다. 인용 칩을 누르면 근거 원문이 열리는 식의 상호작용과 화면 구성을 원하는 대로 잡기 어렵다. 조작할 때마다 스크립트 전체를 다시 실행하는 모델이라 상태를 세션에 따로 챙겨야 한다 |
| React + 번들러(Vite 등) | 컴포넌트·상태 관리·테스트 도구가 갖춰져 있다 | Node와 빌드 단계가 생긴다. 파이썬 저장소에 `node_modules`와 빌드 산출물 관리가 들어오고, `pip install`만으로 뜨지 않는다. 화면 규모(탭 3개, 대화상자 1개)에 비해 층이 많고, 면접에서 설명할 범위가 넓어진다 |
| HTMX (서버가 HTML 조각을 내려 줌) | JS를 거의 안 쓴다. 빌드가 없다 | 서버가 HTML을 만들게 되어 지금의 JSON API와 별도로 템플릿 경로가 생긴다. 남이 쓴 글을 서버 템플릿에서 이스케이프해야 하고, HTMX가 응답을 `innerHTML`로 넣으므로 "마크업으로 해석하는 단계가 없다"는 규칙을 쓸 수 없다. 라이브러리 파일도 함께 내줘야 한다 |

## 결과

- **좋아진 점**
  - 서버 하나, 명령 하나(`axb serve`)로 화면까지 뜬다. 화면이 밖으로 보내는 요청이 없다(정적 검사 테스트가 외부 URL을 막는다).
  - 화면이 쓰는 것은 공개된 JSON API뿐이다. 화면이 할 수 있는 일은 curl로도 할 수 있고, API 테스트가 화면의 서버 쪽을 그대로 검증한다.
  - 브라우저에서 확인한 것(세션 주인, 2026-11-11, 실제 서버와 `qwen3.5:4b-q4_K_M`): 예시 워크스페이스 불러오기 → 색인(69청크) → 질문 → 근거 패널 → 같은 질문을 역할만 바꿔 다시 묻기(준법감시는 내부 지침 제11조 인용, 상담원은 근거 없음으로 거절).
- **치른 값**
  - **상태 관리를 직접 썼다.** 무엇이 바뀌면 무엇을 다시 그릴지를 이벤트 이름으로 손수 잇는다. `emit`을 빠뜨리면 화면이 안 바뀌고, 이를 잡아 줄 도구가 없다. JS는 약 4,200줄이다(`static/js/` 전체, 2026-11-11 기준 `wc -l`).
  - **프런트 단위 테스트가 없다.** `naming.js`(파일 이름 → 문서 id), `answer.js`(답변 파서)처럼 DOM 없이 돌릴 수 있는 순수 함수도 자동 테스트가 없다. 정적 검사는 "위험한 API를 쓰지 않았다"와 "참조한 파일이 있다"만 본다. 동작이 맞는지는 보지 않는다. 화면 회귀는 사람이 브라우저에서 다시 눌러 봐야 드러난다.
  - 번들러가 없어서 깨진 `import`는 브라우저에서야 드러난다. 이것은 정적 검사(`test_module_imports_exist`)로 일부 메웠다.
  - 타입 검사(TypeScript)가 없다.
- **아직 확인하지 않은 것**
  - 브라우저 자동화 테스트(Playwright 등)는 없다. 접근성(키보드 조작, 화면 낭독기)은 코드로 넣었지만 도구로 감사하지 않았다.
  - API 문서 화면(`/docs`, Swagger)은 기본으로 꺼져 있다(`AXB_API_DOCS=true`로 켠다). 외부 CDN 스크립트를 부르고, PR 리뷰에서 다른 사이트의 프레임 안에 떠서 관리 호출이 실행되는 것이 재현됐기 때문이다. 켜면 그 경로에는 프레임 차단과 nosniff만 붙고 CDN 스크립트는 막지 않는다.

## 면접에서 "왜 이렇게 했냐"고 물으면

> "설계는 Streamlit이었는데, 이미 FastAPI 서버가 있어서 그 서버가 정적 파일을 직접 내주는 쪽으로 바꿨습니다. 프로세스와 의존성이 늘지 않고, 폐쇄망에서 외부 요청 없이 뜨고, 인용을 누르면 근거 원문이 열리는 화면을 원하는 대로 만들 수 있어서입니다. 화면에 나오는 글자가 대부분 올린 문서와 LLM 답변이라 XSS를 먼저 생각했습니다. 글자는 텍스트 노드로만 넣고, CSP로 인라인 스크립트를 막고, `innerHTML` 같은 API가 코드에 들어오면 CI가 실패하게 했습니다. 대가는 상태 관리를 직접 쓴 것과 프런트 단위 테스트가 없는 것입니다. 지금은 정적 검사와 API 테스트, 브라우저 수동 확인으로 대신하고 있고, 화면이 더 커지면 순수 함수부터 테스트를 붙이는 것이 다음 순서입니다."
