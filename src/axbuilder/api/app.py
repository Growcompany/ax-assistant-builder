"""REST API: 워크스페이스 만들기 → 문서 올리기 → 색인 → 질문.

create_app(settings, llm=None, embedder=None)이 앱을 만든다. 테스트는 FakeLLM·HashEmbedder를
넣고, 실행용 main.py는 설정을 따른다(LLM은 get_llm, 임베딩 모델은 처음 필요할 때 불러온다).

저장 위치 (data_dir/workspaces/{id}/):
  workspace.yaml               워크스페이스 설정 (WorkspaceStore)
  documents/{doc_id}{확장자}    올린 원본. 이름은 클라이언트가 보낸 파일 이름이 아니라 doc_id
  documents/{doc_id}.json      DocMeta + 확장자·크기·sha256
  index/qdrant/                dense 색인 (Qdrant 로컬 모드 파일)
  index/index.json             색인 정보(임베더, 차원, 청크 수). 색인을 다 만든 뒤에 쓴다

색인은 워크스페이스별로 메모리에 캐시한다. 서버를 다시 켜면 그 워크스페이스에 처음 질문할 때
index/의 Qdrant 색인을 열고, 그 안의 청크로 BM25를 다시 만든다(임베딩은 다시 계산하지 않는다).
임베더가 바뀌었거나 색인이 덜 만들어졌으면 409로 다시 색인하라고 알린다. 문서를 올린 뒤에는
POST /index를 다시 불러야 질문에 반영된다.
"""

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, BinaryIO

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints, field_validator

from axbuilder.corpus import DOC_ID
from axbuilder.generation.answer import answer
from axbuilder.generation.llm import LLMClient, LLMUnavailableError, get_llm
from axbuilder.ingest.chunking import chunk_document
from axbuilder.ingest.loaders import load_document
from axbuilder.models import Chunk, Citation, DocMeta, ScoredChunk
from axbuilder.retrieval.bm25 import BM25Index
from axbuilder.retrieval.dense import DenseIndex, Embedder
from axbuilder.retrieval.hybrid import HybridRetriever
from axbuilder.settings import Settings
from axbuilder.workspace import (
    WINDOWS_RESERVED,
    Workspace,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
    WorkspaceStore,
    is_valid_workspace_id,
)

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 파일 하나의 상한 (실제로 읽은 바이트로 센다)
# 요청 본문 전체의 상한 = 파일 상한 + 양식 필드·multipart 머리글 여유분
UPLOAD_OVERHEAD_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
SUPPORTED_SUFFIXES = (".pdf", ".hwpx", ".txt", ".md")  # load_document가 읽는 형식
SIGNATURE_WINDOW = 1024  # PDF 규격은 %PDF- 머리글이 처음 1024바이트 안에 있으면 된다
MAX_DOC_ID_LENGTH = 64
MAX_QUESTION_CHARS = 2000
COLLECTION = "chunks"
INDEX_INFO = "index.json"

# 오류 메시지에서 지울 로컬 경로: C:\…, \\server\…, ~/…, /home/…(두 단계 이상인 절대 경로).
# URL(http://host/path)은 남긴다: 드라이브 문자 앞에 글자가 없어야 하고,
# 절대 경로의 "/" 앞에는 ":"·"/"·글자가 없어야 한다
_LOCAL_PATH = re.compile(
    r"""(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\|~[\\/]|(?<![\w:/.~])/(?=[^\s/]+/))[^\s'"<>()]*"""
)


class IndexNotReadyError(Exception):
    """질문할 색인이 없거나 지금 설정과 맞지 않는다 (409)."""


class EmbedderUnavailableError(RuntimeError):
    """임베딩 모델을 불러올 수 없다 (503). 메시지에 해결 방법을 담는다."""


class UploadMeta(DocMeta):
    """업로드 양식의 DocMeta. doc_id는 저장 파일 이름이 되므로 경로 문자를 막는다."""

    @field_validator("doc_id")
    @classmethod
    def _check_doc_id(cls, value: str) -> str:
        # DOC_ID는 소문자·숫자 낱말을 "-"로 이은 것만 받는다: "/", "\", "..", 공백이 들어갈 수 없다.
        # "con.txt"처럼 Windows 장치 이름은 확장자가 붙어도 파일로 만들 수 없다
        if len(value) > MAX_DOC_ID_LENGTH or not DOC_ID.fullmatch(value):
            raise ValueError(
                f"doc_id must be lowercase letters/digits joined by '-' "
                f"(at most {MAX_DOC_ID_LENGTH} characters)"
            )
        if value in WINDOWS_RESERVED:
            raise ValueError("doc_id must not be a Windows device name")
        return value


Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
]


