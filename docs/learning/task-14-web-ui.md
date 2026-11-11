# Task 14 — 웹 화면

계획: [`2026-11-10-web-ui.md`](../superpowers/plans/2026-11-10-web-ui.md) 1부. 결정 기록: [ADR 0004](../adr/0004-web-ui-static.md). 커밋 `6708c20`(조회 API, 정적 파일 제공), `8163fed`(화면).

## 이 작업이 하는 일

서버 하나로 브라우저에서 "워크스페이스 고르기 → 문서 올리기 → 색인 → 역할을 골라 질문 → 인용한 원문 확인"까지 한다. 두 부분이다.

**백엔드(14-A)** — 화면이 쓰는 API를 더했다. 기존 API의 경로·필드·상태 코드는 바꾸지 않고 더하기만 했다.

| API | 하는 일 |
|---|---|
| `GET /workspaces` | 목록(id 오름차순). 문서 수와 색인 상태(`none`·`ready`·`stale`)를 함께 준다 |
| `GET /workspaces/{id}` | 위 내용 + 문서 목록(`indexed`: 지금 색인에 들어 있는가) |
| `POST /workspaces/{id}/ask` | 응답에 `passages` 추가. `passages[i]`는 `citations[i]`의 청크 원문 |
| `POST /demo-workspace` | 저장소의 가상 은행 문서 6개로 워크스페이스 `demo`를 만든다. 색인은 하지 않는다 |
| `GET /`, `GET /static/…` | 화면 파일. CSP·nosniff 머리글을 붙인다 |

**프런트엔드(14-B)** — `src/axbuilder/api/static/`의 HTML 하나, CSS 하나, ES 모듈 JS. 빌드 도구가 없다. 구성은 왼쪽 사이드바(워크스페이스 목록, 서버 상태), 워크스페이스 화면(질문 탭, 문서 탭), 인용을 누르면 열리는 근거 패널이다. 워크스페이스가 하나도 없으면 첫 화면이 다음 할 일(예시 불러오기, 직접 만들기)을 보여 준다.

브라우저에서 확인한 흐름(세션 주인, 2026-11-11, 실제 서버와 `qwen3.5:4b-q4_K_M`): 예시 워크스페이스 불러오기 → 색인(69청크) → 질문 → 근거 패널 → 같은 질문을 역할만 바꿔 다시 묻기. 준법감시는 내부 지침 제11조를 인용했고, 상담원은 근거 없음으로 거절했다.

## 핵심 개념

**XSS와 `textContent`.** 화면에 나오는 문서 제목, 답변, 인용 원문은 남이 쓴 글이다. 이것을 `innerHTML`로 넣으면 브라우저가 글자를 마크업으로 읽어서 `<img onerror=…>`가 실행된다. `textContent`나 `createTextNode`로 넣으면 글자 그대로 보인다. 이 화면은 DOM을 만드는 도우미 `h()` 하나를 거치고, 그 함수가 문자열을 늘 텍스트 노드로 붙인다.

**답변을 마크업 없이 꾸미기.** 답변에는 `**굵게**`, `- 목록`, 인용 번호 `[1]`이 섞여 있다. 마크다운 라이브러리로 HTML 문자열을 만들어 넣으면 위 규칙이 깨진다. `answer.js`는 문자열을 정규식으로 잘라 토큰 배열(`text`·`bold`·`cite`)로 만들고, 토큰마다 노드를 하나씩 만든다. `[n]`은 1 ≤ n ≤ 인용 수일 때만 누를 수 있는 인용이 되고, 그 밖의 번호는 글자로 남는다.

**CSP(Content-Security-Policy).** 서버가 응답 머리글로 "이 페이지는 어디서 온 자원만 쓴다"를 브라우저에 알린다. `default-src 'self'`면 같은 출처의 파일만 불러오고 인라인 `<script>`·`style=""`은 막힌다. 코드 규칙이 한 번 깨져도 스크립트가 실행되지 않게 하는 두 번째 벽이다. 대가로 화면 코드도 인라인 스크립트·스타일을 쓸 수 없다. 그래서 테마를 첫 그림 전에 적용하는 짧은 스크립트도 따로 파일(`theme-init.js`)이다.

