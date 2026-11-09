# Claude Code로 이 프로젝트를 효율적으로 개발하는 방법 (2026)

(2026-11-09 조사 · 조사 에이전트 작성 · 표기: [확인] 원문을 직접 열어 확인 / [검색] 검색 결과 요약만 확인 / [추정] 해석. 결정에 반영한 내용은 `00-summary.md` 6절과 M-1 계획의 '변경 기록' 참고)

## 요약
1. Anthropic의 2026년 공식 문서는 세 가지를 요구한다. CLAUDE.md에는 Claude가 코드에서 알아낼 수 없는 것만 200줄 안에 적는다. 예외 없이 지켜야 할 규칙은 hook이나 deny 규칙으로 강제한다. Claude가 직접 돌려 볼 수 있는 통과/실패 체크(테스트·린트)를 준다. [확인] S1, S6
2. 서브에이전트는 답의 품질을 올리지 않는다. 쓸모는 병렬 작업, 긴 출력 격리, 구현을 보지 않은 리뷰어 세 가지이고 토큰이 든다. 2026-10 Anthropic 글은 에이전트를 너무 많이 만드는 문제("subagent sprawl")를 경고하므로 4~5개로 줄이는 편이 맞다. [확인] S1, S12, S15
3. Anthropic 내부와 커뮤니티에서 효과가 확인된 패턴은 같다. 작업 1개 = 브랜치 1개 = 작은 PR 1개, 새 컨텍스트에서 도는 별도 리뷰어, 실행한 명령과 출력이라는 증거 요구, 테스트를 약하게 고치는지 감시. [확인] S14, S19, S23
4. 이 저장소에는 열린 PR #1에 CLAUDE.md와 에이전트 6개가 이미 있다. 남은 보완점은 다음과 같다. [확인] S39
   - 리뷰어가 파일을 못 고치게 막기, docs-writer를 skill로 흡수
   - ruff 포맷 hook과 커밋 전 검사 hook (이 PC에는 jq가 없어 Python으로 작성)
   - deny 규칙, worktree에서 쓸 가상환경(.venv) 경로 주의
5. GitHub Actions에서 Node 20은 2026-09-23에 제거됐다. 지금 CI는 checkout@v4·setup-python@v5를 Node 24로 강제 실행한다는 경고를 띄운다. checkout@v7·setup-python@v7(둘 다 node24)로 올리고, ruff 버전 고정, CPU용 torch 설치, concurrency, permissions를 추가한다. [확인] S33, S29, S31, S39

## 공식 권고

### 1. 기본 원칙 (2026 docs)
- 2025년 엔지니어링 블로그 "Claude Code best practices" 주소는 이제 docs로 308 리다이렉트된다. 지금 기준 문서는 docs다. [확인] S18 → S1
- 지시는 두 종류로 나뉜다. [확인] S1
  - 비결정적: 프롬프트, CLAUDE.md, skill. 모델이 판단해서 따른다.
  - 결정적: hook, deny 규칙, managed settings. 하네스가 매번 강제한다.
  - CLAUDE.md의 한 줄을 계속 강조해도 무시된다면 그 줄은 hook으로 옮길 대상이다.
- 기본값: 터미널·VS Code 세션은 auto mode로 시작한다. 모델은 Opus 5.5, effort는 medium이다. 서브에이전트 위임과 컨텍스트 압축은 자동으로 일어난다. [확인] S1
- "끝났다"의 기준은 실행 가능한 체크로 만든다. 단계는 프롬프트 → `/goal` → Stop hook → 독립 리뷰(`/code-review`) 순으로 강해진다. 어느 단계든 Claude에게 증거(실행한 명령과 출력)를 요구한다. [확인] S1
- 지금은 버려도 되는 예전 조언: IMPORTANT·대문자 강조, "think hard", "포기하지 말고 계속해", CLAUDE.md에 아키텍처·파일 구조 서술, 컨텍스트 보호용 서브에이전트. [확인] S1

### 2. CLAUDE.md 작성법
- 파일당 200줄 미만을 목표로 한다. 길어질수록 지시를 따르는 정도가 떨어진다. `@import`는 정리용일 뿐 컨텍스트 비용은 줄지 않는다(최대 4단계). [확인] S6
- 넣을 것 [확인] S1:
  - 정확한 테스트·타입체크·린트 명령과 테스트 파일 하나만 돌리는 법
  - 코드가 오해를 부르는 곳
  - 저장소 밖에서 내린 결정
  - 두 번 이상 정정한 것
- 넣기 전 판단 기준: "코드를 읽으면 알 수 있는가? 실제로 찾아볼 것인가?" 둘 다 예라면 뺀다. [확인] S1
- 개인 설정은 `CLAUDE.local.md`에 두고 gitignore한다. 경로별 규칙은 `.claude/rules/*.md`에 `paths:` frontmatter로 둔다. 여러 단계짜리 절차는 skill로 옮긴다. [확인] S1, S6
- `/init`으로 초안을 만들고 `/doctor`로 줄인다. `/doctor prompt-audit`은 낡은 지시와 서로 모순되는 지시를 찾아 준다. `/context all`로 실제 로드 여부를 확인한다. [확인] S1, S6
- CLAUDE.md에 `# Compact instructions` 절을 두면 압축할 때 무엇을 남길지 지정할 수 있다. [확인] S7
- CLAUDE.md가 없으면 `AGENTS.md`를 대신 읽는다(v2.1.277 이상). [확인] S6

