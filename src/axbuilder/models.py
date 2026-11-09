"""검색·답변 단계가 주고받는 데이터 모델."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Chunk(BaseModel):
    """색인·검색 단위. chunk_id 형식은 f"{doc_id}::{index:04d}"."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    doc_id: str
    workspace_id: str
    title: str
    text: str
    page: int | None = None
    article: str | None = None
    access_level: Literal["public", "internal"] = "public"


class ScoredChunk(BaseModel):
    chunk: Chunk
    score: float
