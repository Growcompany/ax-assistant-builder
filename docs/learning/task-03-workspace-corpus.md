# Task 3 — 워크스페이스와 가상 은행 코퍼스

## 이 작업이 하는 일

- `src/axbuilder/settings.py`: `Settings.from_env()` — `AXB_*` 환경변수 6개(데이터 폴더, LLM 모드, 로컬·클라우드 모델, Ollama 주소, 임베딩 모델)를 읽고, 없거나 빈 값이면 기본값.
- `src/axbuilder/workspace.py`: `Workspace(id, name, industry, roles)`와 `WorkspaceStore(root)` — 설정을 `root/{id}/workspace.yaml`에 저장·조회·나열. id는 `^[a-z0-9-]{2,32}$`만 허용.
- `src/axbuilder/ingest/loaders.py`: `.hwpx`(공정위 표준약관 형식) 읽기 추가.
- `src/axbuilder/ingest/chunking.py`: `chunk_document`가 **doc_type과 무관하게** 조항 머리 2개 이상이면 조항 단위. `부칙` 뒤 조항은 `"부칙 제N조"`.
- `src/axbuilder/ingest/law_html.py`: 국가법령정보센터 본문 HTML → 조문 줄 텍스트.
- `src/axbuilder/corpus.py` + `scripts/download_corpus.py`: `sources.yaml`(13개)을 검증해 읽고, url 문서 7개를 받아 `data/workspaces/bank/raw/`에 저장, `raw/manifest.json`에 sha256과 받은 시각을 남긴다.

```
corpus/bank/sources.yaml ─load_sources─▶ [CorpusSource × 13]
   url 7개 ─GET─▶ HTML → law_html_to_text → raw/{doc_id}.txt     ┐
                  HWPX(ZIP) 그대로        → raw/{doc_id}.hwpx    ├─ raw/manifest.json
   path 6개 ─────▶ 저장소의 corpus/bank/{synthetic,internal}/*.md ┘
source_file(s) ─▶ load_document ─▶ chunk_document ─▶ [Chunk(article="제4조의2" | "부칙 제1조" | None)]
```

## 핵심 개념

