# AX Assistant Builder — 작업 규칙

회사 문서를 넣으면 그 회사 전용 RAG 어시스턴트를 만들어 주는 플랫폼. 대표 사례는 가상 은행 워크스페이스.
학사 졸업자의 AI/AX 엔지니어 지원용 메인 포트폴리오이며, 저장소는 공개(https://github.com/Growcompany/ax-assistant-builder).

- 설계(무엇을·왜): `docs/superpowers/specs/2026-11-09-company-assistant-builder-design.md`
- 현재 계획(어떻게): `docs/superpowers/plans/2026-11-09-m1-thin-e2e.md` — **Global Constraints**와 각 Task의 Interfaces가 기준이다. 이름·시그니처·문구를 임의로 바꾸지 말 것
- 조사 근거: `docs/research/`

## 명령 (Windows, Git Bash 기준)

```bash
.venv/Scripts/python -m pytest -q                 # 단위 테스트 (slow 제외)
.venv/Scripts/python -m pytest -q -m slow         # 실제 모델·네트워크 테스트
.venv/Scripts/python -m ruff check . && .venv/Scripts/python -m ruff format --check .
.venv/Scripts/python -m pip install --isolated -e ".[dev]"   # --isolated: 깨진 전역 pip 미러 설정 무시
```

- 가상환경은 프로젝트 루트의 `.venv` (Python 3.13). 전역 pip 설정은 수정하지 말 것
- C 드라이브 여유 공간이 적다(약 30GB). 큰 모델·패키지는 계획에 있는 것만 받는다

## 작업 흐름

1. `main`을 최신으로 맞춘 뒤 `task/NN-<이름>` 브랜치 생성
2. 테스트 먼저(TDD): 계획의 테스트를 옮기고 실패 확인 → 구현 → 통과
3. `ruff check`, `ruff format`, `pytest -q` 통과 후 커밋 (작게, 의미 단위로)
4. 학습 노트 `docs/learning/task-NN-<이름>.md` 작성 (아래 형식)
5. 푸시 → PR (한국어 설명: 무엇을, 왜, 어떻게 확인했는지, 리뷰 포인트) → 리뷰 에이전트 → 수정 → CI 통과 → squash 머지

커밋 메시지는 Conventional Commits(`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`) 영어 한 줄 + 필요하면 본문.
커밋 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, PR 설명 끝에 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## 학습 노트 형식 (`docs/learning/`)

사용자가 면접에서 직접 설명해야 하므로 작업마다 한국어로 짧게(1~2쪽):
`## 이 작업이 하는 일` / `## 핵심 개념` / `## 왜 이렇게 했나 (대안과 비교)` / `## 코드 읽는 순서` / `## 면접 예상 질문과 답` (3~5개)

## 지켜야 할 것

- **GitHub은 `Growcompany/ax-assistant-builder` 저장소만** 다룬다. 계정의 다른 저장소·설정은 절대 건드리지 않는다
- 원본 문서(은행 PDF 등)·`data/workspaces/`·`.env`·API 키는 커밋 금지. 출처 목록(`corpus/*/sources.yaml`)과 다운로드 스크립트만 커밋
- 단위 테스트는 네트워크·GPU·모델 다운로드 없이 통과해야 한다. 실제 모델을 쓰면 `@pytest.mark.slow`
- Claude API: `claude-opus-5-5` 기본, `temperature`/`top_p`/`top_k` 금지(400 오류), `stop_reason == "refusal"` 처리
- 시스템 소프트웨어 설치(Ollama, Docker 등)와 유료 API 호출은 사용자 승인 후에만
- 문서·PR·학습 노트는 한국어, 코드 식별자는 영어. 주석은 "왜"가 필요한 곳에만 짧게

## 전문가 에이전트 (`.claude/agents/`)

| 에이전트 | 언제 |
|---|---|
| `axb-implementer` | 계획의 Task 하나를 브랜치·TDD·PR까지 구현 |
| `axb-reviewer` | PR을 계획·설계·Review Focus 기준으로 리뷰 (구현자와 다른 에이전트) |
| `axb-eval-engineer` | 골든셋, 지표, 실험 설계·리포트 |
| `axb-data-curator` | 예시 문서 출처·라이선스, 다운로드 스크립트, 가상 내규 작성 |
| `axb-security-reviewer` | 개인정보 마스킹, 권한(ACL), 프롬프트 인젝션, 비밀값 노출 점검 |
| `axb-docs-writer` | README, ADR, 학습 노트, 실험 기록 |
