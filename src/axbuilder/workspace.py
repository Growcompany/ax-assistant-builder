"""워크스페이스(회사 하나) 설정과 저장 위치. 모든 데이터는 root/{id}/ 아래에 둔다."""

import os
import re
import uuid
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator

# 워크스페이스 id는 폴더 이름이 된다. 허용 문자를 좁혀 "../etc" 같은 경로 조작을 막는다
WORKSPACE_ID = re.compile(r"[a-z0-9-]{2,32}")
# Windows가 장치 이름으로 예약해 폴더로 만들 수 없는 이름 (정규식은 통과한다)
WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"{p}{i}" for p in ("com", "lpt") for i in range(10)),
}
CONFIG_NAME = "workspace.yaml"
# LLM 모델 이름(Ollama의 "qwen3.5:4b-q4_K_M", "hf.co/org/model:Q4" 꼴). 이 이름은 Ollama
# 요청과 "ollama pull <이름>" 안내 문구에 들어가므로 공백·셸 문자·줄바꿈을 받지 않는다
LLM_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:-]{0,127}")


class WorkspaceExistsError(Exception):
    """같은 id의 워크스페이스가 이미 있다 (API에서는 409)."""


class WorkspaceNotFoundError(LookupError):
    """그런 id의 워크스페이스가 없다 (API에서는 404)."""


def is_valid_workspace_id(workspace_id: str) -> bool:
    # match + $는 끝의 "\n"을 통과시키므로 fullmatch로 문자열 전체를 검사한다
    return bool(WORKSPACE_ID.fullmatch(workspace_id)) and workspace_id not in WINDOWS_RESERVED


def is_valid_llm_model_name(name: str) -> bool:
    return bool(LLM_MODEL_NAME.fullmatch(name))


def validate_workspace_id(workspace_id: str) -> str:
    if not is_valid_workspace_id(workspace_id):
        raise ValueError(f"invalid workspace id {workspace_id!r}: need ^[a-z0-9-]{{2,32}}$")
    return workspace_id


class Workspace(BaseModel):
    id: str
    name: str
    industry: str
    # 역할 이름 → 그 역할이 볼 수 있는 문서 접근 등급
    roles: dict[str, list[Literal["public", "internal"]]]
    # 이 워크스페이스가 답할 때 쓸 LLM 모델. None이면 서버 기본 모델(설정의 local_model).
    # 필드가 생기기 전의 설정 파일에는 없으므로 기본값을 둔다
    llm_model: str | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return validate_workspace_id(value)

    @field_validator("llm_model")
    @classmethod
    def _check_llm_model(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_llm_model_name(value):
            raise ValueError("invalid model name: need ^[A-Za-z0-9][A-Za-z0-9._/:-]{0,127}$")
        return value


class WorkspaceStore:
    def __init__(self, root: Path):
        self.root = root

    def path(self, workspace_id: str) -> Path:
        """워크스페이스 폴더. id를 검사한 뒤에만 경로를 만든다 (디스크는 건드리지 않음)."""
        return self.root / validate_workspace_id(workspace_id)

    def create(self, ws: Workspace) -> Workspace:
        directory = self.path(ws.id)
        # 다운로드 스크립트가 raw/를 먼저 만들 수 있으므로 폴더가 아니라 설정 파일로 존재를 판단한다
        directory.mkdir(parents=True, exist_ok=True)
        try:
            # "x": 파일이 있으면 실패하는 배타적 생성. 확인 후 쓰기 사이의 경쟁 상태가 없다
            with (directory / CONFIG_NAME).open("x", encoding="utf-8") as f:
                yaml.safe_dump(ws.model_dump(mode="json"), f, allow_unicode=True, sort_keys=False)
        except FileExistsError as e:
            raise WorkspaceExistsError(f"workspace {ws.id!r} already exists") from e
        return ws

    def get(self, workspace_id: str) -> Workspace:
        config = self.path(workspace_id) / CONFIG_NAME
        try:
            text = config.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise WorkspaceNotFoundError(f"workspace {workspace_id!r} not found") from e
        ws = Workspace.model_validate(yaml.safe_load(text))
        if ws.id != workspace_id:
            raise ValueError(f"{config} has id {ws.id!r}, expected {workspace_id!r}")
        return ws

    def update(self, ws: Workspace) -> Workspace:
        """이미 있는 워크스페이스의 설정을 바꿔 쓴다. 없으면 WorkspaceNotFoundError."""
        config = self.path(ws.id) / CONFIG_NAME
        if not config.is_file():
            raise WorkspaceNotFoundError(f"workspace {ws.id!r} not found")
        # 임시 파일에 쓴 뒤 바꿔 끼운다: 쓰다가 멈춰도 설정 파일이 반쯤 쓴 채로 남지 않는다
        temp = config.with_name(f".{CONFIG_NAME}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8") as f:
                yaml.safe_dump(ws.model_dump(mode="json"), f, allow_unicode=True, sort_keys=False)
            os.replace(temp, config)
        finally:
            temp.unlink(missing_ok=True)
        return ws

    # 메서드 이름 list가 클래스 본문의 내장 list를 가리지 않도록 마지막에 둔다
    def list(self) -> list[Workspace]:
        if not self.root.is_dir():
            return []
        return [
            self.get(entry.name)
            for entry in sorted(self.root.iterdir())
            if is_valid_workspace_id(entry.name) and (entry / CONFIG_NAME).is_file()
        ]
