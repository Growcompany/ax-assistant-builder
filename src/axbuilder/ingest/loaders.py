"""원본 파일(.pdf, .md, .txt)을 페이지 목록이 있는 Document로 읽는다."""

import logging
import unicodedata
from pathlib import Path

import pdfplumber

from axbuilder.models import DocMeta, Document, Page

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {".md", ".txt"}


class UnsupportedFileType(ValueError):
    """지원하지 않는 확장자 (API에서는 415로 바꾼다)."""


def load_document(path: Path, meta: DocMeta, workspace_id: str) -> Document:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = _read_pdf(path)
    elif suffix in TEXT_SUFFIXES:
        pages = [Page(number=1, text=_read_text(path))]
    else:
        raise UnsupportedFileType(f"unsupported file type {suffix or '(none)'}: {path.name}")

    if not any(page.text.strip() for page in pages):
        # 스캔본 PDF는 글자가 이미지라 텍스트 층이 없다. OCR은 다음 마일스톤(학습 노트)
        logger.warning("no extractable text in %s (scanned PDF?); it will yield no chunks", path)
    return Document(meta=meta, workspace_id=workspace_id, pages=pages)


def _read_pdf(path: Path) -> list[Page]:
    pages = []
    with pdfplumber.open(path) as pdf:
        for pdf_page in pdf.pages:
            text = pdf_page.extract_text() or ""
            pages.append(Page(number=pdf_page.page_number, text=_normalize(text)))
            pdf_page.close()  # 페이지별 글자 캐시를 비워 수백 쪽 PDF에서도 메모리가 쌓이지 않게
    return pages


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        # 공공기관에서 받은 한글 텍스트 파일은 CP949(EUC-KR 확장)인 경우가 많다
        text = path.read_text(encoding="cp949")
    return _normalize(text)


def _normalize(text: str) -> str:
    # 자모가 풀어진(NFD) 한글을 합친다. NFKC는 항 번호 "①"을 "1"로 바꿔 버려서 쓰지 않는다
    return unicodedata.normalize("NFC", text)