**경로 조작(path traversal) 방어**
워크스페이스 id와 doc_id는 그대로 폴더·파일 이름이 된다. `../etc`가 들어오면 `data/workspaces/../etc/`처럼 데이터 폴더 밖을 읽고 쓴다. 막는 방법은 "위험한 문자 빼기"(블랙리스트)가 아니라 **허용할 문자만 정하기**(화이트리스트)다. `[a-z0-9-]{2,32}`에는 `/`, `\`, `.`이 없어서 어떤 조합으로도 상위 폴더로 갈 수 없다. 두 가지를 더 챙겼다.
- `re.match(r"^...$")`의 `$`는 끝의 `\n` 앞에서도 맞는다. `"bank\n"`이 통과하므로 `re.fullmatch`를 쓴다(테스트로 고정).
- `con`, `nul`은 정규식을 통과하지만 Windows가 장치 이름으로 예약해 폴더로 못 만든다 → 따로 막는다.
- 검사는 디스크를 건드리기 **전에**(`WorkspaceStore.path`) 한다. `sources.yaml`의 `path`도 같은 원칙: 상대 경로만, `..`·절대 경로·드라이브 문자·역슬래시 금지, 그리고 `resolve()` 결과가 저장소 안인지 한 번 더 확인(심볼릭 링크 대비).

**설정과 코드의 분리**
모델 이름·서버 주소·데이터 위치를 코드에 박으면 바꿀 때마다 코드를 고쳐야 하고, 개발 PC·CI·서버마다 값이 달라진다. 그래서 값은 환경변수로 받고 코드는 기본값만 가진다(12-factor app의 "config" 원칙). 비밀값(`ANTHROPIC_API_KEY`)은 일부러 `Settings`에 넣지 않았다. SDK가 환경변수에서 직접 읽으므로, 설정 객체를 로그나 API 응답에 찍어도 키가 새지 않는다(테스트로 고정). 빈 값(`AXB_LLM_MODE=`)은 "설정 안 함"으로 본다 — `.env`에 빈 줄을 남겨 둔 경우 잘못된 값 오류 대신 기본값이 낫다.

**HWPX 구조**
HWPX는 한컴오피스의 개방형 형식(OWPML, KS X 6101)으로 실체는 **ZIP 안의 XML**이다. 본문은 `Contents/section0.xml, section1.xml …`에 나뉘고, 문단은 `hp:p` > `hp:run`(글자 모양이 같은 구간) > `hp:t`(글자)다. 구현에서 신경 쓴 세 가지:
1. 구역 순서: 이름순 정렬이면 `section10`이 `section2`보다 앞에 온다 → 번호를 숫자로 정렬.
2. 표: 셀 안의 문단(`hp:tbl > hp:tc > hp:subList > hp:p`)이 바깥 문단 **안에** 중첩된다. "문단의 모든 하위 글자"를 모으면 셀 내용이 두 번 나온다 → 각 `hp:t`를 **가장 가까운 상위 `hp:p`**에만 붙인다.
3. 업로드 대비: ZIP은 1MB가 수 GB로 풀릴 수 있다(zip bomb). 읽을 구역의 압축 해제 크기 합계가 50MB를 넘으면 거절한다. `zipfile`은 헤더의 크기보다 많이 풀어 주지 않아 이 합계가 실제 상한이다. XML 자체의 엔티티 폭탄(billion laughs)은 이 환경의 expat 2.7.5가 막는다. 깊게 중첩된 XML은 재귀 대신 스택으로 돌아 `RecursionError`를 피했다.

**저작권 판단 (원문을 커밋하지 않는 이유)**
| 문서 | 판단 | 처리 |
|---|---|---|
| 법령 4개, 금융위 고시 1개 | 저작권법 제7조 제1·2호: 법령·고시는 보호받지 않는 저작물 | 커밋해도 되지만 개정이 잦아 **스크립트로 현행본을 받는다**. 출처 표기 |
| 공정위 표준약관 2개 | 공공누리 표시 없음, 공정위 저작권정책상 사전 협의 대상 → 권리 관계 불명확 | **커밋 금지**, 스크립트로만 받음 |
| 실제 은행 상품설명서 | 은행의 업무상저작물(제9조) | 쓰지 않고 가상 설명서를 직접 씀 |
테스트용 HTML 조각(`tests/fixtures/law_page.html`)은 은행법 조문을 줄인 것이라 제7조 범위 안이다. 약관 HWPX는 테스트에서 `zipfile`로 직접 만든다.

## 왜 이렇게 했나 (대안과 비교)

| 결정 | 대안 | 고른 이유 |
|---|---|---|
| 조항 분할을 doc_type 대신 조항 머리 개수로 결정 | Task 2처럼 law·regulation·terms만 | 가상 내부 지침도 제N조 형식이다. 상품설명서는 줄 맨 앞 제N조가 0개라 그대로 고정 길이로 간다 |
| 부칙 머리 줄은 그 부칙의 첫 조항 청크 앞에 붙이고, 조항 없는 부칙은 `article="부칙"` | 부칙 줄을 무시(앞 조항에 붙음) / 부칙 줄만 따로 청크 | 무시하면 본문 마지막 조항(은행법 제69조)에 "부칙 <법률 …>"과 조항 없는 부칙 수십 개가 붙는다. 부칙 줄만 따로 두면 내부 지침의 "부칙" 두 글자짜리 청크가 생긴다(짧은 청크는 BM25에서 과하게 높게 나온다) |
| 첫 조항 앞 머리말을 `article=None` 청크로 유지 (PR #5 m6) | 버림 | 내부 지침의 "가상 문서" 고지와 "대외비" 표시, 법령의 시행일·법률 번호가 여기 있다 |
| HTML 파서는 표준 라이브러리 `html.parser` | BeautifulSoup·lxml | 계획에 없는 의존성을 늘리지 않는다. law.go.kr 본문은 `<p>` 하나가 조·항·호 한 줄이라 "블록 요소 = 줄"만으로 충분하다. 법령명 `<h2>` 앞의 메뉴(판례·연혁 버튼)는 버린다 |
| 받은 HTML에 조문이 2개 미만이면 실패 처리 | 상태 코드 200이면 성공 | 없는 일련번호에도 안내 페이지가 200으로 올 수 있다. 조용히 빈 문서를 색인하는 것보다 실패 목록에 뜨는 편이 낫다 |
| 로직은 `axbuilder.corpus`, 스크립트는 명령줄 껍데기 | 스크립트에 전부 | `scripts/`는 패키지가 아니라 테스트가 import하기 어렵다. 네트워크는 `httpx.MockTransport`로 흉내 내 단위 테스트에서 저장·manifest·실패 계속 진행을 확인한다 |
| 저장은 임시 파일 → `os.replace` | 바로 쓰기 | 받는 중 끊겨도 반쯤 쓴 파일이 남지 않는다. 재시도가 실패해도 전에 받은 파일과 manifest 기록은 유지 |

**알려진 한계**
- 법령 HTML에는 과거 개정의 부칙이 모두 들어 있다. 은행법은 본문 조항 청크 117개보다 부칙 조항 청크가 146개로 더 많고, `부칙 제1조(시행일)`이 개정마다 반복된다. 검색 잡음이 될 수 있어 "현행 부칙만 남기기"를 M-2 분할 실험 후보로 둔다.
- 행정규칙 HTML 끝의 별지 목록(`[별지 2] 과징금납부통지서` 등)은 마지막 부칙 청크 끝에 붙는다. 장 제목(`제2장 …`)이 앞 조항 끝에 붙는 문제(Task 2)도 그대로다.
- 긴 조항도 더 자르지 않는다. 은행법에서 가장 긴 청크는 7,124자(부칙 제9조, 다른 법률 개정)다(Task 4에서 임베딩 모델 입력 길이 확인 필요).

## 코드 읽는 순서

1. `src/axbuilder/settings.py` → `tests/test_settings.py`
2. `src/axbuilder/workspace.py` (`validate_workspace_id` → `WorkspaceStore.path/create/get/list`) → `tests/test_workspace.py`
3. `src/axbuilder/ingest/loaders.py`의 `_read_hwpx` → `_hwpx_paragraphs` → `tests/ingest/test_hwpx.py`(`make_hwpx`, 표 중복·구역 순서·zip bomb)
4. `src/axbuilder/ingest/chunking.py`의 `ADDENDA_HEADER`, `_headers`, `chunk_by_article` → `tests/ingest/test_chunking.py`의 부칙 테스트
5. `src/axbuilder/ingest/law_html.py` → `tests/fixtures/law_page.html`
6. `src/axbuilder/corpus.py` (`CorpusSource` 검증 → `load_sources` → `source_file` → `download_sources`) → `scripts/download_corpus.py` → `tests/test_download_corpus.py`

## 면접 예상 질문과 답

**Q1. 워크스페이스 id로 경로 조작 공격을 어떻게 막았나요?**
id가 폴더 이름이 되므로 허용 문자를 소문자·숫자·하이픈으로 제한하는 화이트리스트를 썼습니다. 슬래시와 점이 없어 상위 폴더로 갈 수 없습니다. 정규식은 `fullmatch`로 검사해 끝 줄바꿈 우회를 막았고, 디스크에 접근하기 전에 검사합니다. 출처 목록의 파일 경로도 상대 경로만 받고, 최종 경로가 저장소 안인지 `resolve()`로 한 번 더 확인합니다.

**Q2. API 키는 왜 Settings에 넣지 않았나요?**
설정 객체는 디버깅할 때 로그에 찍거나 상태 API로 내보내기 쉽습니다. 키를 넣어 두면 그 순간 유출됩니다. Anthropic SDK가 환경변수에서 키를 직접 읽으므로 우리 코드가 키를 들고 다닐 이유가 없습니다. 테스트로 `repr`과 JSON에 키가 없음을 고정했습니다.

**Q3. HWPX를 읽을 때 표 내용이 두 번 나오는 문제는 왜 생기고 어떻게 해결했나요?**
HWPX에서 표는 문단 안에 들어 있고, 셀 안에 다시 문단이 있습니다. 바깥 문단의 하위 글자를 모두 모으면 셀 글자가 바깥 문단과 셀 문단에 한 번씩, 두 번 들어갑니다. 글자 요소마다 가장 가까운 상위 문단에만 붙이도록 트리를 한 번 순회해서 해결했고, 최소 HWPX를 테스트에서 만들어 확인합니다.

**Q4. 공정위 약관 원문은 왜 저장소에 없나요?**
법령과 고시는 저작권법 제7조로 보호받지 않지만, 공정위 표준약관 게시물에는 공공누리 표시가 없고 공정위 정책상 사전 협의 대상이라 재배포 권리가 불명확합니다. 그래서 출처 URL과 다운로드 스크립트만 커밋하고, 받은 파일은 sha256과 함께 git 밖에 둡니다. 다른 사람도 같은 스크립트로 같은 파일을 받았는지 해시로 확인할 수 있습니다.

**Q5. 받은 약관을 실제로 열어 보니 무엇이 달랐나요?**
가상 주택담보대출 설명서는 금소법 기준으로 "대출 금액에 따른 철회 제한은 없다"고 쓰면서 근거로 약관 제4조의2도 인용합니다. 그런데 약관 원문 제4조의2 제2항에는 "2억원을 초과하는 담보대출은 철회할 수 없다"가 남아 있었습니다. 조 번호는 맞지만 내용이 법과 약관 사이에서 갈리는 지점이라, 평가 질문을 만들 때 정답 근거를 법 기준으로 두고 이 차이를 "오래된 근거" 유형 질문 소재로 쓰도록 PR에 보고했습니다.
