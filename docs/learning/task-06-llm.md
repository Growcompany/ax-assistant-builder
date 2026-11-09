# Task 6 — LLM 클라이언트 (로컬·클라우드)

## 이 작업이 하는 일

- `src/axbuilder/generation/llm.py`에 LLM 호출부를 모았다.
  - `LLMClient` 프로토콜: `generate(system, user, max_tokens) -> LLMResponse`. 답변 생성(Task 7)과 평가(Task 9)는 이 모양만 안다.
  - `OllamaClient`: 로컬 Ollama `POST /api/chat`. 기본 모델은 `qwen3.5:4b-q4_K_M`이다.
  - `ClaudeClient`: 공식 `anthropic` SDK로 Claude(`claude-opus-5-5`)를 부른다. 서버측 폴백을 켜고, 안전 거절을 처리한다.
  - `FakeLLM`: 정해진 답을 돌려주고 받은 프롬프트를 기록한다. 네트워크 없는 단위 테스트용이다.
  - `get_llm(settings)`: `AXB_LLM_MODE`가 `local`이면 Ollama, `cloud`면 Claude를 만든다.
  - `LLMUnavailableError`: 서버가 꺼졌거나, 시간이 초과됐거나, 키가 없을 때 쓴다. **원인과 해결 명령을 메시지에 담는다.** API(Task 10)는 이 오류를 503으로 바꾼다.

```
answer() ──generate(system, user)──▶ LLMClient ─┬─ OllamaClient ─▶ localhost:11434/api/chat
                                                ├─ ClaudeClient ─▶ anthropic SDK ─▶ /v1/messages
                                                └─ FakeLLM (테스트)
                         ◀── LLMResponse(text, model, 토큰 수, 지연, stop_reason)
```

## 핵심 개념

**프로토콜로 구현 교체**
`LLMClient`는 `typing.Protocol`이다. 상속하지 않아도 `generate` 메서드 모양만 같으면 같은 타입으로 본다(구조적 타이핑). 그래서 답변 코드는 어느 LLM인지 모른다. 테스트에서는 `FakeLLM`을, 실행할 때는 설정에 따라 Ollama나 Claude를 넣는다. 의존성 주입과 같은 효과다. 검색기의 `Retriever`(Task 5)와 같은 방식이다.

**로컬(Ollama) 요청에 넣은 두 값**
- `think: false`: qwen3.5는 추론 모드가 기본이다. 같은 질문으로 두 번 재 보니 다음과 같았다.

| think | 출력 토큰 (구현 때 / 리뷰 때) | 지연 (구현 때 / 리뷰 때) |
|---|---|---|
| true | 3,202 / 2,117 | 31.9초 / 20.4초 |
| false | 18 / 29 | 2.8초 / 2.9초 |

  추론을 켜면 생성 길이가 실행마다 크게 달라서 숫자는 그때그때 다르다. 그래도 두 번 모두 출력 토큰은 70배 이상, 지연은 7배 이상 차이가 났다. 짧은 근거 기반 답변에는 추론이 필요 없고, 출력 토큰이 많을수록 느려진다.
- `options.num_ctx: 8192`: VRAM이 24GB보다 작으면 Ollama의 기본 컨텍스트는 4k다. 4k를 넘는 프롬프트는 **앞부분이 조용히 잘린다.** 근거 청크 5개를 넣으면 4k를 넘을 수 있어서 8k로 올렸다. `/api/ps`에서 `context_length: 8192`로 적용된 것을 확인했다.
- `max_tokens`는 Ollama의 `num_predict`(최대 출력 토큰)로 넘긴다.