### 3. 서브에이전트 정의 형식 (`.claude/agents/*.md`)
YAML frontmatter와 본문으로 구성되고, 본문이 시스템 프롬프트가 된다. 필수 필드는 `name`, `description` 둘뿐이다. 필드 이름은 대소문자를 구분하는 camelCase이고, 오타 난 필드는 오류 없이 무시된다. `claude --debug` 로그에서 `does not recognize or apply`를 검색하면 찾을 수 있다. [확인] S2

| 필드 | 의미 |
|---|---|
| `name`, `description` | 필수. description을 보고 자동 위임 여부가 정해진다. "use proactively" 같은 표현은 위임을 늘린다 |
| `tools` / `disallowedTools` | 허용 목록 / 금지 목록(금지가 먼저 적용됨). 생략하면 모든 도구를 상속한다 |
| `model` | `sonnet`·`opus`·`haiku`·`fable`·전체 ID·`inherit` |
| `permissionMode` | `default`·`acceptEdits`·`auto`·`dontAsk`·`bypassPermissions`·`plan` |
| `effort` | `low`~`max` |
| `maxTurns`, `skills`(미리 로드), `mcpServers`, `hooks`(PreToolUse/PostToolUse/Stop) | — |
| `memory` | `project`이면 `.claude/agent-memory/<name>/`. MEMORY.md 앞 200줄 또는 25KB가 시스템 프롬프트에 들어간다 |
| `isolation: worktree` | 임시 worktree에서 실행. 변경이 없으면 자동 삭제 |
| `background`, `color`, `omitClaudeMd`(v2.1.271+), `autoCompactWindow`(v2.1.296+), `initialPrompt`, `experimental.cacheTtl` | — |

- 시작할 때 로드되는 것: CLAUDE.md 전체 계층(`omitClaudeMd`로 끌 수 있음, Explore/Plan은 제외), git status, `skills`에 적은 skill. 대화 기록은 넘어가지 않는다. [확인] S2
- 중첩은 기본 3단계까지 가능하다(`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`). 메인 대화에서 띄운 서브에이전트는 기본적으로 백그라운드에서 돈다. [확인] S2
- 호출 방법: description 기반 자동 위임, 이름 지정, `@agent-<name>`(반드시 그 에이전트로 실행), `claude --agent <name>`. `/tasks`로 진행을 본다. [확인] S2
- 권장 [확인] S2, S1:
  - 최소 권한. 리뷰어는 `tools: Read, Grep, Glob`이면 하네스가 수정을 막아 준다.
  - 단순 작업은 `model: haiku`.
  - 요약만 짧게 돌려받는다.
  - description은 짧게. 전체 description 합이 15k 토큰을 넘으면 시작할 때 경고가 뜬다.
  - 꼭 전달할 규칙은 위임 프롬프트에 다시 적는다.
- Anthropic 블로그(2026-04-07) 기준 [확인] S12:
  - 쓸 때: 10개 이상 파일을 탐색할 때, 독립 작업이 3개 이상일 때, 새 시각의 리뷰가 필요할 때, 커밋 전 검증
  - 피할 때: 앞 단계 결과에 의존하는 순차 작업, 같은 파일 편집, 작은 작업, 전문 에이전트가 너무 많아 자동 위임이 흐려질 때
- Anthropic 글(2026-10-22): 하나의 루프 + skill로 필요한 맥락만 그때그때 불러오는 방식이 품질·비용·지연에서 더 나은 경우가 많다. [확인] S15
  - 위임할 때마다 정보가 손실되고("telephone game"), 비슷한 에이전트가 늘어나는 "subagent sprawl"이 생긴다.
  - 서로 결과를 이어 받는 전문가들은 하나로 합치고, 나눌 때는 맥락 단위로 나눈다.

### 4. Skills
- 위치는 `.claude/skills/<name>/SKILL.md`이다. `description`(+`when_to_use`, 1,536자에서 잘림)은 항상 컨텍스트에 있고, 본문은 호출될 때만 로드된다. [확인] S3
- 주요 필드 [확인] S3:
  - `disable-model-invocation: true`: 사용자만 호출 가능. 배포·커밋처럼 부작용 있는 절차에 쓴다.
  - `user-invocable: false`: `/` 메뉴에 안 보이고 Claude만 호출한다.
  - `allowed-tools`: 권한 허용 목록일 뿐 제한이 아니다.
  - `context: fork`, `agent`, `paths`, `model`, `effort`, `hooks`
  - `` !`cmd` ``: 본문에 명령 실행 결과를 미리 넣는다.