class AskRequest(BaseModel):
    question: Question  # 공백뿐인 질문도 422
    role: str


class AskResponse(BaseModel):
    answer: str  # 본문의 [i]는 citations[i-1]
    citations: list[Citation]
    refused: bool
    latency_ms: float  # 검색 + LLM 생성 시간


@dataclass
class _LoadedIndex:
    dense: DenseIndex  # Qdrant 로컬 클라이언트를 쥐고 있다. 버릴 때 close()
    retriever: HybridRetriever
    chunks: int


def _public_message(text: str) -> str:
    """API 응답에 넣을 오류 메시지. 서버의 로컬 경로(사용자 이름이 들어 있다)를 지운다."""
    return _LOCAL_PATH.sub("<path>", text)


def _size_label(n: int) -> str:
    return f"{n // (1024 * 1024)}MB" if n >= 1024 * 1024 else f"{n}바이트"


def _client_suffix(filename: str | None) -> str:
    """클라이언트 파일 이름에서는 확장자만 쓴다(경로·이름은 저장에 쓰지 않는다)."""
    return PurePosixPath((filename or "").replace("\\", "/")).suffix.lower()


def _chunk_order(chunk: Chunk) -> tuple[str, int, str]:
    """색인할 때와 다시 열 때 청크 순서를 같게 한다(BM25는 동점을 색인 순서로 정한다)."""
    number = chunk.chunk_id.rpartition("::")[2]
    return (
        chunk.doc_id,
        int(number) if number.isascii() and number.isdigit() else -1,
        chunk.chunk_id,
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """임시 파일에 쓴 뒤 바꿔 끼운다. 쓰다가 멈춰도 반쯤 쓴 파일이 남지 않는다."""
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _copy_limited(source: BinaryIO, target: Path) -> tuple[int, str, bytes, bool]:
    """source를 target에 복사한다 → (크기, sha256, 앞부분, NUL 포함 여부).

    상한은 Content-Length가 아니라 실제로 읽은 바이트로 검사한다(머리글은 거짓일 수 있다).
    """
    size = 0
    digest = hashlib.sha256()
    head = b""
    has_nul = False
    with target.open("xb") as out:
        while block := source.read(READ_CHUNK_BYTES):
            size += len(block)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"파일이 상한({_size_label(MAX_UPLOAD_BYTES)})을 넘습니다")
            if len(head) < SIGNATURE_WINDOW:
                head += block[: SIGNATURE_WINDOW - len(head)]
            has_nul = has_nul or b"\x00" in block
            digest.update(block)
            out.write(block)
    return size, digest.hexdigest(), head, has_nul


def _signature_matches(suffix: str, head: bytes, has_nul: bool) -> bool:
    """확장자만 믿지 않고 내용의 서명을 본다."""
    if suffix == ".pdf":
        return b"%PDF-" in head
    if suffix == ".hwpx":
        return head.startswith(b"PK\x03\x04")  # ZIP 로컬 파일 머리글
    return not has_nul  # .txt·.md: 텍스트에는 NUL이 없다 (UTF-16·바이너리를 걸러낸다)


def _check_parses(path: Path, meta: DocMeta, workspace_id: str) -> None:
    """올린 파일을 실제로 읽어 본다. 깨졌거나 텍스트가 없으면 422 (색인 때가 아니라 지금 알린다)."""
    try:
        chunks = chunk_document(load_document(path, meta, workspace_id))
    except Exception:
        # pdfminer·zipfile·XML·문자 인코딩 오류 등 파서마다 예외가 다르다. 상세는 서버 로그에만
        logger.warning("could not parse upload doc_id=%s", meta.doc_id, exc_info=True)
        raise HTTPException(
            422,
            "문서를 읽을 수 없습니다. 파일이 깨졌거나 내용이 확장자와 맞지 않습니다 "
            "(텍스트 파일은 UTF-8 또는 CP949)",
        ) from None
    if not chunks:
        raise HTTPException(
            422,
            "문서에서 텍스트를 뽑을 수 없습니다. 스캔본(이미지) PDF는 아직 지원하지 않습니다",
        )


