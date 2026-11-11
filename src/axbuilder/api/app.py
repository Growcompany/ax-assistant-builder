"""REST API: 워크스페이스 만들기 → 문서 올리기 → 색인 → 질문.

create_app(settings, llm=None, embedder=None)이 앱을 만든다. 테스트는 FakeLLM·HashEmbedder를
넣고, 실행용 main.py는 설정을 따른다(임베딩 모델은 처음 필요할 때 불러온다).

저장 위치 (data_dir/workspaces/{id}/):
  workspace.yaml               워크스페이스 설정 (WorkspaceStore)
  documents/{doc_id}{확장자}    올린 원본. 이름은 클라이언트가 보낸 파일 이름이 아니라 doc_id
  documents/{doc_id}.json      DocMeta + 확장자·크기·sha256
  index/qdrant/                dense 색인 (Qdrant 로컬 모드 파일)
  index/index.json             색인 정보(임베더, 차원, 청크 수, 색인에 든 doc_id 목록).
                               색인을 다 만든 뒤에 쓴다
  keys.json                    API 키의 해시(원문 없음). axbuilder.api.keys

색인은 워크스페이스별로 메모리에 캐시한다. 서버를 다시 켜면 그 워크스페이스에 처음 질문할 때
index/의 Qdrant 색인을 열고, 그 안의 청크로 BM25를 다시 만든다(임베딩은 다시 계산하지 않는다).
임베더가 바뀌었거나 색인이 덜 만들어졌으면 409로 다시 색인하라고 알린다. 문서를 올린 뒤에는
POST /index를 다시 불러야 질문에 반영된다.

화면(static/)도 이 앱이 내준다: GET /은 static/index.html, GET /static/…은 그 폴더의 파일.
화면이 쓰는 조회 API(GET /workspaces, GET /workspaces/{id})는 색인을 열지 않고 파일만 읽는다.

LLM은 워크스페이스마다 고를 수 있다(workspace.yaml의 llm_model, 없으면 서버 기본 모델). 클라이언트는
모델 이름별로 하나씩 만들어 다시 쓴다. cloud 모드에서는 워크스페이스 설정과 무관하게 설정된 Claude
모델만 쓴다. create_app에 llm을 주면(테스트) 모든 모델에 그것을 쓰고, llm_factory를 주면 모델
이름으로 클라이언트를 만든다.

접근 규칙(미들웨어, 바깥 → 안):
  _RejectForeignHost   Host가 허용 목록에 없으면 403 (모든 경로)
  AccessRules          이 PC(loopback) 밖에서 온 요청은 /v1/* 만. /v1/* 는 API 키가 없으면 401
  _RejectCrossSite     다른 출처의 Origin, cross-site 상태 변경 요청은 403. 유효한 키가 있는
                       /v1/* 요청은 건너뛴다(키가 인증이다)
  _BodySizeLimit       본문 상한
  _UISecurityHeaders   화면 응답의 CSP 등
이 PC에서 온 요청의 역할은 요청이 정하고(/workspaces/{id}/ask), 키로 온 요청의 역할은 키가 정한다
(/v1/*, axbuilder.api.v1).
"""

import functools
import getpass
import hashlib
import json
import logging
import mimetypes
import re
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, BinaryIO, Literal

import httpx
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from axbuilder.api import v1
from axbuilder.api.access import KEY_SCOPE, AccessRules, is_v1_scope, openai_error
from axbuilder.api.keys import KeyStore
from axbuilder.corpus import DOC_ID, load_sources, source_file
from axbuilder.generation.answer import answer
from axbuilder.generation.llm import (
    ClaudeClient,
    LLMClient,
    LLMUnavailableError,
    OllamaClient,
    OllamaModel,
    OllamaNotRunningError,
    has_ollama_model,
    list_ollama_models,
    strip_url_credentials,
)
from axbuilder.ingest.chunking import chunk_document
from axbuilder.ingest.loaders import DocumentTooLargeError, load_document
from axbuilder.models import Chunk, Citation, DocMeta, LLMResponse, ScoredChunk
from axbuilder.retrieval.bm25 import BM25Index
from axbuilder.retrieval.dense import DenseIndex, Embedder
from axbuilder.retrieval.hybrid import HybridRetriever
from axbuilder.settings import Settings
from axbuilder.workspace import (
    CONFIG_NAME,
    WINDOWS_RESERVED,
    Workspace,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
    WorkspaceStore,
    is_valid_llm_model_name,
    is_valid_workspace_id,
)

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 파일 하나의 상한 (실제로 읽은 바이트로 센다)
# 업로드 요청 본문 전체의 상한 = 파일 상한 + 양식 필드·multipart 머리글 여유분
UPLOAD_OVERHEAD_BYTES = 1024 * 1024
# 업로드가 아닌 요청(JSON) 본문의 상한. 질문은 2000자, 워크스페이스 설정도 몇 KB면 된다
MAX_JSON_BYTES = 64 * 1024
# /v1/* 본문의 상한. 채팅 클라이언트는 이전 대화를 통째로 보낸다(쓰는 것은 마지막 user 메시지뿐).
# 키가 있는 요청만 여기까지 온다
MAX_V1_BYTES = 1024 * 1024
# /v1/* 로 내보내는 고정 문구 (상세는 서버 로그와 이 PC의 관리 API에만)
V1_LLM_UNAVAILABLE = "LLM을 사용할 수 없습니다. 이 어시스턴트의 관리자에게 문의하세요"
V1_SEARCH_UNAVAILABLE = "문서 검색을 사용할 수 없습니다. 이 어시스턴트의 관리자에게 문의하세요"
V1_INDEX_NOT_READY = "색인이 준비되지 않았습니다. 이 어시스턴트의 관리자에게 문의하세요"
READ_CHUNK_BYTES = 1024 * 1024
SUPPORTED_SUFFIXES = (".pdf", ".hwpx", ".txt", ".md")  # load_document가 읽는 형식
# 규격(ISO 32000)은 %PDF- 머리글을 첫 줄에 두라고 하지만, Adobe Reader는 앞 1024바이트 안이면
# 읽는다(PDF Reference 부록 H의 관례). 앞에 쓰레기가 붙은 실제 파일도 받으려고 관례를 따른다
SIGNATURE_WINDOW = 1024
MAX_DOC_ID_LENGTH = 64
MAX_QUESTION_CHARS = 2000
# 문서 하나의 청크 수 상한(약 7백만 자). 색인 시간·메모리와 재시작 후 BM25 재구성 비용을 묶는다
MAX_CHUNKS_PER_DOCUMENT = 10_000
# 동시에 도는 문서 파싱 수. PDF 하나가 해제 상한(256MB)이나 쪽당 객체 상한(약 200MB)까지 쓸 수
# 있어 최악 메모리를 묶는다
MAX_CONCURRENT_PARSES = 2
COLLECTION = "chunks"
INDEX_INFO = "index.json"
STATIC_DIR = Path(__file__).parent / "static"  # 화면 파일 (패키지 데이터)
# 화면 응답에 붙인다. 인라인 스크립트·스타일과 외부 출처를 막아, 문서 제목이나 답변의 글자가
# 혹시 마크업으로 들어가도 실행되지 않게 한다
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
# 예시 워크스페이스(POST /demo-workspace): 서버를 띄운 폴더 기준의 출처 목록에서 저장소에 있는
# 가상 문서(path 항목)만 등록한다
DEMO_SOURCES = Path("corpus") / "bank" / "sources.yaml"
DEMO_WORKSPACE = Workspace(
    id="demo",
    name="AXB은행(가상)",
    industry="은행",
    roles={"상담원": ["public"], "준법감시": ["public", "internal"]},
)