**클라우드(Claude) 요청**
- `temperature`/`top_p`/`top_k`를 넣지 않는다. 현재 API에서 400 오류다. 단위 테스트가 요청 본문에 이 값이 없는지 검사한다.
- `max_tokens` 기본값은 16000이다. Opus 5.5는 생각(thinking)을 끌 수 없고, 생각 토큰도 `max_tokens`에 포함된다. 2048처럼 작게 잡으면 답이 중간에 잘릴 수 있다. 16000은 스트리밍 없이도 SDK의 HTTP 시간 제한 안에 드는 크기다.
- **안전 거절(`stop_reason == "refusal"`)은 예외가 아니다.** HTTP 200으로 오는 정상 응답이다. 그래서 `text=""`, `stop_reason="refusal"`로 돌려주고 경고 로그(분류 `category` 포함)를 남긴다. Task 7은 빈 텍스트를 거절로 처리한다. 분류기는 출력 도중에도 거절할 수 있어서 `content`에 부분 출력이 남아 있을 수 있다. 완성된 답이 아니므로 버린다(테스트가 부분 출력이 있는 거절로 확인한다).
- **서버측 폴백.** `betas=["server-side-fallback-2026-07-01"]`과 `fallbacks="default"`를 `client.beta.messages.create`에 넣었다. 분류기가 거절하면 서버가 거절 분류에 맞는 대체 모델로 같은 요청을 다시 돌린다. 단, 모든 거절이 폴백되지는 않는다. 분류에 따라 폴백 없이 거절로 바로 올 수 있다. 예를 들어 Opus 5.5에서 `reasoning_extraction`(내부 추론을 답에 쓰라는 요청) 거절은 대체 모델로 다시 돌리지 않는다(claude-api 문서). 그래서 폴백을 켜도 거절 처리는 꼭 있어야 한다. 폴백이 일어나면 `response.model`에는 실제로 답한 모델 이름이 온다. 그래서 `LLMResponse.model`에 요청한 이름이 아니라 이 값을 넣는다. 헤더 날짜는 형식마다 다르다. `"default"`는 `-2026-07-01`, 모델 목록 배열은 `-2026-06-01`이고, 섞으면 400이다. 사용법은 claude-api 스킬 문서로 확인했다.
- 응답 `content`에는 `thinking` 블록(기본은 빈 문자열)과 `text` 블록이 섞여 온다. `type == "text"`인 블록만 이어 붙인다.

**토큰과 비용**
- 두 클라이언트 모두 입력·출력 토큰 수를 `LLMResponse`에 남긴다. Ollama는 `prompt_eval_count`/`eval_count`, Claude는 `usage.input_tokens`/`output_tokens`다. Task 9가 질문당 비용과 지연을 집계할 때 쓴다.
- Claude Opus 5.5 가격은 입력 $4, 출력 $20(100만 토큰당)이다. 근거 5개를 넣은 질문이 입력 약 3천 토큰, 출력(생각 포함) 약 1천 토큰이라고 가정하면 질문당 약 $0.03이다. 50문항 평가 한 번이면 약 $1.6이다. 이 값은 추정이고 실제로 재지 않았다. **생각 토큰은 응답에 보이지 않아도 출력 토큰으로 과금된다.**
- 로컬은 토큰당 비용이 없다. 대신 GPU 메모리를 쓴다. qwen3.5 4B Q4는 8k 컨텍스트에서 VRAM 약 3.1GiB(RTX 3070 Ti 8GB)를 차지했다.

**타임아웃·오류 처리**

| 상황 | Ollama | Claude | 메시지에 담은 해결 방법 |
|---|---|---|---|
| 서버 꺼짐·연결 실패 | `ConnectError`, `ConnectTimeout` | `APIConnectionError` | `ollama serve` / 네트워크·프록시 확인 |
| 응답 시간 초과 | `ReadTimeout` 등 (기본 120초) | `APITimeoutError` (120초, 1회 재시도 — Task 10에서 정함. SDK 기본은 10분, 2회) | 첫 호출은 모델 로딩이 느림, `ollama ps` |
| 모델 없음 | HTTP 404 | `NotFoundError` | `ollama pull <모델>` / `AXB_CLAUDE_MODEL` |
| 인증 | — | 키·토큰·프로필 없음(호출 전 검사), 프로필 깨짐(`CredentialsError`·`WorkloadIdentityError`), 401·403 | `ANTHROPIC_API_KEY` 설정 또는 `ant auth login` |
| 일시적인 HTTP 오류 | 4xx·5xx (서버 오류 문구 포함) | 429, 408·409, 5xx·529 | 상태 코드와 원문, 잠시 후 재시도 |
| 요청이 잘못된 HTTP 오류 | (위와 같음) | 400·413·422 등 나머지 4xx | 상태 코드와 원문, 요청과 코드 확인(재시도해도 같음) |

