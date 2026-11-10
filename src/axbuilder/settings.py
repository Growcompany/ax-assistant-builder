"""실행 설정. 값은 코드가 아니라 환경변수(AXB_*)에서 읽는다."""

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

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
}

DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")
# 호스트 이름·IPv4, 또는 대괄호로 싼 IPv6. 포트·스킴·경로는 받지 않는다
_HOST_NAME = re.compile(r"\[[0-9a-f:.]+\]|[a-z0-9]([a-z0-9.-]*[a-z0-9])?")


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
