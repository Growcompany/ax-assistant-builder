"""원본 파일(.pdf, .hwpx, .md, .txt)을 페이지 목록이 있는 Document로 읽는다."""

import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pdfplumber

from axbuilder.models import DocMeta, Document, Page

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {".md", ".txt"}

# HWPX = ZIP 안의 XML. 본문은 Contents/section0.xml, section1.xml, ... 에 나뉘어 있다
HWPX_SECTION = re.compile(r"Contents/section(\d+)\.xml")
# 업로드 파일 대비 zip bomb 방지: 읽을 구역들의 압축 해제 크기 합계 상한.
# zipfile은 헤더에 적힌 크기(file_size)보다 많이 풀어 주지 않으므로 이 합계가 실제 상한이 된다
HWPX_MAX_UNCOMPRESSED = 50 * 1024 * 1024
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
# hp:t 안에서 글자 대신 쓰이는 공백류 요소 (탭, 전각 공백, 줄 안 줄바꿈 등)
_HP_SPACES = {f"{_HP}{name}" for name in ("tab", "fwSpace", "nbSpace", "lineBreak")}


class UnsupportedFileType(ValueError):
    """지원하지 않는 확장자 (API에서는 415로 바꾼다)."""


def load_document(path: Path, meta: DocMeta, workspace_id: str) -> Document:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = _read_pdf(path)
    elif suffix == ".hwpx":
        pages = [Page(number=1, text=_read_hwpx(path))]
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


def _read_hwpx(path: Path) -> str:
    """구역을 번호 순서로 읽어 hp:p(문단) 하나를 한 줄로 만든다. 쪽 정보가 없어 전체가 한 페이지."""
    try:
        with zipfile.ZipFile(path) as zf:
            sections = [
                (int(m.group(1)), info)
                for info in zf.infolist()
                if (m := HWPX_SECTION.fullmatch(info.filename))
            ]
            if not sections:
                raise ValueError(f"no Contents/section*.xml in {path.name}; not an HWPX file?")
            total = sum(info.file_size for _, info in sections)
            if total > HWPX_MAX_UNCOMPRESSED:
                raise ValueError(
                    f"{path.name}: sections expand to {total:,} bytes "
                    f"(limit {HWPX_MAX_UNCOMPRESSED // (1024 * 1024)}MB)"
                )
            lines: list[str] = []
            # 이름순이면 section10이 section2보다 앞에 온다 → 번호(숫자)로 정렬
            for _, info in sorted(sections, key=lambda pair: pair[0]):
                lines += _hwpx_paragraphs(ET.fromstring(zf.read(info)))
    except zipfile.BadZipFile as e:
        raise ValueError(f"{path.name} is not a valid HWPX (zip) file") from e
    except ET.ParseError as e:
        raise ValueError(f"{path.name}: broken section XML ({e})") from e
    return _normalize("\n".join(line for line in lines if line))


def _hwpx_paragraphs(root: ET.Element) -> list[str]:
    """hp:p마다 한 줄. hp:t는 가장 가까운 상위 hp:p에만 붙인다.

    표 셀 안의 문단은 바깥 문단 안에 중첩되어 있어, 바깥 문단의 모든 하위 글자를 모으면 셀 내용이
    두 번 들어간다. 재귀 대신 스택을 써서 깊게 중첩된 XML에서도 RecursionError가 나지 않게 한다.
    """
    paragraphs: list[list[str]] = []
    stack: list[tuple[ET.Element, list[str] | None]] = [(root, None)]
    while stack:
        elem, current = stack.pop()
        if elem.tag == f"{_HP}p":
            current = []
            paragraphs.append(current)
        elif elem.tag == f"{_HP}t":
            if current is not None:
                current.append(_hwpx_text(elem))
            continue
        stack.extend((child, current) for child in reversed(elem))  # 문서 순서대로 꺼내도록 역순
    return [" ".join("".join(parts).split()) for parts in paragraphs]


def _hwpx_text(t: ET.Element) -> str:
    parts = [t.text or ""]
    for child in t:
        if child.tag in _HP_SPACES:
            parts.append(" ")
        parts.append(child.tail or "")
    return "".join(parts)


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