- 연결 실패와 연결 시간 초과는 같은 메시지로 묶었다. 닫힌 포트에 연결하면 Windows에서는 거부가 바로 오지 않고 `ConnectTimeout`이 난다(직접 확인).
- 키 없음은 요청 전에 잡는다. SDK는 인증 정보가 없으면 요청 직전에 `TypeError`를 낸다. `TypeError`를 그대로 잡으면 코드 버그까지 삼킬 수 있다. 그래서 클라이언트의 `api_key`·`auth_token`·`credentials`가 모두 비었는지 먼저 검사한다.
- **프로필이 깨진 경우는 사전 검사로 못 잡는다.** `ant auth login` 프로필이 있으면 `credentials`가 채워져 검사를 통과한다. 그런데 토큰이 빠졌거나 갱신에 실패하면 SDK가 요청을 보내기 전에 `CredentialsError`·`WorkloadIdentityError`를 낸다. 둘 다 `AnthropicError`의 하위 클래스지만 `APIError` 계열이 아니라서, 따로 잡지 않으면 API에서 503이 아니라 500이 된다. 리뷰에서 찾은 구멍이고, 지금은 `ant auth login` 안내가 담긴 `LLMUnavailableError`로 바꾼다. 그 밖의 SDK 오류(`AnthropicError`)도 마지막에 `LLMUnavailableError`로 모은다.
- 같은 이유로 SDK 클라이언트는 `ClaudeClient`를 만들 때가 아니라 **첫 호출 때** 만든다. SDK는 클라이언트를 만들 때 `ANTHROPIC_PROFILE`로 고른 프로필을 읽는데, 그 프로필이 없으면 그 자리에서 `CredentialsError`가 난다. 생성 시점에 터지면 `get_llm()`을 부르는 API 서버가 아예 뜨지 못한다.
- 상태 코드 오류는 SDK의 재시도 기준과 같게 나눴다. 408·409·429·5xx(529 포함)는 일시적이라 "잠시 후 다시 시도"를, 나머지 4xx는 요청이 잘못된 것이라 "요청과 코드 확인"을 안내한다.
- Claude 재시도(408·409·429·5xx·연결 오류)는 SDK가 기본 2번 해 준다. 다시 구현하지 않았다. 대신 SDK 기본값(시간 제한 10분, 재시도 2회)이면 요청 하나가 최악의 경우 약 30분(10분 × 3번) 걸린다. API 서버(Task 10)에서 `timeout`·`max_retries`를 정한다. → Task 10에서 120초·재시도 1회로 정했다(최악 약 4분, 근거는 `task-10-api.md`).

## 왜 이렇게 했나 (대안과 비교)

| 결정 | 대안 | 고른 이유 |
|---|---|---|
| `Protocol` + 클라이언트 3개 | LangChain 같은 LLM 추상화 | 필요한 기능이 "system+user → text+토큰"뿐이다. 프레임워크를 쓰면 요청 본문(`think`, `num_ctx`, `fallbacks`)을 직접 통제하기 어렵고, 면접에서 설명할 층이 늘어난다 |
| 로컬 기본(`AXB_LLM_MODE=local`) | 클라우드 기본 | 비용이 0이고 문서가 밖으로 나가지 않는다(폐쇄망 은행 시나리오). 대신 4B 모델은 지식과 지시 준수가 약하다. 그래서 답변 품질은 검색 근거에 더 많이 기대고, Claude는 비교·품질 상한 측정용이다 |
| 오류를 `LLMUnavailableError` 하나로 모으기 | SDK·httpx 예외를 그대로 올리기 | API 계층이 "LLM을 못 씀 → 503" 하나만 처리하면 된다. 원인별 해결 방법은 메시지에 담고, 원래 예외는 `from e`로 남긴다 |
| 안전 거절은 빈 텍스트 | 예외 | 거절은 HTTP 200 정상 응답이고, 사용자에게는 "답할 수 없음"으로 보여야 한다. 예외로 만들면 503(서비스 장애)처럼 보인다 |
| `fallbacks="default"` | 대체 모델을 직접 지정 / 폴백 없음 | 거절 분류마다 알맞은 대체 모델이 다르다. `"default"`는 그 선택을 서버에 맡겨서, 대체 모델이 퇴역해도 코드를 고칠 필요가 없다 |
| 테스트용 `transport`·`client` 주입 | `unittest.mock`으로 함수 바꿔치기 | 실제 HTTP 요청 본문과 헤더(`think`, `fallbacks`, 베타 헤더)를 그대로 검사할 수 있다. 네트워크와 키 없이 돈다 |

수동 확인에서 로컬 모델의 약점이 그대로 드러났다. 근거 없이 "예금자보호 제도"를 물었더니 두 번째 답에 "500만 원(2019년 기준)"이라는 틀린 금액이 나왔다(실제 한도는 1억 원). 자료 없이 4B 모델이 아는 척하는 것을 막는 것이 Task 7(근거 기반 프롬프트·거절)의 일이다.

## 코드 읽는 순서

