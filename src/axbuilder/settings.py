"""실행 설정. 값은 코드가 아니라 환경변수(AXB_*)에서 읽는다."""

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

# 필드 이름 → 환경변수 이름. ANTHROPIC_API_KEY 같은 비밀값은 여기 두지 않는다:
# SDK가 환경변수에서 직접 읽으므로, 설정 객체를 로그에 찍어도 키가 새지 않는다
ENV_VARS = {
    "data_dir": "AXB_DATA_DIR",
    "llm_mode": "AXB_LLM_MODE",
    "local_model": "AXB_LOCAL_MODEL",
    "ollama_url": "AXB_OLLAMA_URL",
    "claude_model": "AXB_CLAUDE_MODEL",
    "embed_model": "AXB_EMBED_MODEL",
}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path = Path("data")
    llm_mode: Literal["local", "cloud"] = "local"
    # Apache-2.0. EXAONE은 비상업 라이선스라 비교군으로만 쓴다
    local_model: str = "qwen3.5:4b-q4_K_M"
    ollama_url: str = "http://localhost:11434"
    claude_model: str = "claude-opus-5-5"
    embed_model: str = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"

    @classmethod
    def from_env(cls) -> "Settings":
        # 빈 값("AXB_LLM_MODE=")은 설정하지 않은 것으로 보고 기본값을 쓴다
        values = {field: os.environ[var] for field, var in ENV_VARS.items() if os.environ.get(var)}
        return cls(**values)
