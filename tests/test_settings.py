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
    "AXB_ALLOWED_HOSTS",
    "AXB_RERANKER",
    "AXB_RERANK_CANDIDATES",
    "AXB_MAX_CHUNK_CHARS",
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


def test_allowed_hosts_default_is_loopback_only():
    assert Settings.from_env().allowed_hosts == ("127.0.0.1", "localhost", "[::1]")


def test_allowed_hosts_from_env_is_comma_separated_and_replaces_default(monkeypatch):
    monkeypatch.setenv("AXB_ALLOWED_HOSTS", " 127.0.0.1, AXB.Internal ,,[::1] ")
    assert Settings.from_env().allowed_hosts == ("127.0.0.1", "axb.internal", "[::1]")


@pytest.mark.parametrize(
    "bad", ["*", "http://localhost", "localhost:8000", "a b", "::1", " , ", "exa_mple.com", "-a"]
)
def test_allowed_hosts_rejects_wildcards_ports_and_urls(monkeypatch, bad):
    monkeypatch.setenv("AXB_ALLOWED_HOSTS", bad)
    with pytest.raises(ValueError):
        Settings.from_env()


# ---- 리랭커 (Task 22) ----


def test_reranker_is_off_by_default_with_30_candidates():
    s = Settings.from_env()
    assert (s.reranker, s.rerank_candidates) == ("none", 30)


@pytest.mark.parametrize("key", ["none", "kure-nano", "bge-v2-m3"])
def test_reranker_from_env(monkeypatch, key):
    monkeypatch.setenv("AXB_RERANKER", key)
    monkeypatch.setenv("AXB_RERANK_CANDIDATES", "50")
    s = Settings.from_env()
    assert (s.reranker, s.rerank_candidates) == (key, 50)


def test_reranker_choices_are_the_models_the_code_knows():
    from typing import get_args

    from axbuilder.retrieval.rerank import RERANKER_MODELS

    choices = get_args(Settings.model_fields["reranker"].annotation)
    assert set(choices) == {"none", *RERANKER_MODELS}


@pytest.mark.parametrize("bad", ["kure", "KURE-NANO", "nlpai-lab/KURE-Reranker-nano", "true"])
def test_unknown_reranker_is_a_settings_error(monkeypatch, bad):
    monkeypatch.setenv("AXB_RERANKER", bad)
    with pytest.raises(ValueError, match="reranker"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["1", "200"])
def test_rerank_candidates_range_ends_are_allowed(monkeypatch, value):
    monkeypatch.setenv("AXB_RERANK_CANDIDATES", value)
    assert Settings.from_env().rerank_candidates == int(value)


@pytest.mark.parametrize("bad", ["0", "-1", "201", "many", "30.5"])
def test_rerank_candidates_out_of_range_is_a_settings_error(monkeypatch, bad):
    monkeypatch.setenv("AXB_RERANK_CANDIDATES", bad)
    with pytest.raises(ValueError, match="rerank_candidates"):
        Settings.from_env()


def test_empty_reranker_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("AXB_RERANKER", "")
    monkeypatch.setenv("AXB_RERANK_CANDIDATES", "")
    s = Settings.from_env()
    assert (s.reranker, s.rerank_candidates) == ("none", 30)


# ---- 긴 조항 나누기 (M-2 Task 25) ----


def test_max_chunk_chars_is_off_by_default():
    assert Settings.from_env().max_chunk_chars is None


@pytest.mark.parametrize("value", ["200", "800", "20000"])
def test_max_chunk_chars_from_env(monkeypatch, value):
    monkeypatch.setenv("AXB_MAX_CHUNK_CHARS", value)
    assert Settings.from_env().max_chunk_chars == int(value)


@pytest.mark.parametrize("bad", ["0", "-1", "199", "20001", "many", "800.5", "none"])
def test_max_chunk_chars_out_of_range_is_a_settings_error(monkeypatch, bad):
    monkeypatch.setenv("AXB_MAX_CHUNK_CHARS", bad)
    with pytest.raises(ValueError, match="max_chunk_chars"):
        Settings.from_env()


def test_empty_max_chunk_chars_means_no_split(monkeypatch):
    monkeypatch.setenv("AXB_MAX_CHUNK_CHARS", "")
    assert Settings.from_env().max_chunk_chars is None


def test_max_chunk_chars_lower_bound_is_what_the_chunker_accepts():
    from axbuilder.ingest.chunking import MIN_MAX_CHARS

    field = Settings.model_fields["max_chunk_chars"]
    lower = next(m.ge for m in field.metadata if getattr(m, "ge", None) is not None)
    assert lower == MIN_MAX_CHARS
