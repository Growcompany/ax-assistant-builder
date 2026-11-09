"""코퍼스 출처 목록(corpus/{workspace_id}/sources.yaml)을 읽고, url 문서를 받아 raw/에 저장한다.

원문은 저장소에 커밋하지 않는다. 출처 목록과 이 모듈만 커밋하고, 원문은 실행할 때 받아
data/workspaces/{id}/raw/(git 제외)에 두며 raw/manifest.json에 받은 시각과 sha256을 남긴다.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from axbuilder.ingest.chunking import ARTICLE_HEADER
from axbuilder.ingest.law_html import law_html_to_text
from axbuilder.models import DocMeta

SourceFormat = Literal["html", "hwpx", "pdf", "md"]
# 받은 원문의 저장 확장자. 법령·고시 HTML은 조문 줄 텍스트로 바꿔 .txt로 저장한다
SAVED_SUFFIX: dict[str, str] = {"html": ".txt", "hwpx": ".hwpx", "pdf": ".pdf", "md": ".md"}
# 저장소 파일(path)로 둘 수 있는 형식: load_document가 바로 읽을 수 있어야 한다
REPO_FILE_SUFFIX: dict[str, str] = {"md": ".md", "pdf": ".pdf", "hwpx": ".hwpx"}
# doc_id는 raw/ 안의 파일 이름이 되므로 경로 문자("/", "..")가 들어갈 수 없게 한다
DOC_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MANIFEST_NAME = "manifest.json"
USER_AGENT = "axbuilder-corpus/0.1 (+https://github.com/Growcompany/ax-assistant-builder)"


class CorpusSource(DocMeta):
    """sources.yaml 항목 = DocMeta 필드 + 원문 위치(url 또는 path) + 형식 + 라이선스 판단."""

    # 오타 필드("licence")가 조용히 무시되지 않도록
    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    path: str | None = None  # 저장소 루트 기준 상대 경로 (직접 쓴 가상 문서)
    format: SourceFormat
    license: str = Field(min_length=1)

    @field_validator("doc_id")
    @classmethod
    def _check_doc_id(cls, value: str) -> str:
        if len(value) > 64 or not DOC_ID.fullmatch(value):
            raise ValueError(f"doc_id {value!r} must be lowercase words joined by '-'")
        return value

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError(f"url must start with https://: {value!r}")
        return value

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        # 저장소 안의 상대 경로만: 절대 경로, 드라이브 문자, "..", 역슬래시(Windows 구분자) 금지
        parts = PurePosixPath(value).parts
        if value.startswith("/") or ":" in value or "\\" in value or ".." in parts:
            raise ValueError(f"path must be a relative path inside the repository: {value!r}")
        return value

    @model_validator(mode="after")
    def _check_location(self) -> "CorpusSource":
        if (self.url is None) == (self.path is None):
            raise ValueError(f"{self.doc_id}: set exactly one of url or path")
        if self.path is not None:
            expected = REPO_FILE_SUFFIX.get(self.format)
            if expected is None or PurePosixPath(self.path).suffix.lower() != expected:
                raise ValueError(f"{self.doc_id}: path {self.path!r} does not match {self.format}")
        return self

    def doc_meta(self) -> DocMeta:
        return DocMeta(**self.model_dump(include=set(DocMeta.model_fields)))


class ManifestEntry(BaseModel):
    doc_id: str
    url: str
    format: SourceFormat
    file: str  # raw/ 안의 파일 이름
    sha256: str  # 저장한 파일의 sha256 (색인에 들어가는 바로 그 파일)
    retrieved_at: datetime


@dataclass
class DownloadResult:
    downloaded: list[ManifestEntry] = field(default_factory=list)
    in_repo: list[str] = field(default_factory=list)  # path 항목: 저장소 파일을 그대로 쓴다
    failed: dict[str, str] = field(default_factory=dict)  # doc_id → 실패 이유


def load_sources(path: Path) -> list[CorpusSource]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: top level must be a list of sources")
    sources = [CorpusSource.model_validate(item) for item in data]
    seen: set[str] = set()
    for source in sources:
        if source.doc_id in seen:
            raise ValueError(f"{path}: duplicate doc_id {source.doc_id!r}")
        seen.add(source.doc_id)
    return sources


def source_file(source: CorpusSource, raw_dir: Path, repo_root: Path) -> Path:
    """load_document에 넘길 로컬 파일 경로.

    url 항목은 raw/{doc_id}.{확장자}, path 항목은 저장소 파일.
    """
    if source.path is None:
        return raw_dir / f"{source.doc_id}{SAVED_SUFFIX[source.format]}"
    path = (repo_root / source.path).resolve()
    if not path.is_relative_to(repo_root.resolve()):  # 심볼릭 링크로 저장소 밖을 가리키는 경우
        raise ValueError(f"{source.doc_id}: {source.path!r} resolves outside the repository")
    return path


def download_sources(
    sources: list[CorpusSource], raw_dir: Path, client: httpx.Client, repo_root: Path
) -> DownloadResult:
    """url 항목을 차례로 받아 저장한다. 한 문서가 실패해도 나머지는 계속 받는다."""
    result = DownloadResult()
    raw_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        try:
            if source.url is None:
                if not source_file(source, raw_dir, repo_root).is_file():
                    raise FileNotFoundError(f"missing repository file {source.path}")
                result.in_repo.append(source.doc_id)
                continue
            body = _fetch(source, client)
            target = source_file(source, raw_dir, repo_root)
            _write_atomic(target, body)
            result.downloaded.append(
                ManifestEntry(
                    doc_id=source.doc_id,
                    url=source.url,
                    format=source.format,
                    file=target.name,
                    sha256=hashlib.sha256(body).hexdigest(),
                    retrieved_at=datetime.now(UTC).replace(microsecond=0),
                )
            )
        except (httpx.HTTPError, ValueError, OSError) as e:
            result.failed[source.doc_id] = f"{type(e).__name__}: {e}"
    _write_manifest(raw_dir, sources, result)
    return result


def _fetch(source: CorpusSource, client: httpx.Client) -> bytes:
    assert source.url is not None
    response = client.get(source.url)
    response.raise_for_status()
    if source.format == "html":
        text = law_html_to_text(response.text)
        # 없는 일련번호에도 200과 안내 페이지가 올 수 있다. 조문이 없으면 받은 것으로 치지 않는다
        if len(ARTICLE_HEADER.findall(text)) < 2:
            raise ValueError("no article lines (제N조) in the page; wrong URL or layout changed")
        return text.encode("utf-8")
    body = response.content
    magic = {"hwpx": b"PK", "pdf": b"%PDF"}.get(source.format)
    if magic is not None and not body.startswith(magic):
        raise ValueError(f"response is not a {source.format} file (starts with {body[:8]!r})")
    return body


def _write_atomic(target: Path, body: bytes) -> None:
    # 받는 도중 끊겨도 반쯤 쓴 파일이 남지 않게 임시 파일에 쓴 뒤 이름을 바꾼다
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(body)
    os.replace(partial, target)


def _write_manifest(raw_dir: Path, sources: list[CorpusSource], result: DownloadResult) -> None:
    """이번에 받은 항목으로 갱신. 이번에 실패했어도 전에 받아 둔 파일이 있으면 그 기록은 남긴다."""
    previous = {entry.doc_id: entry for entry in _read_manifest(raw_dir / MANIFEST_NAME)}
    current = {entry.doc_id: entry for entry in result.downloaded}
    entries = []
    for source in sources:
        if source.url is None:
            continue
        entry = current.get(source.doc_id) or previous.get(source.doc_id)
        if entry is not None and (raw_dir / entry.file).is_file():
            entries.append(entry)
    payload = [entry.model_dump(mode="json") for entry in entries]
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _write_atomic(raw_dir / MANIFEST_NAME, text.encode("utf-8"))


def _read_manifest(path: Path) -> list[ManifestEntry]:
    try:
        return [ManifestEntry.model_validate(item) for item in json.loads(path.read_bytes())]
    except FileNotFoundError:
        return []
    except (ValueError, TypeError):  # 손상된 기록(JSON·검증 오류)은 버리고 새로 쓴다
        return []