- `.claude/commands/*.md`도 계속 동작하지만 새로 만들 때는 skill이 낫다. SKILL.md는 500줄 미만으로 두고 참고 자료는 별도 파일로 뺀다. [확인] S3
- 기본 제공 skill: `/code-review`, `/verify`, `/batch`, `/debug`, `/loop`, `/doctor`. 이 환경에는 `/security-review`도 있다. [확인] S3, S39
- Anthropic 팀의 교훈(2026-06-03) [확인] S13:
  - 가장 가치 있는 내용은 실제로 실패했던 함정(Gotchas) 목록이다.
  - 당연한 지시와 과하게 세세한 단계는 뺀다. 목표와 제약만 적는다.
  - description은 사람용 요약이 아니라 언제 쓰는지(트리거)를 적는다.
  - 몇 줄과 함정 하나로 시작해 키운다.
  - 품질에 가장 크게 기여한 범주는 "제품 검증" skill이었다.

### 5. Hooks
- 이벤트 [확인] S4:
  - 세션: `SessionStart`/`SessionEnd`
  - 도구: `PreToolUse`/`PostToolUse`/`PostToolUseFailure`/`PostToolBatch`
  - 턴·서브에이전트: `Stop`/`SubagentStop`, `UserPromptSubmit`
  - 기타: `PreCompact`/`PostCompact`, `WorktreeCreate`/`WorktreeRemove`, `TaskCompleted`, `FileChanged` 등
- 구조는 이벤트 → `matcher`(예: `Edit|Write`) → 핸들러다. 핸들러 종류는 `command`·`http`·`mcp_tool`·`prompt`·`agent`. 강제가 목적이면 `command`나 `http`를 쓴다. [확인] S4, S1
- 종료 코드 2 [확인] S4:
  - PreToolUse: 도구 호출을 차단한다.
  - PostToolUse: 차단하지 못하고 stderr를 Claude에게 보여 준다.
  - Stop: 멈추지 못하게 하고 작업을 이어 가게 한다.
  - 1이나 다른 코드는 차단이 아니다.
- `if` 필드는 권한 규칙 문법 하나를 받는다(예: `"Bash(git commit *)"`). 도구 이벤트에서만 평가된다. `onFailure: "block"`(v2.1.295+)은 hook 자체가 고장 났을 때도 동작을 막는다. [확인] S4
- Windows [확인] S4:
  - `shell` 기본값은 Git Bash가 있으면 `bash`, 없으면 `powershell`이다.
  - 파일 도구의 `tool_input.file_path`는 항상 절대경로이고 역슬래시로 온다.
  - 문서 예시는 jq를 쓰므로 jq가 없으면 Python으로 파싱하라고 안내한다(S5).
- Stop hook은 연속 8번 막으면 해제된다. 무한 반복을 막으려면 `stop_hook_active`를 확인한다. [확인] S1, S5
- worktree에 들어가도 `$CLAUDE_PROJECT_DIR`는 처음 연 저장소 경로에 고정된다. 실제 작업 경로는 입력 JSON의 `cwd`로 받는다. [확인] S8
- 커밋한 `.claude/settings.json`의 hook은 폴더 신뢰(workspace trust)를 수락한 뒤에 실행된다. `/hooks`로 확인한다. [확인] S1, S5

### 6. Worktree·병렬 작업
- `claude -w <name>`을 쓰면 `.claude/worktrees/<name>/`에 `worktree-<name>` 브랜치가 생긴다. `.claude/worktrees/`는 gitignore에 넣는다. [확인] S8
- worktree는 새 checkout이라 `.env`와 의존성이 없다. 작은 파일은 `.worktreeinclude`로 복사하고, 큰 디렉터리는 `worktree.symlinkDirectories` 설정을 쓰거나 설치 명령을 CLAUDE.md에 적는다. [확인] S1, S8
- 서브에이전트 worktree는 기본적으로 원격 기본 브랜치에서 갈라진다(`worktree.baseRef: "fresh"`). 진행 중인 작업에서 갈라지려면 `"head"`로 바꾼다. [확인] S8
- 한 번에 크게 바꾸는 기계적 변경은 `/batch`가 5~30개 단위로 나눠 단위마다 worktree와 PR을 만든다. [확인] S1

### 7. 권한
- 평가 순서는 deny → ask → allow이다. deny는 auto·bypass를 포함한 모든 모드에서 유지된다. 구체적인 allow 규칙이 deny를 뚫지 못한다. [확인] S10, S1
- 규칙은 의도가 아니라 도구 호출 문자열에 매칭된다. `Read(./.env)`는 `cat .env`도 막지만, 파일을 여는 스크립트는 막지 못한다. `.claudeignore`는 효과가 없다. [확인] S1, S10
- PowerShell 규칙도 같은 문법이다(예: `PowerShell(Remove-Item *)`). [확인] S10
- OS 수준 샌드박스는 macOS·Linux·WSL2에서만 동작한다. 이 PC(네이티브 Windows)에서는 deny 규칙과 hook이 주된 방어선이다. [확인] S1
- `autoMode` 설정은 `~/.claude/settings.json`에만 둘 수 있다(프로젝트 설정의 값은 무시됨). [확인] S1

### 8. 비용·컨텍스트
- 사용량 확인은 `/usage`로 한다. 구독 플랜이면 skill·서브에이전트·MCP별 비중과 개선 권고, `f`로 바로 적용하는 기능까지 나온다. [확인] S7
- 엔터프라이즈 평균은 활성일 개발자당 약 13달러다. [확인] S7
- 습관 [확인] S1, S7:
  - 관련 없는 작업 사이에는 `/clear`(비용 0)
  - 압축할 때는 남길 것을 지정: `/compact <남길 것>`
  - 두 번 정정해도 틀리면 `/clear` 후 새 프롬프트
  - 문제가 생기면 Esc와 `/rewind`