# Windows의 mimetypes는 레지스트리를 읽어 .js를 text/plain으로 알려 주기도 한다. nosniff와 함께면
# 브라우저가 모듈 스크립트·스타일을 쓰지 않으므로 화면에 쓰는 형식은 고정한다
for _media_type, _suffix in (
    ("text/javascript", ".js"),
    ("text/css", ".css"),
    ("image/svg+xml", ".svg"),
):
    mimetypes.add_type(_media_type, _suffix)
_UPLOAD_PATH = re.compile(r"/workspaces/[^/]+/documents")
# 상태를 바꾸는 요청에서 받아 주는 Sec-Fetch-Site 값 (None = 머리글 없음, 브라우저 밖 클라이언트)
_ALLOWED_FETCH_SITES = {None, b"same-origin", b"none"}
# Host 머리글 "호스트[:포트]"와 Origin 머리글 "스킴://호스트[:포트]". 호스트는 이름·IPv4 또는
# 대괄호로 싼 IPv6. 전체가 맞아야 한다(계정 정보 "@", 경로, 쉼표로 이은 값은 맞지 않는다)
_HOST_PORT = r"(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?"
_HOST_HEADER = re.compile(_HOST_PORT)
_ORIGIN_HEADER = re.compile(r"https?://" + _HOST_PORT, re.IGNORECASE)

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


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")  # 제어 문자와 유니코드 줄 구분자


def _single_line(value: str) -> str:
    # 제목·이름은 프롬프트의 자료 머리 줄·시스템 프롬프트에 그대로 들어간다. 줄바꿈이 있으면
    # "[질문]" 같은 구분 줄을 위조할 수 있다
    if _CONTROL.search(value):
        raise ValueError("must be a single line without control characters")
    # 형식 문자(Cf): 방향 제어(U+202E 등)·폭 없는 문자·태그 문자. 인용에 보이는 제목이 실제 글자와
    # 달라지거나, 눈에 안 보이는 글자가 프롬프트에 들어간다. 한글(완성형·자모·채움 문자)과 한자·
    # 기호·결합 문자는 Cf가 아니다. ZWJ로 이은 이모지는 함께 막힌다
    if any(unicodedata.category(char) == "Cf" for char in value):
        raise ValueError("must not contain invisible format characters (Unicode category Cf)")
    return value


def _line(max_length: int, min_length: int = 0) -> Any:
    """한 줄(제어 문자 없음), 앞뒤 공백 제거, 길이 상한이 있는 문자열 타입."""
    return Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length),
        AfterValidator(_single_line),
    ]


Title = _line(200, min_length=1)
Source = _line(500)
WorkspaceName = _line(100, min_length=1)
Industry = _line(50)
RoleName = _line(50, min_length=1)


class UploadMeta(DocMeta):
    """업로드 양식의 DocMeta. doc_id는 저장 파일 이름이 되므로 경로 문자를 막는다.

    access_level은 기본값 없이 반드시 받는다. DocMeta의 기본값(public)대로면 빠뜨린 내부 문서가
    모두에게 보인다(fail-open). DocMeta 계약(코퍼스 sources.yaml)은 그대로 둔다.
    """

    title: Title
    source: Source
    access_level: Literal["public", "internal"]

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


class NewWorkspace(Workspace):
    """POST /workspaces 본문. 이름은 질문마다 시스템 프롬프트에 들어가므로 한 줄·길이 상한을 둔다.
    저장하는 모양은 Workspace 그대로다."""

    name: WorkspaceName
    industry: Industry
    roles: Annotated[dict[RoleName, list[Literal["public", "internal"]]], Field(max_length=50)]


Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
]


class AskRequest(BaseModel):
    question: Question  # 공백뿐인 질문도 422
    role: str


class Passage(BaseModel):
    """인용한 청크의 원문."""

    chunk_id: str
    text: str


