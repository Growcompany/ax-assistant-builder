import json
import logging

import anthropic
import httpx
import httpx2
import pytest

from axbuilder.generation.llm import (
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
    with pytest.raises(LLMUnavailableError):
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
    def handler(request):
        details = {"type": "refusal", "category": "cyber", "explanation": None}
        return httpx2.Response(200, json=claude_message([], "refusal", stop_details=details))

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
    with pytest.raises(LLMUnavailableError, match="529"):
        llm.generate("s", "u")
