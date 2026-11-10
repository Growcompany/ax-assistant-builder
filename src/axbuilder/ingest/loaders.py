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
    check_page_count,
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
# PDF 쪽 수의 상한 (쪽 수 폭탄 방지, pdf_guard). 내용이 없는 쪽도 쪽 객체를 만들고 읽는 데 자원을
# 쓴다: 빈 쪽 10만 개(8MB)에 30초·498MB를 쟀다. 상한에 딱 맞는 빈 쪽 5천 개는 1.1초·69MB.
# 금융 법령·약관·매뉴얼은 길어도 수백~천여 쪽이다
PDF_MAX_PAGES = 5_000
# HWPX 구역 XML의 요소 수 상한, 모든 구역 합계 (요소 수 폭탄 방지). 공정위 표준약관 HWPX 두 개가
# 1,115개(예금거래기본약관, 8천 자)와 1,394개(은행여신거래기본약관 가계용, 1만 2천 자)다: 글자
# 9자에 요소 하나꼴이라 청크 상한(약 7백만 자)에 닿는 문서도 80만 개쯤이다. 상한에 딱 맞춘
# 파일은 가장 나쁜 경우(한 글자짜리 문단 50만 개) 1.6초·95MB를 쟀다
HWPX_MAX_ELEMENTS = 1_000_000
_HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
_HP_P = f"{_HP}p"
_HP_T = f"{_HP}t"
# hp:t 안에서 글자 대신 쓰이는 공백류 요소 (탭, 전각 공백, 줄 안 줄바꿈 등)
_HP_SPACES = {f"{_HP}{name}" for name in ("tab", "fwSpace", "nbSpace", "lineBreak")}


class UnsupportedFileType(ValueError):
    """지원하지 않는 확장자 (API에서는 415로 바꾼다)."""


class DocumentTooLargeError(ValueError):
    """풀어 낸 내용이 상한을 넘는다 (압축 폭탄으로 본다. API에서는 422)."""


class _RejectDTD:
    """XMLParser의 target이 물려받는다. DTD(<!DOCTYPE>)가 나오면 파싱을 멈춘다.

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
        ):
            pdf = pdfplumber.open(path)
            try:
                check_page_count(pdf.doc, PDF_MAX_PAGES)
            except BaseException:
                # pdf.close()(with 블록의 끝)는 pdf.pages로 모든 쪽 객체를 만든 뒤 닫는다.
                # 쪽이 너무 많을 때는 파일만 닫는다
                pdf.stream.close()
                raise
            with pdf:
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


class _HwpxParagraphs(_RejectDTD):
    """XMLParser의 target. 요소 트리를 만들지 않고 hp:p마다 한 줄씩 글자만 모은다.

    ElementTree는 요소마다 객체를 남겨, 빈 <hp:p/>만 3백만 개 든 20MB 구역(파일 30KB)이
    8초·541MB를 썼다(v0.1.0 보안 점검에서 재현). 여기서는 글자가 있는 문단만 메모리에 남고,
    요소 수가 상한을 넘으면 그 자리에서 멈춘다. 구역마다 새 XMLParser에 같은 target을 넘겨
    문서 전체의 요소를 센다.

    - hp:t는 가장 가까운 상위 hp:p에만 붙인다. 표 셀 안의 문단은 바깥 문단 안에 중첩되어 있어,
      바깥 문단의 모든 하위 글자를 모으면 셀 내용이 두 번 들어간다
    - 줄 순서는 문단이 시작한 순서다(바깥 문단, 그 안의 셀 문단들, 다음 문단)
    - hp:t 안에서는 바로 아래의 글자만 쓴다. 공백류 요소(탭 등)는 공백 하나, 그 밖의 하위 요소
      안의 글자는 버린다. 문단 밖의 hp:t도 버린다
    """

    def __init__(self, source: str, max_elements: int) -> None:
        self._source = source
        self._max_elements = max_elements
        self._elements = 0
        self._paragraphs: list[list[str]] = []  # 시작한 순서. 글자 없이 끝난 문단은 바로 뺀다
        self._open: list[list[str]] = []  # 아직 닫히지 않은 문단 (안쪽이 뒤)
        self._text: list[str] = []  # 지금 읽는 hp:t의 글자 조각
        self._text_depth = 0  # hp:t 안에서의 깊이 (0 = 밖, 1 = hp:t 바로 아래)

    def start(self, tag: str, attrib: dict[str, str]) -> None:
        self._elements += 1
        if self._elements > self._max_elements:
            raise DocumentTooLargeError(
                f"{self._source}: sections have more than {self._max_elements:,} XML elements"
            )
        if self._text_depth:
            self._text_depth += 1
            if self._text_depth == 2 and tag in _HP_SPACES:
                self._text.append(" ")
        elif tag == _HP_P:
            parts: list[str] = []
            self._paragraphs.append(parts)
            self._open.append(parts)
        elif tag == _HP_T:
            self._text_depth = 1

    def end(self, tag: str) -> None:
        if self._text_depth:
            self._text_depth -= 1
            if not self._text_depth:
                if self._open:
                    self._open[-1].extend(self._text)
                self._text.clear()
        elif tag == _HP_P:
            parts = self._open.pop()
            # 빈 문단은 줄을 만들지 않는다. 뒤에 다른 문단이 없을 때(맨 끝)만 뺄 수 있다:
            # 안쪽 빈 문단들이 먼저 빠지므로, 글자가 없는 문단은 중첩돼 있어도 모두 빠진다
            if not parts and self._paragraphs[-1] is parts:
                self._paragraphs.pop()

    def data(self, text: str) -> None:
        if self._text_depth == 1:
            self._text.append(text)

    def close(self) -> None:
        return None

    def lines(self) -> list[str]:
        return [" ".join("".join(parts).split()) for parts in self._paragraphs]


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
            paragraphs = _HwpxParagraphs(path.name, HWPX_MAX_ELEMENTS)
            read = 0  # 지금까지 풀어 읽은 바이트 (모든 구역 합계)
            # 이름순이면 section10이 section2보다 앞에 온다 → 번호(숫자)로 정렬
            for _, info in sorted(sections, key=lambda pair: pair[0]):
                if info.compress_type not in HWPX_COMPRESSIONS:
                    raise ValueError(
                        f"{path.name}: {info.filename} uses unsupported compression "
                        f"{info.compress_type} (only stored or deflate)"
                    )
                parser = ET.XMLParser(target=paragraphs)
                with zf.open(info) as member:
                    while chunk := member.read(HWPX_READ_CHUNK):
                        read += len(chunk)
                        if read > HWPX_MAX_UNCOMPRESSED:
                            raise DocumentTooLargeError(
                                f"{path.name}: sections expand to more than "
                                f"{HWPX_MAX_UNCOMPRESSED // (1024 * 1024)}MB"
                            )
                        parser.feed(chunk)
                parser.close()
    except zipfile.BadZipFile as e:
        raise ValueError(f"{path.name} is not a valid HWPX (zip) file") from e
    except ET.ParseError as e:
        raise ValueError(f"{path.name}: broken section XML ({e})") from e
    return _normalize("\n".join(line for line in paragraphs.lines() if line))


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