class AskResponse(BaseModel):
    answer: str  # 본문의 [i]는 citations[i-1]
    citations: list[Citation]
    refused: bool
    latency_ms: float  # 검색 + LLM 생성 시간
    passages: list[Passage]  # passages[i]는 citations[i]의 원문 (같은 순서, 같은 길이)
    # 답한 모델. LLM이 알려 준 이름(Claude는 대체 모델이 답하면 그 이름)이고, LLM을 부르지 않고
    # 거절했으면 이 워크스페이스가 쓰는 모델 이름
    model: str


class ModelInfo(OllamaModel):
    """고를 수 있는 LLM 모델 하나: name, size_bytes, parameter_size, quantization."""


class ModelList(BaseModel):
    available: bool  # 모델 목록을 읽을 수 있었는가 (local: Ollama가 켜져 있고 응답이 정상)
    default: str  # 워크스페이스에 llm_model이 없을 때 쓰는 서버 기본 모델
    models: list[ModelInfo]  # 이름순
    hint: str | None  # 사용자가 다음에 할 일(없으면 None)


class WorkspacePatch(BaseModel):
    """PATCH /workspaces/{id} 본문. 바꿀 수 있는 것은 모델뿐이다. null은 서버 기본 모델로."""

    # 모르는 필드(name, roles)를 조용히 무시하면 바뀐 줄 안다
    model_config = ConfigDict(extra="forbid")

    llm_model: str | None  # 기본값 없음: 빈 본문({})은 422

    @field_validator("llm_model")
    @classmethod
    def _check_llm_model(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_llm_model_name(value):
            raise ValueError("invalid model name: need ^[A-Za-z0-9][A-Za-z0-9._/:-]{0,127}$")
        return value


class IndexStatus(BaseModel):
    # none = 색인 없음, ready = 지금 문서가 모두 색인에 있음, stale = 다시 색인해야 함
    state: Literal["none", "ready", "stale"]
    chunks: int | None  # 지금 색인의 청크 수. 색인이 없거나 알 수 없으면 None


class WorkspaceSummary(Workspace):
    documents: int
    index: IndexStatus


class DocumentInfo(BaseModel):
    doc_id: str
    title: str
    source: str
    doc_type: str
    access_level: Literal["public", "internal"]
    extension: str  # ".pdf"처럼 점을 포함한다
    size_bytes: int
    indexed: bool  # 지금 색인에 들어 있는가


class WorkspaceDetail(WorkspaceSummary):
    documents_list: list[DocumentInfo]  # doc_id 오름차순


@dataclass
class _LoadedIndex:
    dense: DenseIndex  # Qdrant 로컬 클라이언트를 쥐고 있다. 버릴 때 close()
    retriever: HybridRetriever
    chunks: int


@functools.cache
def _local_identity() -> tuple[str, str]:
    """(홈 폴더, 사용자 이름). 알 수 없으면 빈 문자열."""
    try:
        home = str(Path.home())
    except RuntimeError:
        home = ""
    try:
        user = getpass.getuser()
    except Exception:  # 환경 변수도 계정 DB도 없으면 OSError 등
        user = ""
    return home, user


def _public_message(text: str) -> str:
    """API 응답에 넣을 오류 메시지. 서버의 로컬 경로(사용자 이름이 들어 있다)를 지운다.

    정규식은 공백에서 경로가 끝난다고 보므로 "C:\\Users\\John Doe\\..."는 "Doe\\..."가 남는다.
    그래서 홈 폴더와 사용자 이름을 먼저 글자 그대로 지운다.
    """
    home, user = _local_identity()
    if len(home) > 3:  # "C:\"·"/" 같은 루트는 지우지 않는다
        for variant in {home, home.replace("\\", "/")}:
            # 뒤에 경로 구분자·공백·끝이 와야 한다("C:\Users\hwan2"의 앞부분은 아니다)
            pattern = re.escape(variant) + r"""(?![^\\/\s'"<>()])"""
            text = re.sub(pattern, "~", text, flags=re.IGNORECASE)
    # 경로보다 먼저: URL의 계정 정보(http://user:pass@host)를 지운다
    text = strip_url_credentials(text)
    text = _LOCAL_PATH.sub("<path>", text)
    if len(user) >= 2:
        text = re.sub(rf"(?<!\w){re.escape(user)}(?!\w)", "<user>", text, flags=re.IGNORECASE)
    return text


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

    상한은 실제로 읽은 바이트로 검사한다. 파일 하나의 크기는 Content-Length로 알 수 없다
    (머리글은 multipart 본문 전체의 크기이고, chunked 전송에는 아예 없다).
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


class _Service:
    """앱의 상태: 저장소, LLM, 임베더(지연 로딩), 워크스페이스별 색인 캐시와 잠금."""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient | None,
        embedder: Embedder | None,
        llm_factory: Callable[[str], LLMClient] | None = None,
        ollama_transport: httpx.BaseTransport | None = None,
    ):
        self.store = WorkspaceStore(settings.data_dir / "workspaces")
        self.keys = KeyStore(self.store)
        self._cloud = settings.llm_mode == "cloud"
        self._ollama_url = settings.ollama_url
        self._ollama_transport = ollama_transport
        self.default_model = settings.claude_model if self._cloud else settings.local_model
        self._fixed_llm = llm  # 주입된 LLM(테스트): 모델 이름과 무관하게 이것이 답한다
        self._llm_factory = llm_factory if llm_factory is not None else self._make_llm
        self._llm_lock = threading.Lock()
        self._llms: dict[str, LLMClient] = {}
        if llm is None:
            # 기본 모델의 클라이언트는 시작할 때 만든다: 쓸 수 없는 AXB_OLLAMA_URL은 첫 질문이
            # 아니라 서버를 띄울 때 알린다
            self._llms[self.default_model] = self._llm_factory(self.default_model)
        self._embed_model = settings.embed_model
        self._embedder = embedder
        # 색인 정보에 남겨, 서버를 다시 켰을 때 같은 임베더인지 비교한다. 넣어 준 임베더도 모델
        # 이름이 있으면 그것을 쓴다(차원이 같은 다른 모델로 바꿔도 알아채게)
        model_name = getattr(embedder, "model_name", None)
        if embedder is None:
            self._embedder_name = settings.embed_model
        elif isinstance(model_name, str):
            self._embedder_name = model_name
        else:
            self._embedder_name = type(embedder).__name__
        self._embedder_lock = threading.Lock()
        self._parse_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PARSES)
        self._guard = threading.Lock()
        # 워크스페이스마다 잠금 하나: 색인 만들기·다시 열기·검색·문서 등록을 차례로 한다.
        # Qdrant 로컬 모드는 같은 폴더를 한 클라이언트만 열 수 있고, 동시 접근에 안전하지 않다
        self._locks: dict[str, threading.Lock] = {}
        self._indexes: dict[str, _LoadedIndex] = {}

    # ---- LLM ----

    def _make_llm(self, model: str) -> LLMClient:
        if self._cloud:
            return ClaudeClient(model)
        # 타임아웃·think:false·num_ctx는 OllamaClient가 정한다: 어느 모델이든 같은 설정
        return OllamaClient(model, self._ollama_url, transport=self._ollama_transport)

    def model_name(self, ws: Workspace) -> str:
        """이 워크스페이스가 답할 때 쓰는 모델. cloud 모드에서는 워크스페이스의 llm_model(로컬
        모델 이름)을 Claude API에 보내지 않고 설정된 Claude 모델을 쓴다."""
        if self._cloud or ws.llm_model is None:
            return self.default_model
        return ws.llm_model

    def llm_for(self, ws: Workspace) -> LLMClient:
        if self._fixed_llm is not None:
            return self._fixed_llm
        name = self.model_name(ws)
        with self._llm_lock:  # 모델 이름별로 하나만 만들어 다시 쓴다(HTTP 연결 재사용)
            client = self._llms.get(name)
            if client is None:
                client = self._llms[name] = self._llm_factory(name)
            return client

    def models(self) -> ModelList:
        default = self.default_model
        if self._cloud:
            return ModelList(
                available=True,
                default=default,
                models=[ModelInfo(name=default)],
                hint="클라우드 모드(AXB_LLM_MODE=cloud)에서는 설정된 Claude 모델로만 답합니다. "
                "워크스페이스별로 모델을 고를 수 없습니다",
            )
        try:
            installed = list_ollama_models(self._ollama_url, transport=self._ollama_transport)
        except OllamaNotRunningError:
            return ModelList(
                available=False,
                default=default,
                models=[],
                hint="Ollama가 켜져 있지 않습니다. Ollama를 설치하고 실행한 뒤 다시 확인하세요: "
                "ollama serve",
            )
        except LLMUnavailableError as e:
            # 받은 응답·예외 글자는 화면에 내보내지 않는다(서버 로그에만)
            logger.warning("could not list Ollama models: %s", e)
            return ModelList(
                available=False,
                default=default,
                models=[],
                hint="Ollama에서 모델 목록을 읽지 못했습니다. Ollama 상태와 AXB_OLLAMA_URL을 "
                "확인한 뒤 다시 확인하세요",
            )
        if not installed:
            hint = f"설치된 모델이 없습니다. 터미널에서 모델을 받으세요: ollama pull {default}"
        elif not has_ollama_model(installed, default):
            hint = (
                f"기본 모델 '{default}'이 설치되어 있지 않습니다. 다른 모델을 고르거나 "
                f"터미널에서 받으세요: ollama pull {default}"
            )
        else:
            hint = None
        return ModelList(
            available=True,
            default=default,
            models=[ModelInfo(**model.model_dump()) for model in installed],
            hint=hint,
        )

    def set_llm_model(self, ws: Workspace, llm_model: str | None) -> WorkspaceSummary:
        with self.lock(ws.id):
            try:
                # 잠금 안에서 다시 읽는다: 그 사이 바뀐 다른 필드를 덮어쓰지 않는다
                current = self.store.get(ws.id)
                updated = self.store.update(current.model_copy(update={"llm_model": llm_model}))
            except WorkspaceNotFoundError:
                raise HTTPException(404, "워크스페이스를 찾을 수 없습니다") from None
        return self.summary(updated)

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

    def _parse(self, path: Path, meta: DocMeta, workspace_id: str) -> list[Chunk]:
        """문서를 읽어 청크로 나눈다. 파싱은 메모리를 많이 쓸 수 있어 동시에 몇 개만 돌린다."""
        with self._parse_slots:
            return chunk_document(load_document(path, meta, workspace_id))

    def _check_parses(self, path: Path, meta: DocMeta, workspace_id: str) -> None:
        """올린 파일을 실제로 읽어 본다. 깨졌거나 텍스트가 없거나 너무 크면 422 (색인 때가 아니라
        지금 알린다)."""
        try:
            chunks = self._parse(path, meta, workspace_id)
        except DocumentTooLargeError:
            logger.warning("upload expands too much doc_id=%s", meta.doc_id, exc_info=True)
            raise HTTPException(
                422, "문서를 풀어 낸 내용이 상한을 넘습니다(압축 폭탄으로 봅니다)"
            ) from None
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
        if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
            raise HTTPException(
                422,
                f"문서가 너무 깁니다(청크 {len(chunks):,}개, 상한 {MAX_CHUNKS_PER_DOCUMENT:,}개). "
                "나눠서 올리세요",
            )

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
            self._check_parses(temp, stored, ws.id)
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
            chunks.extend(self._parse(path, meta, workspace_id))
            doc_ids.append(meta.doc_id)
        chunks.sort(key=_chunk_order)
        return chunks, sorted(doc_ids)

    # ---- 조회 (화면용) ----

    def _document_records(self, workspace_id: str) -> list[Path]:
        documents = self._documents_dir(workspace_id)
        return list(documents.glob("*.json")) if documents.is_dir() else []

    def _index_status(self, workspace_id: str, doc_ids: set[str]) -> tuple[IndexStatus, set[str]]:
        """(색인 상태, 색인에 든 doc_id). index.json만 읽는다(색인·임베딩 모델을 열지 않는다).

        잠금 없이 읽는다: index.json은 통째로 바꿔 끼우므로 반쯤 쓴 내용을 볼 일이 없다. 재색인
        도중에는 잠깐 "none"으로 보일 수 있다.
        """
        try:
            raw = (self._index_dir(workspace_id) / INDEX_INFO).read_text(encoding="utf-8")
        except FileNotFoundError:
            return IndexStatus(state="none", chunks=None), set()
        try:
            info = json.loads(raw)
        except ValueError:
            info = None
        if not isinstance(info, dict):
            info = {}
        chunks = info.get("chunks")
        if not isinstance(chunks, int) or isinstance(chunks, bool) or chunks < 0:
            chunks = None
        listed = info.get("documents")
        # 색인에 무엇이 들었는지 모르면(예전 형식·손상) ready라고 하지 않는다: 올렸는데 답에 안
        # 나오는 것보다 다시 색인하라고 하는 쪽이 안전하다
        known = isinstance(listed, list) and all(isinstance(item, str) for item in listed)
        indexed = set(listed) if known else set()
        # 임베더가 다른 색인은 질문하면 409(다시 색인)다. 화면에서도 준비됨으로 보이지 않게 한다
        ready = known and indexed == doc_ids and info.get("embedder") == self._embedder_name
        return IndexStatus(state="ready" if ready else "stale", chunks=chunks), indexed

    def summary(self, ws: Workspace) -> WorkspaceSummary:
        doc_ids = {record.stem for record in self._document_records(ws.id)}
        status, _ = self._index_status(ws.id, doc_ids)
        return WorkspaceSummary(**ws.model_dump(), documents=len(doc_ids), index=status)

    def detail(self, ws: Workspace) -> WorkspaceDetail:
        records = []
        for record_path in self._document_records(ws.id):
            record = json.loads(record_path.read_text(encoding="utf-8"))
            meta = DocMeta.model_validate(record["meta"])
            if meta.doc_id != record_path.stem:
                raise ValueError(f"inconsistent document record {record_path.name}")
            records.append((meta, record))
        records.sort(key=lambda item: item[0].doc_id)
        status, indexed = self._index_status(ws.id, {meta.doc_id for meta, _ in records})
        # 저장 정보(sha256, 올린 시각)는 화면에 필요 없어 내보내지 않는다
        documents = [
            DocumentInfo(
                doc_id=meta.doc_id,
                title=meta.title,
                source=meta.source,
                doc_type=meta.doc_type,
                access_level=meta.access_level,
                extension=record["suffix"],
                size_bytes=record["bytes"],
                indexed=meta.doc_id in indexed,
            )
            for meta, record in records
        ]
        return WorkspaceDetail(
            **ws.model_dump(), documents=len(documents), index=status, documents_list=documents
        )

    # ---- 예시 워크스페이스 ----

    def create_demo(self) -> int:
        """저장소의 가상 문서로 워크스페이스 "demo"를 만든다 → 등록한 문서 수. 색인은 하지 않는다.

        문서는 업로드와 같은 add_document()로 넣는다(형식·서명·파싱 검사, 같은 저장 모양).
        도중에 실패하면 만든 것을 모두 지운다: 반쯤 만든 워크스페이스가 남으면 다시 눌러도 409라
        화면에서 고칠 길이 없다.
        """
        root = Path.cwd()
        not_found = HTTPException(
            404,
            "예시 문서(corpus/bank)를 찾을 수 없습니다. 저장소 루트에서 서버를 실행하세요",
        )
        if not (root / DEMO_SOURCES).is_file():
            raise not_found
        sources = [s for s in load_sources(root / DEMO_SOURCES) if s.path is not None]
        # source_file은 path가 root 밖으로 풀리면 거절한다. path 항목은 raw_dir를 쓰지 않는다
        files = [(source, source_file(source, root, root)) for source in sources]
        if not files or not all(path.is_file() for _, path in files):
            raise not_found
        try:
            ws = self.store.create(DEMO_WORKSPACE)
        except WorkspaceExistsError:
            raise HTTPException(
                409, f"워크스페이스 '{DEMO_WORKSPACE.id}'가 이미 있습니다"
            ) from None
        added: list[str] = []
        doc_id = ""
        try:
            for source, path in files:
                doc_id = source.doc_id
                meta = UploadMeta(**source.doc_meta().model_dump())
                with path.open("rb") as stream:
                    self.add_document(ws, meta, UploadFile(stream, filename=path.name))
                added.append(f"{doc_id}{path.suffix.lower()}")
                added.append(f"{doc_id}.json")
        except BaseException as e:
            self._discard_demo(added)
            if isinstance(e, HTTPException):
                # 서버에 있는 파일의 문제라 요청 오류(4xx)가 아니다. 이유(빈 파일 등)는 알린다
                raise HTTPException(
                    500, f"예시 문서 '{doc_id}'를 등록하지 못했습니다: {e.detail}"
                ) from None
            raise
        return len(files)

    def _discard_demo(self, added: list[str]) -> None:
        directory = self.store.path(DEMO_WORKSPACE.id)
        # 설정 파일부터: 그 뒤로는 API에서 이 워크스페이스가 없는 것으로 보인다
        (directory / CONFIG_NAME).unlink(missing_ok=True)
        documents = self._documents_dir(DEMO_WORKSPACE.id)
        for name in added:  # 여기서 넣은 파일만 지운다
            (documents / name).unlink(missing_ok=True)
        for folder in (documents, directory):
            try:
                folder.rmdir()
            except OSError:  # 없거나, 다른 파일이 있으면 그대로 둔다
                pass

    # ---- 색인 ----

    def build_index(self, ws: Workspace) -> int:
        with self.lock(ws.id):
            chunks, doc_ids = self._read_documents(ws.id)
            embedder = self._get_embedder()
            bm25 = BM25Index()
            bm25.build(chunks)
            index_dir = self._index_dir(ws.id)
            info_path = index_dir / INDEX_INFO
            # 같은 Qdrant 폴더는 한 클라이언트만 열 수 있다: 이전 클라이언트를 닫은 뒤 새로 연다.
            # 아래에서 실패하면 캐시는 비어 있고, 다음 질문 때 폴더의 색인을 다시 연다
            self._drop_locked(ws.id)
            index_dir.mkdir(parents=True, exist_ok=True)
            dense = DenseIndex(embedder, index_dir / "qdrant", COLLECTION)
            try:
                # 정보 파일은 임베딩이 끝나고 저장된 점을 바꾸기 직전에 지운다.
                # - 임베딩에서 실패(GPU 메모리 부족 등): 점도 정보 파일도 그대로라 이전 색인으로
                #   계속 답한다
                # - 점을 바꾸는 도중 실패하거나 서버가 꺼짐: 정보 파일이 없어 "색인 없음"(409)
                dense.build(chunks, before_replace=lambda: info_path.unlink(missing_ok=True))
                _write_json(
                    info_path,
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
        self.keys.flush()  # 아직 쓰지 않은 "마지막 사용 시각"

    # ---- 질문 ----

    def ask(self, ws: Workspace, request: AskRequest) -> AskResponse:
        # 이 경로(관리 API)는 이 PC에서 온 요청만 닿는다: 역할을 요청이 정한다. 역할 필터는 인증이
        # 아니다. 밖에서 오는 요청은 API 키가 역할을 정한다(/v1/*, axbuilder.api.v1)
        if request.role not in ws.roles:
            raise HTTPException(
                400, f"알 수 없는 역할입니다. 가능한 역할: {', '.join(ws.roles) or '(없음)'}"
            )
        response, _ = self.answer_with_usage(ws, request.question, request.role)
        return response

    def answer_with_usage(
        self, ws: Workspace, question: str, role: str
    ) -> tuple[AskResponse, LLMResponse | None]:
        """(응답, LLM 원문 응답). role은 부르는 쪽이 이미 정한 값이다(요청 또는 키).
        LLM 응답은 토큰 수를 알려 주려고 함께 돌려준다. LLM을 부르지 않았으면 None."""
        started = time.perf_counter()
        # 권한 필터(역할 → 접근 등급)는 answer()가 검색 단계에서 건다. 볼 수 없는 문서는
        # 검색 결과에도, LLM 프롬프트에도 들어가지 않는다
        result = answer(question, _WorkspaceRetriever(self, ws.id), self.llm_for(ws), ws, role)
        # 원문은 이 질문에서 이 역할로 검색된 청크(result.retrieved)에서만 찾는다. 색인 전체에서
        # chunk_id로 찾지 않는다: 권한 밖 문서의 본문이 나갈 길을 만들지 않는다
        texts = {scored.chunk.chunk_id: scored.chunk.text for scored in result.retrieved}
        response = AskResponse(
            answer=result.text,
            citations=result.citations,
            refused=result.refused,
            latency_ms=(time.perf_counter() - started) * 1000,
            passages=[
                Passage(chunk_id=citation.chunk_id, text=texts[citation.chunk_id])
                for citation in result.citations
            ],
            model=result.llm.model if result.llm is not None else self.model_name(ws),
        )
        return response, result.llm


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
    """요청 본문 상한. 받은 바이트를 세다가 넘으면 413. 업로드 경로만 큰 상한을 쓴다.

    Content-Length 머리글만 보지 않는 이유: chunked 전송에는 이 머리글이 없다. 또 multipart는
    FastAPI가 엔드포인트 전에 본문을 끝까지 읽어 임시 파일에 쌓으므로, 엔드포인트의 파일 크기
    검사만으로는 거대한 본문이 디스크를 채우는 것을 막지 못한다. 여기서 던진 HTTPException은
    FastAPI가 본문을 읽다 받은 그대로 다시 던져 413 응답이 된다.
    """

    def __init__(self, app: Any, upload_bytes: int, other_bytes: int, v1_bytes: int) -> None:
        self.app = app
        self.upload_bytes = upload_bytes
        self.other_bytes = other_bytes
        self.v1_bytes = v1_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if _UPLOAD_PATH.fullmatch(scope["path"]) is not None:
            max_bytes = self.upload_bytes
        elif is_v1_scope(scope):
            max_bytes = self.v1_bytes
        else:
            max_bytes = self.other_bytes
        received = 0

        async def limited_receive() -> Any:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise HTTPException(
                        413, f"요청 본문이 상한({_size_label(max_bytes)})을 넘습니다"
                    )
            return message

        await self.app(scope, limited_receive, send)


class _UISecurityHeaders:
    """화면 응답(GET /, /static/…)에 CSP와 nosniff를 붙인다. 404 같은 오류 응답에도 붙는다.

    /docs(Swagger)는 CDN의 스크립트를 쓰므로 CSP를 붙이지 않는다. API의 JSON 응답도 그대로 둔다.
    Cache-Control: no-cache는 "쓰기 전에 서버에 확인"이다(ETag로 304). 서버를 새 판으로 바꿨는데
    브라우저가 예전 화면 스크립트를 계속 쓰는 일을 막는다.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope["path"] if scope["type"] == "http" else ""
        if path != "/" and path != "/static" and not path.startswith("/static/"):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Cache-Control"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _RejectCrossSite:
    """브라우저가 다른 출처(origin)의 페이지에서 보낸 상태 변경 요청(POST 등)을 403으로 막는다.

    multipart 업로드와 본문 없는 POST는 preflight 없는 "단순 요청"이라, 악성 페이지가 사용자
    브라우저를 시켜 127.0.0.1 서버에 문서를 심을 수 있다. 브라우저는 Sec-Fetch-Site 머리글을
    붙이고 페이지 스크립트는 이 값을 바꿀 수 없다. same-site도 막는다: 사이트는 포트를 보지 않아
    다른 포트의 로컬 개발 서버(localhost:3000) 페이지도 same-site다. same-origin(/docs), none
    (주소창 직접 입력), 머리글 없음(curl·httpx 같은 브라우저 밖 클라이언트)만 통과한다.

    Origin 머리글이 있으면 GET을 포함한 모든 요청에서 그 호스트가 허용 목록에 있어야 한다.
    Sec-Fetch-Site를 보내지 않는 구형 브라우저 대비다. 호스트만 본다(포트는 보지 않는다): 다른
    포트의 로컬 페이지는 최신 브라우저에서 Sec-Fetch-Site(same-site)로 막힌다.

    유효한 API 키로 온 /v1/* 요청(AccessRules가 scope에 표시)은 두 검사를 건너뛴다. 키는 페이지
    스크립트가 훔쳐 쓸 수 없는 인증이고(쿠키처럼 브라우저가 알아서 붙이지 않는다), 다른 출처의
    페이지에서 쓰라고 만든 길이다.
    """

    def __init__(self, app: Any, allowed_hosts: tuple[str, ...]) -> None:
        self.app = app
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket") and scope.get(KEY_SCOPE) is None:
            origins = [value for name, value in scope["headers"] if name == b"origin"]
            if origins and not _header_host_allowed(_ORIGIN_HEADER, origins, self.allowed_hosts):
                await _reject_host_or_origin(scope, receive, send)
                return
        if (
            scope["type"] == "http"
            and scope.get(KEY_SCOPE) is None
            and scope["method"] not in ("GET", "HEAD", "OPTIONS")
        ):
            site = dict(scope["headers"]).get(b"sec-fetch-site")
            if site not in _ALLOWED_FETCH_SITES:
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "다른 사이트에서 보낸 요청은 받지 않습니다"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _header_host_allowed(
    pattern: re.Pattern[str], values: list[bytes], allowed_hosts: frozenset[str]
) -> bool:
    if len(values) != 1:  # 없거나 여러 개: 어느 것을 믿을지 정하지 않고 거절한다
        return False
    match = pattern.fullmatch(values[0].decode("latin-1"))
    return match is not None and match.group(1).lower() in allowed_hosts


async def _reject_host_or_origin(scope: Any, receive: Any, send: Any) -> None:
    if scope["type"] == "websocket":  # 지금은 websocket 경로가 없다. 생겨도 막히게
        await send({"type": "websocket.close", "code": 1008})
        return
    # 받은 Host·Origin 값은 응답에 되돌려 주지 않는다
    response = JSONResponse(
        status_code=403,
        content={
            "detail": "허용하지 않는 Host 또는 Origin입니다. 서버 주소"
            "(127.0.0.1·localhost)로 접속하거나 AXB_ALLOWED_HOSTS를 확인하세요"
        },
    )
    await response(scope, receive, send)


class _RejectForeignHost:
    """Host 머리글의 호스트가 허용 목록에 없으면 GET을 포함한 모든 요청을 403 (/v1/* 포함).

    DNS 리바인딩 방어다. 공격자가 자기 도메인(attacker.example)의 DNS 응답을 127.0.0.1로 바꾸면,
    그 페이지의 스크립트에게 이 서버는 "같은 출처"가 된다: Sec-Fetch-Site가 same-origin이라
    _RejectCrossSite를 통과하고, CORS 없이 응답(내부 문서로 만든 답)도 읽는다. 이때도 브라우저가
    보내는 Host는 공격자 도메인 그대로이므로, 서버가 자기 이름이 아닌 Host를 거절하면 막힌다.

    Origin 머리글의 검사는 _RejectCrossSite가 한다(유효한 키가 있는 /v1/* 요청은 건너뛰어야 해서
    키를 확인하는 AccessRules보다 안쪽에 있다).
    """

    def __init__(self, app: Any, allowed_hosts: tuple[str, ...]) -> None:
        self.app = app
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            hosts = [value for name, value in scope["headers"] if name == b"host"]
            if not _header_host_allowed(_HOST_HEADER, hosts, self.allowed_hosts):
                await _reject_host_or_origin(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_service(
    settings: Settings,
    llm: LLMClient | None = None,
    embedder: Embedder | None = None,
    *,
    llm_factory: Callable[[str], LLMClient] | None = None,
    ollama_transport: httpx.BaseTransport | None = None,
) -> _Service:
    """HTTP 없이 같은 서비스 계층을 쓴다(MCP 서버). 인자는 create_app과 같다. 다 쓰면 close()."""
    return _Service(settings, llm, embedder, llm_factory, ollama_transport)


def create_app(
    settings: Settings,
    llm: LLMClient | None = None,
    embedder: Embedder | None = None,
    *,
    llm_factory: Callable[[str], LLMClient] | None = None,
    ollama_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    """llm이 없으면 설정대로 모델별 클라이언트(local: OllamaClient, cloud: ClaudeClient)를 만들고,
    embedder가 없으면 SentenceTransformerEmbedder(설정의 embed_model)를 쓴다. 임베딩 모델은 처음
    색인하거나 질문할 때 불러온다(서버가 빨리 뜨고, /health는 GPU 없이도 답한다).

    테스트용: llm을 주면 모든 워크스페이스가 그것으로 답한다. llm_factory(모델 이름)를 주면 모델별
    클라이언트를 그것으로 만든다. ollama_transport는 Ollama로 가는 요청(모델 목록, 기본 공장의
    OllamaClient)에 쓴다."""
    service = _Service(settings, llm, embedder, llm_factory, ollama_transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="AX Assistant Builder", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.add_middleware(_UISecurityHeaders)
    app.add_middleware(
        _BodySizeLimit,
        upload_bytes=MAX_UPLOAD_BYTES + UPLOAD_OVERHEAD_BYTES,
        other_bytes=MAX_JSON_BYTES,
        v1_bytes=MAX_V1_BYTES,
    )
    # 마지막에 더한 것이 바깥이다. 순서(바깥 → 안): Host → 접근 규칙(loopback, 키) → Origin·
    # Sec-Fetch-Site → 본문 상한. 본문을 읽기 전에 모두 거른다
    app.add_middleware(_RejectCrossSite, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        AccessRules,
        keys=service.keys,
        store=service.store,
        cors_origins=settings.cors_origins,
    )
    app.add_middleware(_RejectForeignHost, allowed_hosts=settings.allowed_hosts)

    def error(
        request: Request, status_code: int, detail: str, code: str, v1_detail: str | None = None
    ) -> JSONResponse:
        """/v1/* 는 OpenAI 오류 모양, 그 밖은 {"detail": …}.

        v1_detail: /v1/* 로 내보낼 문구. 키로 부르는 쪽은 이 PC 밖의 사용자일 수 있어서, 백엔드
        상세(내부 Ollama 주소, 모델 이름, 그 서버가 돌려준 본문, 관리 API 경로)를 주지 않는다.
        상세와 해결 방법은 이 PC의 관리 API(화면)와 서버 로그에만 둔다.
        """
        if is_v1_scope(request.scope):
            return openai_error(status_code, v1_detail or detail, code)
        return JSONResponse(status_code=status_code, content={"detail": detail})

    @app.exception_handler(LLMUnavailableError)
    async def _llm_unavailable(request: Request, exc: LLMUnavailableError) -> JSONResponse:
        # local(Ollama)과 cloud(Claude)의 오류가 모두 이 예외로 온다
        logger.warning("LLM unavailable: %s", exc)
        detail = "LLM을 사용할 수 없습니다: " + _public_message(str(exc))
        return error(request, 503, detail, "llm_unavailable", V1_LLM_UNAVAILABLE)

    @app.exception_handler(EmbedderUnavailableError)
    async def _embedder_unavailable(
        request: Request, exc: EmbedderUnavailableError
    ) -> JSONResponse:
        logger.warning("embedder unavailable: %s", exc)
        detail = _public_message(str(exc))
        return error(request, 503, detail, "embedder_unavailable", V1_SEARCH_UNAVAILABLE)

    @app.exception_handler(IndexNotReadyError)
    async def _index_not_ready(request: Request, exc: IndexNotReadyError) -> JSONResponse:
        return error(request, 409, str(exc), "index_not_ready", V1_INDEX_NOT_READY)

    @app.exception_handler(Exception)
    async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
        # 스택 트레이스·내부 경로는 서버 로그에만 남기고 응답에는 넣지 않는다
        logger.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        detail = "서버 내부 오류가 발생했습니다. 서버 로그를 확인하세요"
        return error(request, 500, detail, "internal_error")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if not is_v1_scope(request.scope):
            return await http_exception_handler(request, exc)
        return openai_error(
            exc.status_code, str(exc.detail), getattr(exc, "code", None), exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
        if not is_v1_scope(request.scope):
            return await request_validation_exception_handler(request, exc)
        # OpenAI 클라이언트는 요청 오류를 400으로 안다. 받은 값은 되돌려 주지 않는다
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = str(first.get("msg", "invalid request"))
        return openai_error(400, f"{where}: {message}" if where else message, "invalid_request")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def ui() -> FileResponse:
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(404, "화면 파일(static/index.html)이 없습니다")
        return FileResponse(index, media_type="text/html")

    @app.get("/models")
    def list_models() -> ModelList:
        return service.models()

    @app.patch("/workspaces/{workspace_id}")
    def patch_workspace(workspace_id: str, patch: WorkspacePatch) -> WorkspaceSummary:
        return service.set_llm_model(service.workspace(workspace_id), patch.llm_model)

    @app.get("/workspaces")
    def list_workspaces() -> list[WorkspaceSummary]:
        return [service.summary(ws) for ws in service.store.list()]  # id 오름차순

    @app.get("/workspaces/{workspace_id}")
    def get_workspace(workspace_id: str) -> WorkspaceDetail:
        return service.detail(service.workspace(workspace_id))

    @app.post("/demo-workspace", status_code=201)
    def create_demo_workspace() -> dict[str, str | int]:
        return {"id": DEMO_WORKSPACE.id, "documents": service.create_demo()}

    @app.post("/workspaces", status_code=201)
    def create_workspace(ws: NewWorkspace) -> Workspace:
        try:
            return service.store.create(Workspace.model_validate(ws.model_dump()))
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

    v1.add_routes(app, service)

    # 경로 조작("..", 절대 경로)은 StaticFiles가 폴더 밖이면 404로 막는다. 폴더가 아직 없어도
    # 앱은 뜬다(그때는 404)
    app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
    return app
