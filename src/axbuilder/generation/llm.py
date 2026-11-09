"""LLM 클라이언트: 로컬(Ollama)과 클라우드(Claude)를 같은 LLMClient 모양 뒤에 둔다."""

import logging
import time
from typing import Protocol

import anthropic
import httpx

# LLMResponse는 Answer도 담는 데이터라 models에 두고, 이 모듈에서도 import할 수 있게 둔다
from axbuilder.models import LLMResponse
from axbuilder.settings import Settings

logger = logging.getLogger(__name__)

# VRAM 24GB 미만이면 Ollama 기본 컨텍스트가 4k라 프롬프트(근거 청크 5개)가 잘린다
OLLAMA_NUM_CTX = 8192
# Opus 5.5는 생각(thinking)을 끌 수 없고 생각 토큰도 max_tokens에 포함된다. 작게 잡으면
# 답이 잘린다. 16000은 스트리밍 없이도 SDK의 HTTP 시간 제한 안에 드는 크기(claude-api 스킬)
CLAUDE_MAX_TOKENS = 16000
# fallbacks="default"는 이 헤더와 짝이다. 배열 형식(2026-06-01 헤더)과 섞으면 400
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# SDK 기본값은 요청당 600초에 재시도 2번이고 시간 초과도 재시도하므로, 질문 하나가 최악
# 30분 넘게 묶인다. /ask는 사람이 기다리는 요청이라 한 번에 120초(OllamaClient와 같은 값),
# 재시도 1번(529·5xx·연결 오류처럼 금방 끝나는 일시 오류용)으로 최악 약 4분에 끝낸다.
# 근거 청크 5개에 답하는 짧은 답은 이 안에 든다. 더 긴 답이 필요하면 스트리밍으로 바꾼다.
# ClaudeClient 전체에 걸리므로 평가(run_eval --with-llm, cloud)도 같은 값을 쓴다. 평가는 첫
# LLMUnavailableError에서 멈추므로, 생각이 긴 답 하나가 유료 50문항 실행을 중간에 끝낼 수 있다.
# 첫 클라우드 평가(M-2) 전에 호출자별 timeout 인자나 스트리밍을 정한다
# anthropic 2.x는 httpx2 기반이라 httpx.Timeout이 아니라 anthropic.Timeout을 쓴다
CLAUDE_TIMEOUT = anthropic.Timeout(120.0, connect=5.0)
CLAUDE_MAX_RETRIES = 1


class LLMClient(Protocol):
    """OllamaClient, ClaudeClient, FakeLLM이 모두 따르는 생성 모양."""

    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse: ...


class LLMUnavailableError(RuntimeError):
    """LLM을 부를 수 없다(서버 꺼짐, 시간 초과, 인증 실패 등). 메시지에 해결 방법을 담는다."""


class OllamaClient:
    def __init__(
        self,
        model: str,
        base_url: str,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            # qwen3.5는 추론 모드가 기본이라, 끄지 않으면 답 앞에 긴 추론을 생성해 느려진다
            "think": False,
            "options": {"num_ctx": OLLAMA_NUM_CTX, "num_predict": max_tokens},
        }
        start = time.perf_counter()
        try:
            response = self._http.post("/api/chat", json=payload)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise LLMUnavailableError(
                f"Ollama 서버({self.base_url})에 연결할 수 없습니다. "
                "Ollama가 실행 중인지 확인하세요: ollama serve"
            ) from e
        except httpx.TimeoutException as e:
            raise LLMUnavailableError(
                f"Ollama가 {self.timeout:g}초 안에 답하지 않았습니다. 첫 호출은 모델을 GPU에 "
                "올리느라 느릴 수 있으니 다시 시도하거나 timeout을 늘리세요 (상태 확인: ollama ps)"
            ) from e
        except httpx.HTTPStatusError as e:
            status, detail = e.response.status_code, _ollama_error_detail(e.response)
            if status == 404:
                raise LLMUnavailableError(
                    f"Ollama에서 모델 '{self.model}'을 찾지 못했습니다({detail}). "
                    f"모델을 받으세요: ollama pull {self.model}"
                ) from e
            raise LLMUnavailableError(f"Ollama 오류 {status}: {detail}") from e
        except httpx.TransportError as e:
            raise LLMUnavailableError(
                f"Ollama 서버({self.base_url})와 통신하지 못했습니다: {e}. "
                "AXB_OLLAMA_URL과 Ollama 상태를 확인하세요"
            ) from e
        latency_ms = (time.perf_counter() - start) * 1000

        try:
            data = response.json()
            text = data["message"]["content"]
        except (ValueError, KeyError, TypeError) as e:
            raise LLMUnavailableError(
                f"Ollama 응답 형식이 예상과 다릅니다: {response.text[:200]!r}"
            ) from e
        return LLMResponse(
            text=text,
            model=self.model,
            # 프롬프트가 캐시되면 prompt_eval_count가 빠질 때가 있다
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            latency_ms=latency_ms,
            stop_reason=data.get("done_reason"),
        )