- effort [확인] S1, S16:
  - low: 브레인스토밍·스케치
  - medium: 일반 기능 구현
  - high/xhigh: 남의 코드 버그, 경계 조건이 중요할 때
  - max: 어려운 자율 작업만
  - 한 턴만 깊게 생각시키려면 `ultrathink`
- 프롬프트 캐시는 구독이면 약 1시간, API 키면 5분, 서브에이전트는 5분이다. [확인] S1
- 무인 실행은 `claude -p`에 `--max-turns`와 `--max-budget-usd`를 붙이고, 결과는 PR로 받아 사람이 승인한다. [확인] S1

### 9. 에이전트 팀 (실험 기능)
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`로 켠다. 팀원끼리 직접 메시지를 주고받고 작업 목록을 공유한다. [확인] S9
- plan mode에서는 일반 세션보다 토큰을 약 7배 쓴다. 세션을 재개하면 팀원이 복원되지 않는다. 화면 분할은 Windows Terminal과 VS Code 터미널에서 지원되지 않는다. [확인] S7, S9
- 같은 파일을 편집하거나 순차 의존이 있는 작업에는 서브에이전트나 단일 세션이 낫다. [확인] S9
- M-1에는 쓰지 않는 것을 권한다. [추정]

## 커뮤니티 패턴

### 작업별 브랜치·PR + 리뷰 에이전트
- Anthropic Claude Code 팀(2026-10-15) [확인] S14:
  - PR 전에 Claude가 테스트·빌드·린트를 돌리고, 새 컨텍스트의 두 번째 에이전트가 diff를 리뷰한다("show the output instead of saying it's done").
  - PR마다 CI → Claude 코드 리뷰(여러 에이전트 병렬, 지적을 Important/Nit/Pre-existing로 분류) → 별도 보안 리뷰 순으로 진행한다.
  - 지적은 다른 에이전트 그룹이 투표로 검증한 뒤에만 게시하고, 스타일 지적은 버린다.
  - "Most PRs over 1,000 lines get findings" → PR을 작게 유지한다.
  - 사람이 맡는 것: 방향·범위 결정, 위험한 코드, 증거 확인
- 리뷰 프롬프트에는 "skip style"을 넣는다. 지적하라고 하면 리뷰어는 반드시 뭔가를 찾아내고, 그걸 다 고치면 불필요한 추상화가 쌓인다. [확인] S1
- 무한 리뷰 반복은 피한다. 위 글은 한 PR에서 50라운드 넘게 돈 사례를 비생산적이라고 언급했다. [추정 — 요약에서만 확인] S14
- obra/superpowers(별 31만 개 이상, 이 PC에 설치되어 있음) [확인] S19:
  - 흐름: brainstorming → worktree → writing-plans → 작업마다 새 서브에이전트 → 2단계 리뷰(명세 준수, 그다음 코드 품질) → TDD 강제 → 브랜치 마무리
  - 이 저장소의 `docs/superpowers/` 계획이 이 흐름에서 나왔다.
- 자동 PR 리뷰를 GitHub에서 돌리려면 `anthropics/claude-code-action`(v1, `/install-github-app`으로 설치)이나 2026-10 GA된 Code Review를 쓸 수 있다. 둘 다 API 비용이 든다. [확인] S37 / [검색] S38

### TDD 루프
- 흔한 실패는 코드 대신 테스트를 약하게 고치는 것과 처음 작성한 테스트가 부실한 것이다. 테스트 파일 변경은 특히 꼼꼼히 봐야 한다. (2025-06 글) [확인] S23
- 커뮤니티 구성 [검색] S24, S25:
  - test-writer와 implementer 서브에이전트를 분리해, 테스트 설계 추론이 구현 쪽으로 새지 않게 한다.
  - PreToolUse hook(`tdd_guard.py` 같은 스크립트)으로 테스트 없는 소스 편집을 차단한다.
  - PostToolUse로 테스트를 돌려 결과를 Claude에게 바로 보여 준다.
- nizos/tdd-guard(별 2.4k)는 hook 기반 TDD 강제 플러그인으로 pytest를 지원하고 Node 22+가 필요하다. 작성자 경험상 테스트 우선을 강제해도 설계 품질(결합도·중복)은 저절로 좋아지지 않았다. [확인] S22 / [검색] S24
- 이 저장소에서는 계획에 있는 테스트 코드가 곧 명세이므로 test-writer 분리가 필요 없다. "계획 테스트 그대로 옮기기 → 실패 출력 확인 → 구현 → 리뷰어가 테스트를 계획과 비교" 순서로 충분하다. [추정]

### "전문가 에이전트 팀" 예시
- VoltAgent/awesome-claude-code-subagents(별 26.4k, 161개 이상) [확인] S20:
  - Python·AI 쪽 이름: `python-pro`, `fastapi-developer`, `llm-architect`, `ml-engineer`, `nlp-engineer`, `prompt-engineer`, `data-scientist`
  - RAG 전용 에이전트는 없다.
  - 도구 패턴: 리뷰어는 Read/Grep/Glob, 작성자는 Write/Edit/Bash 추가
- wshobson/agents(별 42.3k): 플러그인 94개, 에이전트 202개, skill 185개. 플러그인 단위로 묶었고 모델은 별칭으로 지정한다. [확인] S21
- 반론: 범용 전문가를 많이 들이는 방식은 위 Anthropic 권고(S12, S15)와 충돌한다. 이 프로젝트의 업무 흐름(구현·리뷰·평가·데이터·보안)에 맞춘 소수 정예가 낫다. [추정]

### 컨텍스트 관리
- 스펙은 인터뷰로 SPEC.md에 쓰고, 구현은 새 세션에서 그 파일로 시작한다. 리뷰도 그 파일을 기준으로 한다. [확인] S1
- 작업 하나가 끝나면 `/clear`하고, 며칠 걸리는 작업은 `/rename`과 `claude --resume <name>`으로 이어 간다. [확인] S1
- 실패한 시도가 컨텍스트에 쌓이면 Claude가 방어적이고 과설계된 코드를 쓴다. 대응은 "테스트를 통과하는 가장 단순한 것"을 요청하는 것이다. [검색] S26

### 흔한 실패 원인과 대응
| 실패 | 대응 |
|---|---|
| 큰 작업에서 일부만 하고 멈춤 [확인] S23 | 작업을 작게 쪼개고, 무인 실행이면 `/goal`이나 Stop hook [확인] S1 |
| 압축 후 이미 읽은 파일을 잊고 같은 실수를 반복 [확인] S23 | `/clear`와 새 프롬프트, Compact instructions [확인] S1, S7 |
| 테스트 약화·삭제, 고칠 수 없는 반복("doom loop") [확인] S23 / [검색] S27 | 리뷰어가 테스트 diff 확인, 정정 2회 실패 시 `/clear` |
| 임시 파일·산출물을 작업 폴더에 남김, 위험한 git 명령 [확인] S23 | `git status` 확인, force push deny, 브랜치 보호 |
| 옛 코드를 지우지 않고 "New" 함수만 추가 [확인] S23 | 리뷰 항목에 "죽은 코드" 추가 |
| 증거 없이 "통과했다"고 보고 [확인] S1, S14 | 명령과 출력 첨부를 완료 조건으로 |

## 이 저장소 적용안

### 0. 현재 상태 [확인] S39
- PR #1(`chore/agent-team`)이 열려 있다. CLAUDE.md와 `axb-implementer`·`axb-reviewer`·`axb-eval-engineer`·`axb-data-curator`·`axb-security-reviewer`·`axb-docs-writer`가 들어 있고, 모두 `name`/`description`/`tools`만 정의했다.
- **로컬 `main`에 Task 0 변경이 커밋되지 않은 채 있다**(pyproject 수정, `rag_app`→`axbuilder` 이름 변경). CLAUDE.md 흐름대로라면 `git switch -c task/00-setup`으로 옮겨야 한다.
- 최근 CI 실행 경고: "Node.js 20 is deprecated … actions/checkout@v4, actions/setup-python@v5 … forced to run on Node.js 24".
- 이 PC: Claude Code 2.1.316, ruff 0.17.4(PyPI 최신과 같음, S36), Python 3.13.13, gh 2.101.0, **jq 없음**. Claude 세션의 기본 셸은 Windows PowerShell 5.1이라 `&&`를 쓸 수 없다. [확인] S39 / 근거 문서 [추정] S40

### A. CLAUDE.md에 넣을(고칠) 항목
현재 약 70줄이라 200줄 한도 안에 있다. 아래만 더하거나 고친다.
1. **테스트 하나만 돌리는 명령** 추가. 공식 문서가 가장 쓸모 있는 줄로 꼽는다. [확인] S1
   예: `.venv/Scripts/python -m pytest tests/retrieval/test_bm25.py -q`, `-k <이름>`
2. **셸 주의**: lint 명령의 `ruff check . && ruff format --check .`는 PowerShell 5.1에서 실패한다. 두 줄로 나누거나 "Bash 도구로 실행"이라고 적는다. [확인] S39 / [추정] S40
3. **worktree 주의**: worktree에는 `.venv`가 없다. 새 venv를 만들지 말고(디스크 여유 약 30GB, CUDA torch가 venv마다 수 GB) 메인 저장소의 venv를 절대경로로 쓴다. `pytest`의 `pythonpath=["src"]` 덕분에 worktree 쪽 src가 먼저 import될 것이다. [확인] S8 / [추정]
4. **완료의 정의**: "실행한 명령과 출력 끝부분을 PR '확인 방법'에 붙인다. 출력 없이 통과라고 쓰지 않는다." [확인] S1, S14
5. **테스트 = 명세**: 계획에 있는 테스트를 구현 중에 수정하지 않는다. 바꿔야 하면 PR의 "계획과 다른 점"에 근거와 함께 적는다. [확인] S23 / [추정]
6. **리뷰 규칙**: 리뷰는 최대 2라운드. 스타일 지적은 minor로만 두고 blocker는 정확성·계획 위반·보안에 한정한다. [확인] S1 / [추정]
7. **위임 규칙**: Task 단위 구현만 `axb-implementer`에 맡기고, 작은 수정·질문은 메인에서 직접 처리한다. [확인] S12
8. `# Compact instructions` 절: 압축할 때 현재 Task 번호, 브랜치, 실패 중인 테스트, 계획과 다른 점을 남긴다. [확인] S7
9. 이미 있는 "지켜야 할 것" 가운데 예외 없는 항목(.env, force push, main 직접 push)은 아래 C의 deny 규칙과 브랜치 보호로 옮기고, CLAUDE.md에는 한 줄만 남긴다. [확인] S1