class _Service:
    """앱의 상태: 저장소, LLM, 임베더(지연 로딩), 워크스페이스별 색인 캐시와 잠금."""

    def __init__(self, settings: Settings, llm: LLMClient | None, embedder: Embedder | None):
        self.store = WorkspaceStore(settings.data_dir / "workspaces")
        self.llm = llm if llm is not None else get_llm(settings)
        self._embed_model = settings.embed_model
        self._embedder = embedder
        # 색인 정보에 남겨, 서버를 다시 켰을 때 같은 임베더인지 비교한다
        self._embedder_name = settings.embed_model if embedder is None else type(embedder).__name__
        self._embedder_lock = threading.Lock()
        self._guard = threading.Lock()
        # 워크스페이스마다 잠금 하나: 색인 만들기·다시 열기·검색·문서 등록을 차례로 한다.
        # Qdrant 로컬 모드는 같은 폴더를 한 클라이언트만 열 수 있고, 동시 접근에 안전하지 않다
        self._locks: dict[str, threading.Lock] = {}
        self._indexes: dict[str, _LoadedIndex] = {}

    # ---- 공통 ----

    def lock(self, workspace_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(workspace_id, threading.Lock())

    def workspace(self, workspace_id: str) -> Workspace:
        # 형식이 틀린 id("..", "..\\etc", "con")는 그런 워크스페이스가 있을 수 없으므로 404.
        # 경로를 만들기 전에 거른다
        if not is_valid_workspace_id(workspace_id):
            raise HTTPException(404, "워크스페이스를 찾을 수 없습니다")
        try:
            return self.store.get(workspace_id)
        except WorkspaceNotFoundError:
            raise HTTPException(404, "워크스페이스를 찾을 수 없습니다") from None

    def _documents_dir(self, workspace_id: str) -> Path:
        return self.store.path(workspace_id) / "documents"

    def _index_dir(self, workspace_id: str) -> Path:
        return self.store.path(workspace_id) / "index"

    def _get_embedder(self) -> Embedder:
        with self._embedder_lock:
            if self._embedder is None:
                try:
                    # torch를 끌어오는 import라 실제로 필요할 때만 한다
                    from axbuilder.retrieval.dense import SentenceTransformerEmbedder

                    self._embedder = SentenceTransformerEmbedder(self._embed_model)
                except Exception as e:
                    logger.exception("could not load embedding model %s", self._embed_model)
                    raise EmbedderUnavailableError(
                        f"임베딩 모델 '{self._embed_model}'을 불러오지 못했습니다"
                        f'({type(e).__name__}). ml 의존성(pip install -e ".[ml]"), '
                        "AXB_EMBED_MODEL, GPU 상태를 확인하세요"
                    ) from e
            return self._embedder

    # ---- 문서 ----

    def add_document(self, ws: Workspace, meta: UploadMeta, upload: UploadFile) -> None:
        suffix = _client_suffix(upload.filename)
        if suffix not in SUPPORTED_SUFFIXES:
            raise HTTPException(
                415, f"지원하지 않는 형식입니다. 가능한 형식: {', '.join(SUPPORTED_SUFFIXES)}"
            )
        documents = self._documents_dir(ws.id)
        record_path = documents / f"{meta.doc_id}.json"
        if record_path.exists():
            raise HTTPException(409, f"문서 '{meta.doc_id}'가 이미 있습니다")
        documents.mkdir(parents=True, exist_ok=True)
        # 확장자는 load_document가 형식을 고르는 데 쓴다. 이름은 서버가 정한다
        temp = documents / f".upload-{uuid.uuid4().hex}{suffix}"
        try:
            size, sha256, head, has_nul = _copy_limited(upload.file, temp)
            if size == 0:
                raise HTTPException(422, "빈 파일입니다")
            if not _signature_matches(suffix, head, has_nul):
                raise HTTPException(415, f"파일 내용이 {suffix} 형식이 아닙니다")
            stored = DocMeta(**meta.model_dump())
            _check_parses(temp, stored, ws.id)
            with self.lock(ws.id):
                if record_path.exists():  # 같은 doc_id를 동시에 올린 경우
                    raise HTTPException(409, f"문서 '{meta.doc_id}'가 이미 있습니다")
                temp.replace(documents / f"{meta.doc_id}{suffix}")
                _write_json(
                    record_path,
                    {
                        "meta": stored.model_dump(mode="json"),
                        "suffix": suffix,
                        "bytes": size,
                        "sha256": sha256,
                        "uploaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    },
                )
        finally:
            temp.unlink(missing_ok=True)  # 실패했으면 임시 파일을 지운다 (성공하면 이미 옮겨짐)

    def _read_documents(self, workspace_id: str) -> tuple[list[Chunk], list[str]]:
        """올린 문서를 모두 읽어 청크로 나눈다 → (청크, doc_id 목록)."""
        documents = self._documents_dir(workspace_id)
        chunks: list[Chunk] = []
        doc_ids: list[str] = []
        records = sorted(documents.glob("*.json")) if documents.is_dir() else []
        for record_path in records:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            meta = DocMeta.model_validate(record["meta"])
            suffix = record["suffix"]
            # 기록 파일이 가리키는 경로도 doc_id·확장자로 다시 만든다(기록을 경로로 믿지 않는다)
            if meta.doc_id != record_path.stem or suffix not in SUPPORTED_SUFFIXES:
                raise ValueError(f"inconsistent document record {record_path.name}")
            path = documents / f"{meta.doc_id}{suffix}"
            chunks.extend(chunk_document(load_document(path, meta, workspace_id)))
            doc_ids.append(meta.doc_id)
        chunks.sort(key=_chunk_order)
        return chunks, sorted(doc_ids)

    # ---- 색인 ----

    def build_index(self, ws: Workspace) -> int:
        with self.lock(ws.id):
            chunks, doc_ids = self._read_documents(ws.id)
            embedder = self._get_embedder()
            bm25 = BM25Index()
            bm25.build(chunks)
            index_dir = self._index_dir(ws.id)
            # 같은 Qdrant 폴더는 한 클라이언트만 열 수 있다: 이전 클라이언트를 닫은 뒤 새로 연다
            self._drop_locked(ws.id)
            # 다 만들기 전에 서버가 꺼지면 다음 시작 때 "색인 없음"으로 보게 정보 파일을 먼저 지운다
            (index_dir / INDEX_INFO).unlink(missing_ok=True)
            index_dir.mkdir(parents=True, exist_ok=True)
            dense = DenseIndex(embedder, index_dir / "qdrant", COLLECTION)
            try:
                dense.build(chunks)
                _write_json(
                    index_dir / INDEX_INFO,
                    {
                        "embedder": self._embedder_name,
                        "dim": embedder.dim,
                        "chunks": len(chunks),
                        "documents": doc_ids,
                        "indexed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    },
                )
            except BaseException:
                dense.close()
                raise
            self._indexes[ws.id] = _LoadedIndex(dense, HybridRetriever(bm25, dense), len(chunks))
            return len(chunks)

    def loaded_index(self, workspace_id: str) -> _LoadedIndex:
        """잠금을 잡은 채로 부른다. 캐시에 없으면(서버 재시작) 폴더의 색인을 연다."""
        loaded = self._indexes.get(workspace_id)
        if loaded is not None:
            return loaded
        index_dir = self._index_dir(workspace_id)
        info_path = index_dir / INDEX_INFO
        if not info_path.is_file():
            raise IndexNotReadyError(
                f"워크스페이스 '{workspace_id}'에 색인이 없습니다. "
                f"먼저 POST /workspaces/{workspace_id}/index 를 호출하세요"
            )
        stale = IndexNotReadyError(
            f"워크스페이스 '{workspace_id}'의 색인이 지금 설정과 맞지 않습니다"
            "(임베딩 모델 변경 등). "
            f"POST /workspaces/{workspace_id}/index 로 다시 색인하세요"
        )
        info = json.loads(info_path.read_text(encoding="utf-8"))
        if info.get("embedder") != self._embedder_name:
            raise stale
        embedder = self._get_embedder()
        if info.get("dim") != embedder.dim:
            raise stale
        dense = DenseIndex(embedder, index_dir / "qdrant", COLLECTION)
        try:
            chunks = sorted(dense.chunks(), key=_chunk_order)
            if len(chunks) != info.get("chunks"):
                raise stale
        except BaseException:
            dense.close()
            raise
        bm25 = BM25Index()
        bm25.build(chunks)
        loaded = _LoadedIndex(dense, HybridRetriever(bm25, dense), len(chunks))
        self._indexes[workspace_id] = loaded
        logger.info("reopened index of %s (%d chunks)", workspace_id, len(chunks))
        return loaded

    def _drop_locked(self, workspace_id: str) -> None:
        loaded = self._indexes.pop(workspace_id, None)
        if loaded is not None:
            loaded.dense.close()

    def close(self) -> None:
        """종료할 때 Qdrant 클라이언트를 모두 닫아 폴더 잠금을 푼다."""
        with self._guard:
            workspace_ids = list(self._indexes)
        for workspace_id in workspace_ids:
            with self.lock(workspace_id):
                self._drop_locked(workspace_id)

    # ---- 질문 ----

    def ask(self, ws: Workspace, request: AskRequest) -> AskResponse:
        if request.role not in ws.roles:
            raise HTTPException(
                400, f"알 수 없는 역할입니다. 가능한 역할: {', '.join(ws.roles) or '(없음)'}"
            )
        started = time.perf_counter()
        # 권한 필터(역할 → 접근 등급)는 answer()가 검색 단계에서 건다. 볼 수 없는 문서는
        # 검색 결과에도, LLM 프롬프트에도 들어가지 않는다
        result = answer(
            request.question, _WorkspaceRetriever(self, ws.id), self.llm, ws, request.role
        )
        return AskResponse(
            answer=result.text,
            citations=result.citations,
            refused=result.refused,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class _WorkspaceRetriever:
    """answer()에 넘기는 검색기. 검색할 때마다 워크스페이스 잠금 안에서 지금 색인을 꺼낸다.

    재색인은 이전 Qdrant 클라이언트를 닫으므로, 검색기를 미리 꺼내 두면 닫힌 클라이언트를 쓸
    수 있다. 잠금은 검색하는 동안만 잡고, 느린 LLM 생성은 잠금 밖에서 한다.
    """

    def __init__(self, service: _Service, workspace_id: str) -> None:
        self._service = service
        self._workspace_id = workspace_id

    def search(
        self, query: str, k: int = 5, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]:
        with self._service.lock(self._workspace_id):
            index = self._service.loaded_index(self._workspace_id)
            return index.retriever.search(query, k=k, access_levels=access_levels)


class _BodySizeLimit:
    """요청 본문 상한. 받은 바이트를 세다가 넘으면 413 (Content-Length는 믿지 않는다).

    multipart는 FastAPI가 엔드포인트 전에 본문을 끝까지 읽어 임시 파일에 쌓으므로, 엔드포인트의
    파일 크기 검사만으로는 거대한 본문이 디스크를 채우는 것을 막지 못한다. 여기서 던진
    HTTPException은 FastAPI가 본문을 읽다 받은 그대로 다시 던져 413 응답이 된다.
    """

    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Any:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(
                        413, f"요청 본문이 상한({_size_label(self.max_bytes)})을 넘습니다"
                    )
            return message

        await self.app(scope, limited_receive, send)


def create_app(
    settings: Settings, llm: LLMClient | None = None, embedder: Embedder | None = None
) -> FastAPI:
    """llm이 없으면 get_llm(settings), embedder가 없으면 SentenceTransformerEmbedder(설정의
    embed_model)를 쓴다. 임베딩 모델은 처음 색인하거나 질문할 때 불러온다(서버가 빨리 뜨고,
    /health는 GPU 없이도 답한다)."""
    service = _Service(settings, llm, embedder)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="AX Assistant Builder", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.add_middleware(_BodySizeLimit, max_bytes=MAX_UPLOAD_BYTES + UPLOAD_OVERHEAD_BYTES)

    @app.exception_handler(LLMUnavailableError)
    async def _llm_unavailable(request: Request, exc: LLMUnavailableError) -> JSONResponse:
        logger.warning("LLM unavailable: %s", exc)
        detail = "LLM을 사용할 수 없습니다: " + _public_message(str(exc))
        return JSONResponse(status_code=503, content={"detail": detail})

    @app.exception_handler(EmbedderUnavailableError)
    async def _embedder_unavailable(
        request: Request, exc: EmbedderUnavailableError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": _public_message(str(exc))})

    @app.exception_handler(IndexNotReadyError)
    async def _index_not_ready(request: Request, exc: IndexNotReadyError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
        # 스택 트레이스·내부 경로는 서버 로그에만 남기고 응답에는 넣지 않는다
        logger.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "서버 내부 오류가 발생했습니다. 서버 로그를 확인하세요"},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/workspaces", status_code=201)
    def create_workspace(ws: Workspace) -> Workspace:
        try:
            return service.store.create(ws)
        except WorkspaceExistsError:
            raise HTTPException(409, f"워크스페이스 '{ws.id}'가 이미 있습니다") from None

    @app.post("/workspaces/{workspace_id}/documents", status_code=201)
    def upload_document(
        workspace_id: str, meta: Annotated[UploadMeta, Form()], file: UploadFile
    ) -> dict[str, str]:
        ws = service.workspace(workspace_id)
        service.add_document(ws, meta, file)
        return {"doc_id": meta.doc_id}

    @app.post("/workspaces/{workspace_id}/index")
    def build_index(workspace_id: str) -> dict[str, int]:
        ws = service.workspace(workspace_id)
        return {"chunks": service.build_index(ws)}

    @app.post("/workspaces/{workspace_id}/ask")
    def ask(workspace_id: str, request: AskRequest) -> AskResponse:
        ws = service.workspace(workspace_id)
        return service.ask(ws, request)

    return app
