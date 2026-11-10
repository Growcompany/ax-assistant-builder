# Task 0 — 개발 환경과 작업 흐름

## 이 작업이 하는 일

- 패키지 이름을 `rag_app` → `axbuilder`로 바꾼다 (`src/axbuilder/__init__.py`, `__version__ = "0.1.0"`).
- `pyproject.toml`에 M-1에서 쓸 라이브러리를 넣되, **기본 의존성 / `dev` / `ml`** 세 묶음으로 나눈다.
  - 기본: pydantic, kiwipiepy, pdfplumber, PyYAML, qdrant-client, anthropic, httpx, fastapi, uvicorn, python-multipart
  - `dev`: pytest, ruff (테스트·린트 도구)
  - `ml`: sentence-transformers (PyTorch를 끌어오는 무거운 묶음)
- pytest에 `slow` 마커를 등록하고 기본 실행에서 제외한다 (`addopts = "-m 'not slow'"`).
- `.gitignore`의 `**/data/workspaces/` 줄로 워크스페이스 데이터·색인이 커밋되지 않게 한다(Task 0에서는 `data/workspaces/`였고, Task 3에서 어느 깊이에 있어도 제외되도록 `**/`를 붙였다).
- CI의 `actions/checkout`, `actions/setup-python`을 v7로 올리고, CI는 `.[dev]`만 설치한다.

## 핵심 개념

**가상환경(venv)**
프로젝트마다 따로 쓰는 파이썬 설치본. 전역 파이썬에 패키지를 섞지 않으려고 쓴다. 이 프로젝트는 루트의 `.venv`(Python 3.13)를 쓰고, 명령은 항상 `.venv/Scripts/python -m ...`으로 실행해서 "어느 파이썬인지" 헷갈릴 여지를 없앤다.

**편집 설치 (`pip install -e .`)**
패키지를 site-packages에 복사하지 않고 "소스 폴더를 가리키는 링크"만 설치한다. `src/axbuilder/`를 고치면 재설치 없이 바로 반영된다. 패키지 이름을 바꿨기 때문에 예전 편집 설치(`rag-app`)는 `pip uninstall`로 지웠다 — 남겨 두면 존재하지 않는 경로를 가리키는 찌꺼기가 된다.

**optional extras (`[project.optional-dependencies]`)**
`pip install -e ".[dev]"`처럼 대괄호로 고르는 선택 의존성 묶음. "누가 무엇을 위해 설치하나"에 따라 나눈다.
- 개발자·CI: `.[dev]` → 수 분 안에 설치, GPU 불필요
- 실제 임베딩 모델을 돌릴 때: `.[dev,ml]` + PyTorch CUDA 빌드 (수 GB)

**pytest marker**
테스트에 붙이는 태그. `@pytest.mark.slow`가 붙은 테스트(실제 모델·네트워크 사용)는 `addopts = "-m 'not slow'"` 때문에 기본 `pytest -q`에서 빠지고, `pytest -m slow`로 따로 돌린다. `markers`에 등록하지 않은 마커를 쓰면 pytest가 경고를 띄우므로 이름도 등록해 둔다.

**stacked PR**
PR의 base를 `main`이 아니라 **바로 앞 작업의 브랜치**로 두는 방식. `main ← chore/agent-team(#1) ← task/00-setup ← task/01-…` 처럼 쌓인다. 앞 PR이 아직 머지되지 않았어도 다음 작업을 이어서 리뷰받을 수 있고, 각 PR의 diff에는 그 작업의 변경만 보인다.

## 왜 이렇게 했나 (대안과 비교)