**`nosniff`와 MIME 타입.** `X-Content-Type-Options: nosniff`는 브라우저가 내용을 보고 형식을 추측하지 못하게 한다. 그러면 서버가 알려 주는 `Content-Type`이 정확해야 한다. Windows의 `mimetypes`는 레지스트리를 읽어 `.js`를 `text/plain`으로 알려 주기도 하고, 그러면 브라우저가 모듈 스크립트를 실행하지 않는다. 그래서 `app.py`가 `.js`·`.css`·`.svg`의 형식을 직접 등록한다.

**색인 상태 `stale`.** 색인한 뒤 문서를 올리면 그 문서는 답에 나오지 않는다. 사용자는 "올렸는데 왜 안 나오지"가 된다(Review Focus 3). 그래서 `index/index.json`에 색인에 든 `doc_id` 목록을 남기고, 지금 문서 목록과 다르면 `stale`이다. 목록이 없는 예전 `index.json`이나 깨진 파일도 `ready`가 아니라 `stale`로 읽는다. 모를 때는 다시 색인하라고 하는 쪽이 안전하다. 임베딩 모델이 바뀐 색인도 `stale`이다.

**`passages`와 권한.** 근거 패널에 원문을 보여 주려면 청크 본문이 필요하다. "`chunk_id`로 원문을 주는 API"를 따로 만들면 권한 밖 문서의 본문을 꺼낼 길이 생긴다. 그래서 원문은 **그 질문에서 그 역할로 검색된 청크** 안에서만 찾아 `ask` 응답에 함께 넣는다(`answer_with_usage`). 색인 전체에서 찾지 않는다.

**상태와 이벤트.** 프레임워크가 없어서 "무엇이 바뀌면 무엇을 다시 그리나"를 직접 정한다. `state.js`에 상태 객체 하나와 `on(이벤트, 함수)`/`emit(이벤트)`이 있다. `actions.js`가 서버를 부르고 상태를 바꾼 뒤 `emit`하면, 구독한 화면 조각이 다시 그린다. 대화 기록은 메모리에만 두므로 새로 고치면 사라진다.

## 왜 이렇게 했나 (대안과 비교)

- **Streamlit 대신 정적 화면.** 프로세스와 의존성이 늘지 않고, 외부 요청 없이 뜨고, 화면 구성을 직접 정할 수 있다. 대가는 상태 관리를 직접 쓴 것과 프런트 단위 테스트가 없는 것이다. React+번들러, HTMX와의 비교는 [ADR 0004](../adr/0004-web-ui-static.md)에 있다.
- **프런트 테스트 대신 파이썬 정적 검사.** [`test_static_ui.py`](../../tests/api/test_static_ui.py)가 `static/`의 파일을 글자로 읽어 `innerHTML`·`eval(`·외부 URL·인라인 이벤트 속성 등이 있으면 실패한다. `index.html`이 참조하는 파일과 JS의 상대 경로 `import`가 실제로 있는지도 본다. 번들러가 없어서 깨진 import는 브라우저에서야 드러나기 때문이다. 이 검사는 "위험한 API를 쓰지 않았다"만 보장한다. 동작이 맞는지는 사람이 브라우저에서 확인한다.
- **예시 워크스페이스는 업로드와 같은 길로.** `create_demo`는 파일을 직접 복사하지 않고 `add_document()`를 부른다. 형식·서명·파싱 검사를 똑같이 거치고 저장 모양도 같다. 도중에 실패하면 만든 것을 모두 지운다. 반쯤 만든 `demo`가 남으면 다시 눌러도 409라서 화면에서 고칠 길이 없기 때문이다.
- **조회 API는 색인을 열지 않는다.** 목록 화면이 뜰 때마다 Qdrant나 임베딩 모델을 열면 느리고 GPU를 쓴다. `index.json`만 읽는다. 이 파일은 통째로 바꿔 끼우므로 잠금 없이 읽어도 반쯤 쓴 내용을 보지 않는다.
- **오류는 그 자리에 문장으로.** LLM이 꺼져 있으면(503) 화면이 멈추지 않고 이유와 할 일(`ollama serve`)을 보여 준다(Review Focus 4). `errors.js`가 상태 코드와 상황(질문, 업로드, 색인)에 따라 문장과 다음 동작(다시 시도, 다시 색인)을 정한다.
- **등급은 직접 고르게.** 업로드 양식의 `access_level`에는 기본값이 없다. 기본이 `public`이면 빠뜨린 내부 문서가 모두에게 보인다. 서버(`UploadMeta`)와 화면 모두 그렇게 한다.
- **정적 파일은 패키지 데이터.** `pyproject.toml`에 `"axbuilder.api" = ["static/**"]`를 넣어 `pip install` 뒤에도 화면이 뜬다. 테스트가 `importlib.resources`로 `index.html`이 있는지 확인한다.