### B. 에이전트 구성 (6개 → 5개 + skill 1개 권장)
| 이름 | 한 줄 역할 | tools | model / effort / 기타 | 언제 호출 |
|---|---|---|---|---|
| `axb-implementer` | 계획 Task 하나를 브랜치·TDD·학습 노트·PR까지 | Read, Edit, Write, Grep, Glob, Bash | `inherit`(Opus 5.5) / `medium`. 병렬로 돌릴 때만 `isolation: worktree` | Task 0~11마다, 리뷰 반영 수정 |
| `axb-reviewer` | 계획·Review Focus 기준 독립 리뷰, 직접 테스트 실행, PR 코멘트 | Read, Grep, Glob, Bash + **`disallowedTools: Edit, Write`** | `opus` / `high`, `memory: project`(반복 지적 누적), `maxTurns: 40` | PR이 열린 직후 머지 전(최대 2라운드). 이어서 `/code-review`로 코드 품질 2차 리뷰 |
| `axb-eval-engineer` | 골든셋 50문항, 지표(Recall@k·MRR·인용 정밀도·거절 정확도), 실험 기록 | Read, Edit, Write, Grep, Glob, Bash | `inherit` / `high`, `memory: project` | Task 8·9, 검색 코드를 바꾼 PR마다 회귀 확인 |
| `axb-data-curator` | `corpus/bank/sources.yaml` 출처·라이선스, 다운로드 스크립트, 가상 내규 | Read, Write, Edit, Grep, Glob, Bash, WebSearch, WebFetch | `sonnet` / `medium`, `isolation: worktree`(Task 1~2와 병렬 가능) | Task 3 전후 |
| `axb-security-reviewer` | 경로 조작·업로드·개인정보 마스킹·ACL·프롬프트 인젝션·비밀값 점검 | Read, Grep, Glob, Bash + `disallowedTools: Edit, Write` | `opus` / `high` | Task 3(경로), Task 10(API 업로드), Task 11 릴리스 전. 로컬 `/security-review`와 병행 |
| ~~`axb-docs-writer`~~ → skill `learning-note` | 학습 노트 템플릿과 함정 목록 | — | 학습 노트는 implementer가 작성. README·ADR은 Task 11에서 implementer가 처리 | 매 Task 끝 |

