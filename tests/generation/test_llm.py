import json
import logging

import anthropic
import httpx
import httpx2
import pytest

from axbuilder.generation.llm import (
    CLAUDE_MAX_RETRIES,
    CLAUDE_TIMEOUT,
    ClaudeClient,
    FakeLLM,
    LLMClient,
    LLMResponse,
    LLMUnavailableError,
    OllamaClient,
    get_llm,
)
from axbuilder.settings import Settings

ANTHROPIC_CREDENTIAL_ENV = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_CONFIG_DIR",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
]


@pytest.fixture
def no_anthropic_credentials(monkeypatch, tmp_path):
    # 키 말고도 토큰·`ant auth login` 프로필·WIF로 인증될 수 있다. 개발 PC에 프로필이
    # 있어도 유료 API를 실제로 부르지 않게, 프로필을 찾는 폴더를 빈 임시 폴더로 바꾼다
    for name in ANTHROPIC_CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))  # Windows 프로필 위치
    monkeypatch.setenv("HOME", str(tmp_path))  # Linux·macOS 프로필 위치(~/.config/anthropic)


# ---- 계획의 테스트 ----


def test_fake_llm_records_calls():
    llm = FakeLLM("답")
    r = llm.generate("sys", "q")
    assert r.text == "답" and llm.calls == [("sys", "q")]


def test_get_llm_local(monkeypatch):
    monkeypatch.setenv("AXB_LLM_MODE", "local")
    assert isinstance(get_llm(Settings.from_env()), OllamaClient)


def test_ollama_sends_think_false_and_num_ctx():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"message": {"content": "답"}, "prompt_eval_count": 3, "eval_count": 1}
        )

    llm = OllamaClient("m", "http://x", transport=httpx.MockTransport(handler))
    assert llm.generate("s", "u").text == "답"
    assert seen["think"] is False and seen["options"]["num_ctx"] == 8192 and seen["stream"] is False


def test_ollama_unreachable_raises():
    llm = OllamaClient("m", "http://127.0.0.1:9", timeout=1)  # 닫힌 포트
    # 계획보다 강하게: Windows에서 실제로 나는 ConnectTimeout도 해결 방법을 안내하는지 본다
    with pytest.raises(LLMUnavailableError, match="ollama serve"):
        llm.generate("s", "u")


def test_claude_without_key_raises(monkeypatch, no_anthropic_credentials):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailableError):
        ClaudeClient("claude-opus-5-5").generate("s", "u")


# ---- 추가: 프로토콜과 get_llm ----


def test_fake_llm_works_through_protocol():
    # 답변 생성(Task 7)은 LLMClient 타입만 알고, 테스트에서는 FakeLLM을 넣는다
    def use(llm: LLMClient) -> LLMResponse:
        return llm.generate("s", "u", max_tokens=10)

    r = use(FakeLLM("x"))
    assert r.text == "x" and r.model == "fake" and r.latency_ms == 0.0


def test_get_llm_cloud(monkeypatch):
    monkeypatch.setenv("AXB_LLM_MODE", "cloud")
    monkeypatch.delenv("AXB_CLAUDE_MODEL", raising=False)  # 개발 PC 설정과 무관하게 기본값 확인
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # 생성만 하고 호출하지 않는다
    llm = get_llm(Settings.from_env())
    assert isinstance(llm, ClaudeClient) and llm.model == "claude-opus-5-5"


def test_get_llm_local_uses_settings(monkeypatch):
    monkeypatch.setenv("AXB_LLM_MODE", "local")
    monkeypatch.setenv("AXB_LOCAL_MODEL", "other:1b")
    monkeypatch.setenv("AXB_OLLAMA_URL", "http://ollama.test:1234")
    llm = get_llm(Settings.from_env())
    assert isinstance(llm, OllamaClient)
    assert llm.model == "other:1b" and llm.base_url == "http://ollama.test:1234"


# ---- 추가: Ollama 요청·응답 ----


def test_ollama_request_and_response_fields():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "m",
                "message": {"role": "assistant", "content": "한도는 1억원입니다 [1]."},
                "done_reason": "stop",
                "prompt_eval_count": 120,
                "eval_count": 15,
            },
        )

    llm = OllamaClient("m", "http://x/", transport=httpx.MockTransport(handler))
    r = llm.generate("시스템", "질문", max_tokens=300)
    assert seen["url"] == "http://x/api/chat"
    assert seen["body"]["model"] == "m"
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "시스템"},
        {"role": "user", "content": "질문"},
    ]
    assert seen["body"]["options"]["num_predict"] == 300
    assert r.text == "한도는 1억원입니다 [1]." and r.model == "m"
    assert (r.input_tokens, r.output_tokens) == (120, 15)
    assert r.stop_reason == "stop" and r.latency_ms >= 0