def _ollama_error_detail(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"])
    except (ValueError, KeyError, TypeError):
        return response.text[:200]


def _credentials_unavailable(e: anthropic.AnthropicError) -> LLMUnavailableError:
    return LLMUnavailableError(
        f"Claude API 인증 정보를 쓸 수 없습니다({e}). `ant auth login`으로 다시 로그인하거나 "
        "ANTHROPIC_API_KEY를 설정하세요 (키 없이 쓰려면 AXB_LLM_MODE=local)"
    )


class ClaudeClient:
    def __init__(self, model: str, *, client: anthropic.Anthropic | None = None) -> None:
        self.model = model
        # 기본 클라이언트는 첫 호출 때 만든다. SDK는 만들 때 프로필을 읽어서, 고른 프로필이
        # 깨졌으면 그때 CredentialsError를 낸다. 여기서 터지면 API 서버가 뜨지 못한다
        self._client = client

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            try:
                # 인증 정보: ANTHROPIC_API_KEY → ANTHROPIC_AUTH_TOKEN → `ant auth login` 프로필
                self._client = anthropic.Anthropic(
                    timeout=CLAUDE_TIMEOUT, max_retries=CLAUDE_MAX_RETRIES
                )
            except anthropic.AnthropicError as e:
                raise _credentials_unavailable(e) from e
        return self._client

    def generate(self, system: str, user: str, max_tokens: int = CLAUDE_MAX_TOKENS) -> LLMResponse:
        client = self._get_client()
        if client.api_key is None and client.auth_token is None and client.credentials is None:
            raise LLMUnavailableError(
                "Claude API 인증 정보가 없습니다. ANTHROPIC_API_KEY 환경변수를 설정하거나 "
                "`ant auth login`으로 로그인하세요 (키 없이 쓰려면 AXB_LLM_MODE=local)"
            )
        start = time.perf_counter()
        try:
            # temperature·top_p·top_k는 넣지 않는다(현재 API에서 400)
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                # 안전 분류기가 거절하면 서버가 거절 분류에 맞는 대체 모델로 같은 요청을 다시
                # 돌린다. 분류에 따라(예: reasoning_extraction) 폴백 없이 거절로 오기도 한다
                betas=[CLAUDE_FALLBACK_BETA],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LLMUnavailableError(
                f"Claude API 인증에 실패했습니다({e.status_code}). ANTHROPIC_API_KEY가 맞는지, "
                "이 키로 모델을 쓸 권한이 있는지 확인하세요"
            ) from e
        except anthropic.NotFoundError as e:
            raise LLMUnavailableError(
                f"Claude 모델 '{self.model}'을 찾지 못했습니다. AXB_CLAUDE_MODEL을 확인하세요"
            ) from e
        except anthropic.RateLimitError as e:
            raise LLMUnavailableError(
                "Claude API 사용 한도(429)에 걸렸습니다. 잠시 후 다시 시도하세요"
            ) from e
        except anthropic.APIStatusError as e:
            # SDK와 같은 기준: 408·409·5xx(529 포함)는 일시적이고, 나머지 4xx는 요청이 잘못됐다
            if e.status_code >= 500 or e.status_code in (408, 409):
                raise LLMUnavailableError(
                    f"Claude API 오류 {e.status_code}: {e.message}. 잠시 후 다시 시도하세요"
                ) from e
            raise LLMUnavailableError(
                f"Claude API 요청 오류 {e.status_code}: {e.message}. 다시 보내도 같은 오류가 "
                "나므로 요청(모델, max_tokens, 프롬프트 길이)과 코드를 확인하세요"
            ) from e
        except anthropic.APITimeoutError as e:
            raise LLMUnavailableError(
                "Claude API 응답 시간이 초과됐습니다. 잠시 후 다시 시도하세요"
            ) from e
        except anthropic.APIConnectionError as e:
            raise LLMUnavailableError(
                "Claude API에 연결할 수 없습니다. 인터넷 연결과 프록시 설정을 확인하세요"
            ) from e
        except (anthropic.CredentialsError, anthropic.WorkloadIdentityError) as e:
            # 프로필에 토큰이 없거나 토큰 갱신이 실패하면 요청을 보내기 전에 난다(APIError 아님)
            raise _credentials_unavailable(e) from e
        except anthropic.AnthropicError as e:
            raise LLMUnavailableError(f"Claude SDK 오류({type(e).__name__}): {e}") from e
        latency_ms = (time.perf_counter() - start) * 1000

        if response.stop_reason == "refusal":
            # 모델의 안전 거절은 오류가 아니라 정상 응답(HTTP 200)이다. 빈 텍스트로 돌려주면
            # 답변 단계가 거절로 처리한다. 출력 도중 거절됐으면 content에 부분 출력이 남는데,
            # 완성된 답이 아니므로 버린다. stop_details는 없을 수도 있다
            category = response.stop_details.category if response.stop_details else None
            logger.warning(
                "Claude refused the request: stop_reason=refusal model=%s category=%s",
                response.model,
                category,
            )
            text = ""
        else:
            # thinking 블록(기본은 빈 문자열)은 빼고 text 블록만 합친다
            text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            # 대체 모델이 답했으면 그 모델 이름이 온다
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
            stop_reason=response.stop_reason,
        )


class FakeLLM:
    """테스트용: 정해진 답을 돌려주고, 받은 (system, user)를 calls에 기록한다."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(
            text=self.reply, model="fake", input_tokens=0, output_tokens=0, latency_ms=0.0
        )


def get_llm(settings: Settings) -> LLMClient:
    if settings.llm_mode == "cloud":
        return ClaudeClient(settings.claude_model)
    return OllamaClient(settings.local_model, settings.ollama_url)
