from pathlib import Path

import pytest

from axbuilder.settings import Settings

ENV_NAMES = [
    "AXB_DATA_DIR",
    "AXB_LLM_MODE",
    "AXB_LOCAL_MODEL",
    "AXB_OLLAMA_URL",
    "AXB_CLAUDE_MODEL",
    "AXB_EMBED_MODEL",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # 개발 PC에 AXB_* 값이 설정돼 있어도 테스트 결과가 같게
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_settings_defaults(monkeypatch):
    for name in ["AXB_LLM_MODE", "AXB_LOCAL_MODEL", "AXB_EMBED_MODEL"]:
        monkeypatch.delenv(name, raising=False)
    s = Settings.from_env()
    assert s.llm_mode == "local" and s.claude_model == "claude-opus-5-5"
    assert s.local_model == "qwen3.5:4b-q4_K_M"
    assert s.embed_model == "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
    assert s.data_dir == Path("data")
    assert s.ollama_url == "http://localhost:11434"


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("AXB_DATA_DIR", "/srv/axb")
    monkeypatch.setenv("AXB_LLM_MODE", "cloud")
    monkeypatch.setenv("AXB_LOCAL_MODEL", "exaone3.5:7.8b")
    monkeypatch.setenv("AXB_OLLAMA_URL", "http://gpu-box:11434")
    monkeypatch.setenv("AXB_CLAUDE_MODEL", "claude-test")
    monkeypatch.setenv("AXB_EMBED_MODEL", "BAAI/bge-m3")
    s = Settings.from_env()
    assert s.data_dir == Path("/srv/axb")
    assert (s.llm_mode, s.local_model, s.ollama_url) == (
        "cloud",
        "exaone3.5:7.8b",
        "http://gpu-box:11434",
    )
    assert (s.claude_model, s.embed_model) == ("claude-test", "BAAI/bge-m3")


def test_empty_env_value_falls_back_to_default(monkeypatch):
    # .env에 "AXB_LLM_MODE="처럼 값 없이 남겨 둔 줄은 설정하지 않은 것으로 본다
    monkeypatch.setenv("AXB_LLM_MODE", "")
    assert Settings.from_env().llm_mode == "local"


def test_invalid_llm_mode_raises(monkeypatch):
    monkeypatch.setenv("AXB_LLM_MODE", "gpu")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_api_key_is_not_kept_in_settings(monkeypatch):
    # 비밀값은 SDK가 환경변수에서 직접 읽는다. 설정 객체를 로그·응답에 찍어도 새지 않게
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    s = Settings.from_env()
    assert "sk-test-secret" not in repr(s) and "sk-test-secret" not in s.model_dump_json()