근거: 리뷰어 쓰기 차단은 최소 권한 원칙 [확인] S2, S1. 모델과 effort 배분 [확인] S2, S16. docs-writer 흡수는 subagent sprawl 경고와 "절차는 skill로" [확인] S15, S6. `memory` 경로 [확인] S2.

보완:
- 리뷰어 frontmatter에 `hooks: PreToolUse`를 두고, matcher `Bash`에 `git commit`·`git push`·`gh pr merge`를 exit 2로 막는 스크립트를 붙이면 Bash로 우회하는 것도 막힌다. [확인] S2, S4
- 리뷰어가 PR 브랜치를 체크아웃하면 메인 작업 폴더가 바뀐다. 구현과 리뷰를 동시에 돌린다면 `isolation: worktree`를 주고 `.venv`는 절대경로로 쓴다. [확인] S8 / [추정]
- 공개 저장소이므로 `.claude/agent-memory/`가 커밋된다. 비밀값이나 개인정보를 쓰지 말라는 줄을 본문에 넣는다. [확인] S2 / [추정]

### C. Hook 2개 + deny 규칙 (`.claude/settings.json`)
jq가 없으니 hook은 Python으로 쓴다. Git Bash가 설치되어 있어 기본 셸은 bash다. [확인] S4, S39
```json
{
  "permissions": {
    "deny": ["Read(./.env)", "Bash(git push --force *)", "Bash(git push -f *)",
             "PowerShell(git push --force *)", "Bash(gh repo delete *)"],
    "ask":  ["Bash(gh pr merge *)", "PowerShell(gh pr merge *)", "Bash(ollama pull *)"]
  },
  "hooks": {
    "PostToolUse": [{ "matcher": "Edit|Write", "hooks": [{ "type": "command", "timeout": 30,
      "command": "\"$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe\" \"$CLAUDE_PROJECT_DIR/.claude/hooks/ruff_after_edit.py\"" }]}],
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "if": "Bash(git commit *)", "onFailure": "block", "timeout": 300,
        "command": "\"$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe\" \"$CLAUDE_PROJECT_DIR/.claude/hooks/precommit_gate.py\"" }]},
      { "matcher": "PowerShell", "hooks": [{ "type": "command", "if": "PowerShell(git commit *)", "onFailure": "block", "timeout": 300,
        "command": "\"$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe\" \"$CLAUDE_PROJECT_DIR/.claude/hooks/precommit_gate.py\"" }]}
    ]
  }
}
```
1. **`ruff_after_edit.py` (PostToolUse)**: `.py` 파일을 편집하면 `ruff format`과 import 정렬(`ruff check --select I --fix`)만 실행한다.
   - 전체 `--fix`는 쓰지 않는다. 다음 편집에서 쓰려고 먼저 추가한 import를 F401(미사용 import) 자동 수정이 지워 버릴 수 있다. [추정]
   - `--force-exclude`를 붙여 `docs/` 제외 설정을 지킨다. [추정]
   - 경로는 `json.load(sys.stdin)["tool_input"]["file_path"]`에서 읽는다(절대경로, 역슬래시). [확인] S4
