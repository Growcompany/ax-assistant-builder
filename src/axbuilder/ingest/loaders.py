"""원본 파일(.pdf, .hwpx, .md, .txt)을 페이지 목록이 있는 Document로 읽는다."""

import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pdfplumber

from axbuilder.ingest.pdf_guard import (
    PDFDecodeLimitError,
    decode_budget,
    find_limit_error,
    layout_budget,
)
from axbuilder.models import DocMeta, Document, Page

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {".md", ".txt"}

# HWPX = ZIP 안의 XML. 본문은 Contents/section0.xml, section1.xml, ... 에 나뉘어 있다
HWPX_SECTION = re.compile(r"Contents/section(\d+)\.xml")
# 업로드 파일 대비 zip bomb 방지: 구역들을 실제로 풀어 읽은 바이트 합계의 상한.
# 헤더에 적힌 크기(file_size)는 위조할 수 있고, zf.read()는 구역 전체를 한 번에 푼 뒤에야
# 그 크기로 자른다. 그래서 1MB씩 풀어 읽으며 센다
HWPX_MAX_UNCOMPRESSED = 50 * 1024 * 1024
HWPX_READ_CHUNK = 1024 * 1024
# zipfile은 deflate는 요청한 양까지만 풀지만 bzip2·lzma는 한 번에 풀 양의 상한이 없다.
# 한컴오피스는 구역을 deflate로 저장한다
HWPX_COMPRESSIONS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
# PDF 하나에서 pdfminer가 스트림을 풀어 낸 바이트 합계의 상한 (압축 폭탄 방지, pdf_guard).
# 텍스트 위주 4천 쪽 매뉴얼의 처음 150쪽이 약 3MB로 풀렸다. 실제 문서에는 넉넉하다
PDF_MAX_DECODED = 256 * 1024 * 1024
# PDF가 만드는 레이아웃 객체(글자·선·그림) 수의 상한 (글자 폭탄 방지, pdf_guard).
# 쪽당: 상한 직전(글자 9만 9천 개) 쪽 하나를 읽는 데 최대 약 194MB·2.2초를 쟀다. 빽빽한 텍스트
# 쪽도 글자 수천 개다. 문서 전체: 청크 상한(약 7백만 자)보다 조금 넉넉하게 두어, 쪽이 아주 많은
# 폭탄의 파싱 시간을 약 4분으로 묶는다(이 상한이 없으면 해제 상한 256MB까지 약 1.5시간)
PDF_MAX_LAYOUT_OBJECTS_PER_PAGE = 100_000
PDF_MAX_LAYOUT_OBJECTS_PER_DOCUMENT = 10_000_000
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
# hp:t 안에서 글자 대신 쓰이는 공백류 요소 (탭, 전각 공백, 줄 안 줄바꿈 등)
_HP_SPACES = {f"{_HP}{name}" for name in ("tab", "fwSpace", "nbSpace", "lineBreak")}


class UnsupportedFileType(ValueError):
    """지원하지 않는 확장자 (API에서는 415로 바꾼다)."""


class DocumentTooLargeError(ValueError):
    """풀어 낸 내용이 상한을 넘는다 (압축 폭탄으로 본다. API에서는 422)."""


class _RejectDTD(ET.TreeBuilder):
    """DTD(<!DOCTYPE>)가 나오면 파싱을 멈춘다.

    내부 엔티티(<!ENTITY a "...">를 정의하고 &a;를 반복)는 XML 바이트 상한을 우회해 수십~수백 배로
    풀린다. expat의 billion laughs 방어도 100배까지는 허용한다. 한컴오피스가 만든 구역 XML에는
    DTD가 없으므로 아예 받지 않는다. 엔티티를 정의하기 전, 문서 머리에서 멈춘다.
    """

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise ValueError("DTD (<!DOCTYPE>) is not allowed in HWPX section XML")


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
    try:
        with (
            decode_budget(PDF_MAX_DECODED),
            layout_budget(PDF_MAX_LAYOUT_OBJECTS_PER_PAGE, PDF_MAX_LAYOUT_OBJECTS_PER_DOCUMENT),
            pdfplumber.open(path) as pdf,
        ):
            for pdf_page in pdf.pages:
                text = pdf_page.extract_text() or ""
                pages.append(Page(number=pdf_page.page_number, text=_normalize(text)))
                # 페이지별 글자 캐시를 비워 수백 쪽 PDF에서도 메모리가 쌓이지 않게
                pdf_page.close()
    except Exception as e:
        limit = find_limit_error(e)
        if limit is None:
            raise
        if isinstance(limit, PDFDecodeLimitError):
            message = f"PDF streams expand to more than {PDF_MAX_DECODED // (1024 * 1024)}MB"
        else:
            message = str(limit)
        raise DocumentTooLargeError(f"{path.name}: {message}") from e
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
            lines: list[str] = []
            read = 0  # 지금까지 풀어 읽은 바이트 (모든 구역 합계)
            # 이름순이면 section10이 section2보다 앞에 온다 → 번호(숫자)로 정렬
            for _, info in sorted(sections, key=lambda pair: pair[0]):
                if info.compress_type not in HWPX_COMPRESSIONS:
                    raise ValueError(
                        f"{path.name}: {info.filename} uses unsupported compression "
                        f"{info.compress_type} (only stored or deflate)"
                    )
                parser = ET.XMLParser(target=_RejectDTD())
                with zf.open(info) as member:
                    while chunk := member.read(HWPX_READ_CHUNK):
                        read += len(chunk)
                        if read > HWPX_MAX_UNCOMPRESSED:
                            raise DocumentTooLargeError(
                                f"{path.name}: sections expand to more than "
                                f"{HWPX_MAX_UNCOMPRESSED // (1024 * 1024)}MB"
                            )
                        parser.feed(chunk)
                lines += _hwpx_paragraphs(parser.close())
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