1. `LLMResponse`, `LLMClient`, `LLMUnavailableError`: 바깥에서 보이는 모양
2. `OllamaClient.generate`: 요청 본문(`think`, `num_ctx`, `num_predict`) → 예외 순서(연결 → 시간 초과 → HTTP 상태 → 기타 전송 오류) → 응답 필드
3. `ClaudeClient.generate`: `_get_client()`(첫 호출 때 SDK 클라이언트 생성) → 인증 정보 사전 검사 → `beta.messages.create`(폴백) → 예외 체인(구체적인 것부터: 401·403 → 404 → 429 → 그 밖의 상태 코드 → 시간 초과 → 연결 → 인증 정보 오류 → 그 밖의 SDK 오류) → 거절 분기 → text 블록 합치기
4. `FakeLLM`, `get_llm`
5. `tests/generation/test_llm.py`: 계획의 5개 테스트, `httpx.MockTransport`(Ollama)·`httpx2.MockTransport`(anthropic SDK)로 요청 본문 검사, 거절(부분 출력 포함)·인증 실패·깨진 프로필·과부하(529)·요청 오류(400) 경로

## 면접 예상 질문과 답

**Q1. 로컬 LLM과 클라우드 LLM을 어떻게 바꿔 끼우나요?**
`LLMClient` 프로토콜 하나에 `generate(system, user, max_tokens)`만 정의했습니다. Ollama·Claude·테스트용 Fake가 같은 모양이라, 답변 코드는 어느 쪽인지 모릅니다. 실행할 때는 환경변수 `AXB_LLM_MODE` 하나로 고릅니다. 테스트는 Fake로 네트워크 없이 돕니다.

**Q2. Ollama에 `think: false`와 `num_ctx`를 왜 넣었나요?**
qwen3.5는 추론 모드가 기본입니다. 같은 질문에서 출력 토큰이 3,202개 대 18개, 지연이 31.9초 대 2.8초였습니다. 다시 재면 2,117개 대 29개, 20.4초 대 2.9초처럼 값은 달라지지만 차이는 늘 몇 배 이상이었습니다. `num_ctx`는 Ollama 기본값이 VRAM 24GB 미만에서 4k라서, 근거 5개를 넣으면 프롬프트 앞부분이 조용히 잘릴 수 있어 8k로 올렸습니다.

**Q3. Claude가 안전 이유로 거절하면 어떻게 되나요?**
거절은 HTTP 200에 `stop_reason: "refusal"`로 옵니다. 서버측 폴백(`fallbacks="default"`)을 켜 두어서, 거절 분류에 따라 서버가 다른 모델로 다시 시도합니다. 다만 분류에 따라서는 폴백 없이 거절로 바로 옵니다. 예를 들어 내부 추론을 답에 쓰라는 `reasoning_extraction` 거절은 다시 시도하지 않습니다. 최종 응답이 거절이면 빈 텍스트와 `stop_reason="refusal"`을 돌려주고 분류를 로그에 남깁니다. 출력 도중 거절돼 부분 출력이 있어도 버립니다. 답변 단계는 이것을 "답할 수 없음"으로 처리합니다. 장애가 아니므로 예외로 만들지 않았습니다.

**Q4. Ollama가 꺼져 있거나 API 키가 없으면요?**
`LLMUnavailableError`가 납니다. 메시지에 원인과 해결 명령이 들어갑니다. 예를 들면 "Ollama가 실행 중인지 확인하세요: ollama serve", "ANTHROPIC_API_KEY를 설정하거나 ant auth login" 같은 문구입니다. API는 이 오류를 503으로 바꿉니다. 키 없음은 요청 전에 검사합니다. SDK가 내는 `TypeError`를 잡으면 진짜 버그까지 삼킬 수 있기 때문입니다. `ant auth login` 프로필이 깨진 경우(토큰 없음, 갱신 실패)는 SDK가 `APIError`가 아닌 `CredentialsError` 계열을 내서, 이것도 따로 잡아 같은 오류로 바꿉니다.

**Q5. Claude를 쓰면 비용이 얼마나 드나요?**
Opus 5.5는 100만 토큰당 입력 $4, 출력 $20입니다. 근거 5개짜리 질문을 입력 3천·출력 1천 토큰으로 잡으면 질문당 약 3센트, 50문항 평가 한 번에 약 1.6달러입니다(추정). 생각 토큰은 보이지 않아도 출력으로 과금됩니다. 그래서 응답마다 토큰 수를 기록해 두고, 평가 리포트에서 실제 비용을 계산할 수 있게 했습니다.
