"""문서 적재·검색·답변 단계가 주고받는 데이터 모델."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Page(BaseModel):
    number: int = Field(ge=1)  # 1부터. 빈 페이지도 남겨 두어야 뒤 페이지 번호가 원문과 맞는다
    text: str


class DocMeta(BaseModel):
    doc_id: str
    title: str
    source: str
    doc_type: Literal["law", "regulation", "terms", "product", "internal", "other"]
    access_level: Literal["public", "internal"] = "public"
    effective_from: date | None = None
    effective_to: date | None = None


class Document(BaseModel):
    meta: DocMeta
    workspace_id: str
    pages: list[Page]


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


class LLMResponse(BaseModel):
    """LLM 한 번 호출의 결과. 클라이언트 구현(axbuilder.generation.llm)과 Answer가 함께 쓴다."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    stop_reason: str | None = None


class Citation(BaseModel):
    """답변이 근거로 든 청크의 위치. [n] 번호를 검색 결과 n번째 청크로 바꾼 것."""

    chunk_id: str
    doc_id: str
    title: str
    page: int | None
    article: str | None


class Answer(BaseModel):
    text: str
    citations: list[Citation]
    refused: bool
    retrieved: list[ScoredChunk]  # 프롬프트에 넣은 청크 (번호 = 순서 + 1). 평가가 검색 지표에 쓴다
    llm: LLMResponse | None  # 원문 응답. 검색 결과가 없어 LLM을 부르지 않았으면 None