2. **`precommit_gate.py` (PreToolUse, `git commit`에서만 실행)**: 입력 JSON의 `cwd`(worktree를 따라감, S8)에서 아래를 순서대로 실행하고, 실패하면 stderr에 출력 끝부분을 쓰고 exit 2로 커밋을 막는다. [확인] S4, S8
   - `python -m ruff check .`
   - `python -m ruff format --check .`
   - `python -m pytest -q -x -m "not slow"`
   - 한글 출력이 깨지지 않게 `sys.stderr.reconfigure(encoding="utf-8")`를 넣고, subprocess를 `encoding="utf-8", errors="replace"`로 연다. [추정]
   - Stop hook 대신 이 방식을 고른 이유: Stop은 질문에 답하는 턴에도 매번 실행되지만, 이 게이트는 커밋할 때만 돈다. [확인] S5 / [추정]
- 적용 후 `/hooks`에서 확인하고, 일부러 들여쓰기를 틀린 편집과 실패하는 커밋으로 실제 동작을 검증한다. Windows 경로를 bash에서 실행하는 부분은 직접 확인해야 한다. [확인] S5 / [추정]
- deny 규칙은 문자열 매칭이라 `git push origin x --force` 같은 변형은 빠져나간다. **main 브랜치 보호(PR 필수 + `lint-and-test` 통과 필수)**를 최종 방어선으로 둔다. [확인] S1, S10
- `.gitignore`에 `.claude/worktrees/`, `CLAUDE.local.md`, `data/workspaces/`, `.claude/agent-memory-local/`를 추가한다. [확인] S8, S6, S2

### D. GitHub Actions CI 개선
| 항목 | 현재 | 권장 | 근거 |
|---|---|---|---|
| checkout | `@v4` (Node 20 대상) | `actions/checkout@v7`(최신 v7.0.1, `runs.using: node24`) | [확인] S28, S29 |
| setup-python | `@v5` | `actions/setup-python@v7`(최신 v7.1.0, node24. v7에서 `pip-install` 입력과 EOL 파이썬 제거) | [확인] S30, S31, S32 |
| Node 20 일정 | — | 2025-09 폐기 공지 → 2026-09-23 러너에서 제거, 우회 변수도 폐지 | [검색] S34 / [확인] S33 |
| pip 캐시 키 | 기본값 | 기본값도 `requirements.txt`나 `pyproject.toml`을 찾지만 `cache-dependency-path: pyproject.toml`로 명시 | [확인] S32 |
| ruff 버전 | `ruff>=0.6`(버전 미고정) | `ruff==0.17.4`로 로컬과 맞춤. 포맷 결과가 달라 CI가 깨지는 것을 막는다 | [확인] S36, S39 |
| torch | 아직 없음(Task 0에서 추가 예정) | CI에는 GPU가 없으니 `pip install torch --index-url https://download.pytorch.org/whl/cpu`를 먼저 실행. 더 나은 안은 `sentence-transformers`를 지연 import하고 `[ml]` extra로 분리하는 것(계획과 다르므로 PR에 명시) | [추정] S42 |
| 그 밖 | — | `permissions: contents: read`, `concurrency`(같은 ref 실행 취소), `timeout-minutes: 15`, `pytest -m "not slow"` 명시, `ruff check --output-format=github` | [추정] |
| 유지보수 | — | `.github/dependabot.yml`로 `github-actions` 버전 자동 갱신. 필요하면 SHA 고정 | [추정] S41 |
| 선택 | — | `windows-latest`를 matrix에 추가해 Windows 경로 버그를 잡는다(공개 저장소라 표준 러너 무료) | [추정] |
| 대안 | — | `astral-sh/setup-uv@v10`(v10.3.3)으로 설치를 빠르게 할 수 있지만, node24 여부는 릴리스 페이지에 없다 | [확인] S35 |

```yaml
permissions: { contents: read }
concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }
jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with: { python-version: "3.13", cache: pip, cache-dependency-path: pyproject.toml }
      - run: |
          python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
          python -m pip install -e ".[dev]"
      - run: ruff check --output-format=github .
      - run: ruff format --check .
      - run: pytest -q -m "not slow"
```