## 코드 읽는 순서

1. [`app.py`](../../src/axbuilder/api/app.py)의 `IndexStatus`·`WorkspaceSummary`·`DocumentInfo`·`Passage`(응답 모양)
2. `app.py`의 `_index_status`(none·ready·stale 판정), `summary`, `detail`
3. `app.py`의 `answer_with_usage`(`passages`를 어디서 찾는가), `create_demo`와 `_discard_demo`
4. `app.py`의 `CONTENT_SECURITY_POLICY`, `_SecurityHeaders`(모든 응답의 보안 머리글), `create_app` 끝의 `GET /`와 `app.mount("/static", …)`
5. [`static/js/main.js`](../../src/axbuilder/api/static/js/main.js)(시작점) → `state.js` → `api.js` → `actions.js`
6. [`static/js/dom.js`](../../src/axbuilder/api/static/js/dom.js)의 `h()`, [`answer.js`](../../src/axbuilder/api/static/js/answer.js)의 `parseInline`·`renderAnswer`
7. `static/js/views/`의 `ask.js`(질문 탭), `evidence.js`(근거 패널), `documents.js`(문서 탭), `errors.js`
8. 테스트: [`tests/api/test_ui_api.py`](../../tests/api/test_ui_api.py), [`tests/api/test_static_ui.py`](../../tests/api/test_static_ui.py)

## 면접 예상 질문과 답

**Q1. 왜 React나 Streamlit을 쓰지 않았나요?**
이미 FastAPI 서버가 있어서 그 서버가 정적 파일을 내주면 프로세스도 의존성도 늘지 않습니다. 폐쇄망 고객을 생각해 외부 요청이 0건이어야 했고, 인용을 누르면 원문이 열리는 화면을 직접 구성하고 싶었습니다. 대가로 상태 관리를 직접 썼고 프런트 단위 테스트가 없습니다. 화면이 더 커지면 다시 따져 볼 결정입니다.

**Q2. 문서 제목에 스크립트가 들어 있으면 어떻게 되나요?**
글자로 보입니다. 서버에서 온 글자는 텍스트 노드로만 넣고, 답변의 굵게·목록·인용도 문자열을 토큰으로 잘라 노드를 직접 만듭니다. 그 위에 CSP가 인라인 스크립트를 막고, `innerHTML` 같은 API가 코드에 들어오면 실패하는 테스트가 CI에서 돕니다. 한 겹이 뚫려도 다음 겹이 막게 했습니다.

**Q3. 프런트엔드 테스트 없이 품질을 어떻게 확인했나요?**
세 가지로 나눴습니다. 화면이 쓰는 API는 파이썬 테스트가 검증합니다. 위험한 코드 패턴과 깨진 파일 참조는 정적 검사 테스트가 잡습니다. 실제 동작은 실제 서버와 모델을 띄우고 브라우저에서 처음부터 끝까지 눌러 확인했습니다. 화면 동작의 회귀를 자동으로 못 잡는다는 한계는 남아 있습니다.

**Q4. 근거 원문을 보여 주는 API를 따로 두지 않은 이유는요?**
`chunk_id`로 원문을 주는 API는 권한 검사를 한 번 더 해야 하고, 빠뜨리면 내부 문서가 새는 길이 됩니다. 그래서 원문은 질문 응답에 함께 넣되, 그 질문에서 그 역할로 검색된 청크 안에서만 찾습니다. 권한 밖 청크는 검색 단계에서 이미 빠졌으므로 나갈 수 없습니다. public 역할의 `passages`에 내부 문서 본문이 없다는 테스트가 있습니다.

**Q5. 색인 상태를 왜 세 가지로 나눴나요?**
문서를 올려도 다시 색인하기 전에는 답에 나오지 않기 때문입니다. 색인에 든 문서 목록을 남겨 지금 문서와 다르면 `stale`로 알립니다. 판단할 정보가 없는 예전 파일이나 깨진 파일도 `stale`로 봅니다. "최신"이라고 잘못 말하는 것보다 다시 색인하라고 하는 쪽이 안전합니다.