def test_ollama_missing_token_counts_default_to_zero():
    # Ollama는 프롬프트가 캐시되면 prompt_eval_count를 빼고 보낼 때가 있다
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "답"}})

    r = OllamaClient("m", "http://x", transport=httpx.MockTransport(handler)).generate("s", "u")
    assert (r.input_tokens, r.output_tokens) == (0, 0) and r.stop_reason is None


def raising_transport(exc: Exception) -> httpx.MockTransport:
    def handler(request):
        raise exc

    return httpx.MockTransport(handler)


def test_ollama_connect_error_message_has_fix():
    llm = OllamaClient("m", "http://x", transport=raising_transport(httpx.ConnectError("refused")))
    with pytest.raises(LLMUnavailableError, match="ollama serve"):
        llm.generate("s", "u")


def test_ollama_read_timeout_raises():
    llm = OllamaClient(
        "m", "http://x", timeout=5, transport=raising_transport(httpx.ReadTimeout("slow"))
    )
    with pytest.raises(LLMUnavailableError, match="5"):
        llm.generate("s", "u")


def test_ollama_missing_model_suggests_pull():
    def handler(request):
        return httpx.Response(404, json={"error": 'model "m" not found, try pulling it first'})

    llm = OllamaClient("m", "http://x", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMUnavailableError, match="ollama pull m"):
        llm.generate("s", "u")


def test_ollama_server_error_raises():
    def handler(request):
        return httpx.Response(500, json={"error": "out of memory"})

    llm = OllamaClient("m", "http://x", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMUnavailableError, match="out of memory"):
        llm.generate("s", "u")


def test_ollama_unexpected_body_raises():
    def handler(request):
        return httpx.Response(200, text="not json")

    llm = OllamaClient("m", "http://x", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMUnavailableError):
        llm.generate("s", "u")


# ---- 추가: Claude 요청·응답 (가짜 HTTP, 네트워크·키 없음) ----


def claude_client(handler) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="test-key",
        base_url="http://anthropic.test",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def claude_message(content: list[dict], stop_reason: str, **extra) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 200, "output_tokens": 30},
        **extra,
    }


def test_claude_request_uses_fallback_and_no_sampling_params():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["beta"] = request.headers.get("anthropic-beta", "")
        seen["body"] = json.loads(request.content)
        content = [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "한도는 1억원입니다"},
            {"type": "text", "text": " [1]."},
        ]
        return httpx2.Response(200, json=claude_message(content, "end_turn"))

    llm = ClaudeClient("claude-opus-5-5", client=claude_client(handler))
    r = llm.generate("시스템", "질문")

    body = seen["body"]
    assert seen["path"] == "/v1/messages"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert body["fallbacks"] == "default"
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000
    assert body["system"] == "시스템"
    assert body["messages"] == [{"role": "user", "content": "질문"}]
    assert not {"temperature", "top_p", "top_k"} & body.keys()

    # thinking 블록은 빼고 text 블록만 합친다
    assert r.text == "한도는 1억원입니다 [1]."
    assert (r.input_tokens, r.output_tokens) == (200, 30)
    assert r.model == "claude-opus-5-5" and r.stop_reason == "end_turn"


def test_claude_refusal_returns_empty_text_and_logs(caplog):
    # 분류기는 출력 도중에도 거절할 수 있다. 거절 전에 나온 부분 출력은 답으로 쓰지 않는다
    def handler(request):
        details = {"type": "refusal", "category": "cyber", "explanation": None}
        partial = [{"type": "text", "text": "부분 답"}]
        return httpx2.Response(200, json=claude_message(partial, "refusal", stop_details=details))

    llm = ClaudeClient("claude-opus-5-5", client=claude_client(handler))
    with caplog.at_level(logging.WARNING, logger="axbuilder.generation.llm"):
        r = llm.generate("s", "u")
    assert r.text == "" and r.stop_reason == "refusal"
    assert "refusal" in caplog.text and "cyber" in caplog.text


def test_claude_invalid_key_raises():
    def handler(request):
        error = {"type": "authentication_error", "message": "invalid x-api-key"}
        return httpx2.Response(401, json={"type": "error", "error": error})

    llm = ClaudeClient("claude-opus-5-5", client=claude_client(handler))
    with pytest.raises(LLMUnavailableError, match="ANTHROPIC_API_KEY"):
        llm.generate("s", "u")


def test_claude_connection_error_raises():
    def handler(request):
        raise httpx2.ConnectError("no network")

    llm = ClaudeClient("claude-opus-5-5", client=claude_client(handler))
    with pytest.raises(LLMUnavailableError):
        llm.generate("s", "u")