| 결정 | 대안 | 고른 이유 |
|---|---|---|
| sentence-transformers를 `ml` extra로 분리 | 기본 의존성에 포함 (계획 원안) | sentence-transformers는 torch를 끌어온다. CPU torch도 수백 MB, CUDA 빌드는 수 GB라 CI가 느려지고 이 PC는 C 드라이브 여유가 약 30GB뿐이다. 단위 테스트는 가짜 임베더(`HashEmbedder`)로 돌기 때문에 torch가 필요 없다 |
| CI는 `.[dev]`만 설치 | `.[dev,ml]` 설치 | "단위 테스트는 네트워크·GPU·모델 없이 통과" 원칙을 CI가 강제로 지키게 된다. 누가 실수로 torch를 import하는 테스트를 `slow` 없이 넣으면 CI가 바로 실패해서 알려 준다 |
| `slow`를 `addopts`로 기본 제외 | 매번 `-m "not slow"`를 직접 입력 | 기본값이 안전한 쪽(빠른 테스트)이어야 실수가 줄어든다 |
| 액션을 v7로 올림 | v4/v5 유지 | 구버전은 지원이 끝나는 Node 20 런타임을 써서 경고가 뜬다. 최신 메이저는 GitHub API(`/releases/latest`)로 확인했다 |
| `PIP_CONFIG_FILE=nul` | 시스템 pip 설정 수정, `pip --isolated` | 시스템 설정(`C:\ProgramData\pip\pip.ini`)에 접속 안 되는 미러가 있지만, PC 설정은 건드리지 않는 것이 원칙이다. `--isolated`는 환경변수·사용자 설정만 무시하고 시스템 설정은 그대로 읽는다(`pip config debug --isolated`로 확인). 그래서 이 작업의 설치는 죽은 미러 때문에 10분 넘게 걸렸다. `PIP_CONFIG_FILE`을 빈 장치(`nul`)로 지정하면 설정 파일을 모두 건너뛴다 |
| stacked PR | 앞 PR 머지를 기다렸다가 `main`에서 분기 | 아직 머지 권한을 받지 않았다. 기다리는 대신 쌓아 두면 리뷰가 병렬로 진행된다. 단점은 아래 질문 참고 |

## 코드 읽는 순서

1. `pyproject.toml` — `[project.dependencies]` → `[project.optional-dependencies]` → `[tool.pytest.ini_options]`
2. `src/axbuilder/__init__.py` — 패키지 이름과 버전
3. `tests/test_smoke.py` — 패키지가 import되고 버전이 맞는지만 보는 최소 테스트
4. `.github/workflows/ci.yml` — 설치(`.[dev]`) → 린트 → 테스트 순서
5. `.gitignore` — `**/data/workspaces/` 줄

## 면접 예상 질문과 답

**Q1. 왜 sentence-transformers를 기본 의존성에서 뺐나요?**
torch가 따라오기 때문입니다. 단위 테스트는 가짜 임베더로 돌게 설계해서 torch가 필요 없고, CI 설치 시간·디스크를 아끼면서 "테스트가 모델에 의존하지 않는다"는 규칙도 CI가 자동으로 검증하게 됩니다. 실제 모델이 필요한 사람만 `.[ml]`을 설치합니다.

**Q2. `pip install -e`는 일반 설치와 뭐가 다른가요?**
일반 설치는 코드를 site-packages로 복사하고, 편집 설치는 소스 폴더를 가리키게만 합니다. 그래서 코드를 고칠 때마다 다시 설치할 필요가 없습니다. 대신 패키지 이름이나 폴더를 바꾸면 예전 링크를 지우고 다시 설치해야 합니다.

**Q3. 느린 테스트는 어떻게 관리하나요?**
`@pytest.mark.slow`로 표시하고 `addopts`로 기본 제외합니다. CI와 평소 개발은 빠른 테스트만, 실제 모델·네트워크 확인은 `pytest -m slow`로 따로 돌립니다.

**Q4. stacked PR의 단점은요?**
아래 PR이 squash 머지되면 커밋 해시가 바뀌어서, 위 PR의 base를 `main`으로 바꾸고 rebase해야 할 수 있습니다. 또 아래 PR에서 리뷰 수정이 생기면 위 브랜치들에 전파해야 합니다. 그래서 각 PR을 작게 유지하고 순서대로 머지합니다.

**Q5. CI에서 Python 3.13만 테스트하는데 `requires-python = ">=3.11"`인 이유는요?**
라이브러리 호환 범위를 열어 두기 위해서입니다. 3.13 설치가 막히면 3.12로 내려갈 수 있게 해 둔 것이고, 지금은 개발 환경과 같은 3.13 하나만 CI에서 검사합니다. 여러 버전을 보장해야 할 때 matrix를 추가하면 됩니다.