### E. 작업 루프 (Task마다)
1. `main` 최신화 → `task/NN-*` 브랜치 → `@agent-axb-implementer Task NN`.
2. implementer가 계획 테스트를 옮기고 실패 출력을 확인한 뒤 구현한다. ruff hook이 포맷을 처리하고, 커밋 게이트가 검사를 강제한다. 실패 출력과 통과 출력을 PR 본문에 붙인다.
3. `axb-reviewer`로 계획과 Review Focus 기준 리뷰를 하고, `/code-review`로 2차 리뷰를 한다. 보안 관련 Task라면 `axb-security-reviewer`나 `/security-review`를 추가한다. 최대 2라운드.
4. `gh pr checks --watch`로 CI 통과를 확인한다. squash 머지는 `ask` 규칙이 사용자에게 확인을 받는다. 학습 노트를 읽는 시점과 겹치게 한다. 그다음 `/clear`.
5. 긴 무인 실행이면 `/goal "Task NN PR open, CI green, blocker 0"`을 건다. 비용은 `/usage`로 주기적으로 본다. [확인] S1, S7

### F. 사용자 승인이 필요한 것
- main 브랜치 보호 설정(저장소 설정 변경)
- `claude-code-action`이나 Code Review 도입(API 과금, S37·S38)
- Ollama 설치와 모델 pull
- 로컬 `main`의 미커밋 Task 0 변경을 브랜치로 옮기는 작업
- PR #1에 위 보완점을 반영할지 여부

## 출처
- S1 https://code.claude.com/docs/en/best-practices [확인]
- S2 https://code.claude.com/docs/en/sub-agents [확인]
- S3 https://code.claude.com/docs/en/skills [확인]
- S4 https://code.claude.com/docs/en/hooks [확인]
- S5 https://code.claude.com/docs/en/hooks-guide [확인]
- S6 https://code.claude.com/docs/en/memory [확인]
- S7 https://code.claude.com/docs/en/costs [확인]
- S8 https://code.claude.com/docs/en/worktrees [확인]
- S9 https://code.claude.com/docs/en/agent-teams [확인]
- S10 https://code.claude.com/docs/en/permissions [확인]
- S11 https://code.claude.com/docs/en/settings-reference [확인 — 색인만: `attribution`, `worktree.*`]
- S12 https://claude.com/blog/how-and-when-to-use-subagents-in-claude-code (2026-04-07) [확인]
- S13 https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/ (2026-06-03) [확인]
- S14 https://claude.dev/blog/solving-code-review-at-anthropic/ (2026-10-15) [확인]
- S15 https://claude.com/resources/articles/building-agents-when-to-fold-subagents-into-your-main-agent-loop (2026-10-22) [확인]
- S16 https://claude.dev/blog/spending-your-effort/ (2026-09-25) [확인]
- S17 https://claude.com/blog/category/claude-code [확인 — 글 목록]
- S18 https://www.anthropic.com/engineering/claude-code-best-practices (2025 블로그, 현재 S1로 308 리다이렉트) [확인]
- S19 https://github.com/obra/superpowers [확인]
- S20 https://github.com/VoltAgent/awesome-claude-code-subagents [확인]
- S21 https://github.com/wshobson/agents [확인]
- S22 https://github.com/nizos/tdd-guard [확인]
- S23 https://www.dolthub.com/blog/2025-06-30-claude-code-gotchas/ (2025) [확인]
- S24 https://thepromptshelf.dev/blog/claude-code-tdd-enforcement/ [검색]
- S25 https://alexop.dev/posts/custom-tdd-workflow-claude-code-vue/ [검색]
- S26 https://ao92265.github.io/claude-code-playbook/docs/anti-patterns/ [검색]
- S27 https://www.amazingcto.com/where-ai-struggle-doom-loops/ (2026-01) [검색]
- S28 https://github.com/actions/checkout/releases [확인]
- S29 https://raw.githubusercontent.com/actions/checkout/v7/action.yml [확인]
- S30 https://github.com/actions/setup-python/releases [확인]
- S31 https://raw.githubusercontent.com/actions/setup-python/v7/action.yml [확인]
- S32 https://github.com/actions/setup-python/blob/main/README.md [확인]
- S33 https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions [확인]
- S34 https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/ (2025) [검색]
- S35 https://github.com/astral-sh/setup-uv/releases [확인]
- S36 https://pypi.org/project/ruff/ [확인]
- S37 https://github.com/anthropics/claude-code-action [확인]
- S38 https://claude.com/resources/articles/code-review-is-now-generally-available [검색 — 목록에서 제목만]
- S39 저장소와 로컬 직접 확인 [확인]
  - https://github.com/Growcompany/ax-assistant-builder/pull/1
  - https://github.com/Growcompany/ax-assistant-builder/actions/runs/42604753011
  - 로컬 `<repo>\.github\workflows\ci.yml`, `pyproject.toml`, `CLAUDE.md`, `docs/superpowers/plans/2026-11-09-m1-thin-e2e.md`, `git status`, `claude --version`
- S40 https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_pipeline_chain_operators [추정 — 열어 보지 않음]
- S41 https://docs.github.com/en/code-security/dependabot/working-with-dependabot/keeping-your-actions-up-to-date-with-dependabot [추정 — 열어 보지 않음]
- S42 https://pytorch.org/get-started/locally/ [추정 — 열어 보지 않음]