def test_claude_overloaded_raises():
    def handler(request):
        error = {"type": "overloaded_error", "message": "Overloaded"}
        return httpx2.Response(529, json={"type": "error", "error": error})

    llm = ClaudeClient("claude-opus-5-5", client=claude_client(handler))
    with pytest.raises(LLMUnavailableError, match="529") as exc_info:
        llm.generate("s", "u")
    assert "잠시 후 다시 시도" in str(exc_info.value)


def test_claude_bad_request_does_not_suggest_retry():
    # 400 같은 요청 오류는 다시 보내도 같다. 재시도가 아니라 요청과 코드 확인을 안내한다
    def handler(request):
        error = {"type": "invalid_request_error", "message": "prompt is too long"}
        return httpx2.Response(400, json={"type": "error", "error": error})

    llm = ClaudeClient("claude-opus-5-5", client=claude_client(handler))
    with pytest.raises(LLMUnavailableError, match="400") as exc_info:
        llm.generate("s", "u")
    message = str(exc_info.value)
    assert "prompt is too long" in message and "잠시 후" not in message


def write_profile(config_dir, authentication: dict, credentials: dict) -> None:
    """`ant auth login`이 만드는 user_oauth 프로필(설정 + 자격 증명 파일)을 흉내 낸다."""
    (config_dir / "configs").mkdir(parents=True)
    (config_dir / "credentials").mkdir()
    config = {"authentication": authentication}
    (config_dir / "configs" / "default.json").write_text(json.dumps(config), encoding="utf-8")
    credentials_file = config_dir / "credentials" / "default.json"
    credentials_file.write_text(json.dumps(credentials), encoding="utf-8")
    credentials_file.chmod(0o600)  # Linux·macOS에서 SDK는 남이 읽을 수 있는 파일을 거부한다


@pytest.mark.parametrize(
    ("authentication", "credentials", "sdk_error", "detail"),
    [
        # 토큰이 빠진 프로필
        ({"type": "user_oauth"}, {}, anthropic.CredentialsError, "access_token"),
        # 갱신해야 하는 프로필인데 refresh_token이 없다(토큰 갱신 실패와 같은 예외)
        (
            {"type": "user_oauth", "client_id": "test-client"},
            {"access_token": "test-token"},
            anthropic.WorkloadIdentityError,
            "refresh_token",
        ),
    ],
    ids=["no-access-token", "no-refresh-token"],
)
def test_claude_broken_profile_raises_before_request(
    monkeypatch, tmp_path, no_anthropic_credentials, authentication, credentials, sdk_error, detail
):
    # 프로필이 깨졌으면 SDK가 요청을 보내기 전에 CredentialsError·WorkloadIdentityError를 낸다.
    # APIError 계열이 아니라서 따로 잡지 않으면 API에서 503이 아니라 500이 된다
    config_dir = tmp_path / "anthropic-config"
    write_profile(config_dir, authentication, credentials)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)  # 프로필 인증은 https만 허용
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=claude_message([], "end_turn"))

    client = anthropic.Anthropic(
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    assert client.api_key is None and client.credentials is not None  # 사전 검사는 통과한다

    llm = ClaudeClient("claude-opus-5-5", client=client)
    with pytest.raises(LLMUnavailableError, match="ant auth login") as exc_info:
        llm.generate("s", "u")
    assert isinstance(exc_info.value.__cause__, sdk_error)
    assert detail in str(exc_info.value)
    assert requests == []


def test_claude_missing_named_profile_raises(monkeypatch, no_anthropic_credentials):
    # ANTHROPIC_PROFILE로 고른 프로필이 없으면 SDK는 클라이언트를 만들 때 오류를 낸다.
    # 생성(get_llm)은 성공하고, 호출할 때 LLMUnavailableError로 알려야 한다
    monkeypatch.setenv("ANTHROPIC_PROFILE", "missing")
    llm = ClaudeClient("claude-opus-5-5")
    with pytest.raises(LLMUnavailableError, match="ant auth login") as exc_info:
        llm.generate("s", "u")
    assert isinstance(exc_info.value.__cause__, anthropic.CredentialsError)


def test_claude_default_client_sets_timeout_and_retries(monkeypatch, no_anthropic_credentials):
    # SDK 기본값(요청당 600초, 재시도 2번)이면 /ask 하나가 최악 30분 넘게 묶인다.
    # 기본 클라이언트는 우리가 정한 값으로 만든다 (요청은 보내지 않는다)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = ClaudeClient("claude-opus-5-5")._get_client()
    assert client.max_retries == CLAUDE_MAX_RETRIES == 1
    assert client.timeout == CLAUDE_TIMEOUT
    assert (CLAUDE_TIMEOUT.read, CLAUDE_TIMEOUT.connect) == (120.0, 5.0)
    # 시간 초과도 재시도되므로 최악의 대기 = 시도 횟수 × 한 번의 시간 제한 (백오프 제외)
    assert (CLAUDE_MAX_RETRIES + 1) * CLAUDE_TIMEOUT.read <= 300
