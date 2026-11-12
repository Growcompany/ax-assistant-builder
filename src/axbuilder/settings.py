"""실행 설정. 값은 코드가 아니라 환경변수(AXB_*)에서 읽는다."""

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 필드 이름 → 환경변수 이름. ANTHROPIC_API_KEY 같은 비밀값은 여기 두지 않는다:
# SDK가 환경변수에서 직접 읽으므로, 설정 객체를 로그에 찍어도 키가 새지 않는다
ENV_VARS = {
    "data_dir": "AXB_DATA_DIR",
    "llm_mode": "AXB_LLM_MODE",
    "local_model": "AXB_LOCAL_MODEL",
    "ollama_url": "AXB_OLLAMA_URL",
    "claude_model": "AXB_CLAUDE_MODEL",
    "embed_model": "AXB_EMBED_MODEL",
    "allowed_hosts": "AXB_ALLOWED_HOSTS",
    "cors_origins": "AXB_CORS_ORIGINS",
    "api_docs": "AXB_API_DOCS",
    "reranker": "AXB_RERANKER",
    "rerank_candidates": "AXB_RERANK_CANDIDATES",
    "max_chunk_chars": "AXB_MAX_CHUNK_CHARS",
}

DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")
# max_chunk_chars의 상한. 평가 CLI(--max-chunk-chars)도 같은 값을 쓴다. 나누는 대상인 본문 조항은 이 코퍼스에서
# 가장 긴 것이 3,572자라 그보다 크면 나뉘는 것이 없다(11,452자짜리 부칙 청크는 원래 나누지 않는다)
MAX_CHUNK_CHARS_LIMIT = 20_000
# 호스트 이름·IPv4, 또는 대괄호로 싼 IPv6. 포트·스킴·경로는 받지 않는다
_HOST_NAME = re.compile(r"\[[0-9a-f:.]+\]|[a-z0-9]([a-z0-9.-]*[a-z0-9])?")
# 브라우저가 보내는 Origin 머리글 모양 그대로: 스킴://호스트[:포트]. 경로·계정 정보·"*"는 없다
_ORIGIN = re.compile(
    r"https?://(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::([0-9]{1,5}))?"
)


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path = Path("data")
    llm_mode: Literal["local", "cloud"] = "local"
    # Apache-2.0. EXAONE은 비상업 라이선스라 비교군으로만 쓴다
    local_model: str = "qwen3.5:4b-q4_K_M"
    ollama_url: str = "http://localhost:11434"
    claude_model: str = "claude-opus-5-5"
    embed_model: str = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
    # API가 받아 주는 Host 머리글의 호스트 이름(포트는 무엇이든). 환경변수는 쉼표로 구분하고,
    # 주면 기본 목록을 대신한다. DNS 리바인딩 방어라 "*" 같은 와일드카드는 없다
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    # /v1/* 응답에 CORS 머리글을 달아 줄 출처(브라우저에서 직접 부르는 페이지). 환경변수는 쉼표로
    # 구분한다. 기본은 빈 값(꺼짐). 정확히 일치하는 출처에만 달고 와일드카드는 없다
    cors_origins: tuple[str, ...] = ()
    # /docs(Swagger)·/redoc을 켠다. 기본은 끔: 그 화면은 외부 CDN의 스크립트를 불러오고, 그
    # 안의 버튼으로 관리 API가 실행된다. 개발할 때만 켠다(true/false)
    api_docs: bool = False
    # 검색 결과를 크로스 인코더로 다시 줄 세운다(axbuilder.retrieval.rerank의 RERANKER_MODELS).
    # 기본은 끔: 모델을 더 받아야 하고 질문마다 시간이 더 든다. 색인과는 무관하다
    reranker: Literal["none", "kure-nano", "bge-v2-m3"] = "none"
    # 리랭커에 넘기는 후보 수. 리랭커가 꺼져 있으면 쓰지 않는다
    rerank_candidates: int = Field(default=30, ge=1, le=200)
    # 본문 조항이 이 글자 수보다 길면 항·호·목·줄 경계에서 나눈다(axbuilder.ingest.chunking).
    # 기본 None은 나누지 않는다. 색인에 남고, 바꾸면 다시 색인해야 한다. 하한은
    # chunking.MIN_MAX_CHARS와 같다
    max_chunk_chars: int | None = Field(default=None, ge=200, le=MAX_CHUNK_CHARS_LIMIT)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, (list, tuple)):
            return value
        origins = tuple(str(item).strip().lower() for item in value if str(item).strip())
        for origin in origins:
            match = _ORIGIN.fullmatch(origin)
            if match is None or (match.group(1) and int(match.group(1)) > 65535):
                raise ValueError(
                    f"cors_origins entry {origin!r} must be an origin such as "
                    "https://app.example.com or http://localhost:3000 (no path, no wildcard)"
                )
        return origins

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _parse_allowed_hosts(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, (list, tuple)):
            return value
        hosts = tuple(str(item).strip().lower() for item in value if str(item).strip())
        if not hosts:
            raise ValueError("allowed_hosts must name at least one host")
        for host in hosts:
            if not _HOST_NAME.fullmatch(host):
                raise ValueError(
                    f"allowed_hosts entry {host!r} must be a host name without scheme or port "
                    "(IPv6 in brackets, e.g. [::1])"
                )
        return hosts

    @classmethod
    def from_env(cls) -> "Settings":
        # 빈 값("AXB_LLM_MODE=")은 설정하지 않은 것으로 보고 기본값을 쓴다
        values = {field: os.environ[var] for field, var in ENV_VARS.items() if os.environ.get(var)}
        return cls(**values)
