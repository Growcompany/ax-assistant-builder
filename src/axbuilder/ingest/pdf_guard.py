"""PDF 압축 폭탄 방어: 문서 하나에서 pdfminer가 풀어 낸 스트림 바이트 합계에 상한을 둔다.

pdfminer(pdfplumber가 쓰는 파서)는 FlateDecode 스트림을 zlib.decompress(data)로 한 번에 푼다.
deflate는 최대 약 1000배로 풀리고 필터를 겹치면 곱으로 커진다. 100KB PDF가 100MB, 1MB가 1GB로
풀렸다(PR #16 보안 리뷰에서 재현). pdfminer에는 상한 옵션이 없어서, pdftypes 모듈이 부르는 해제
함수(Flate, 깨진 Flate, LZW, RunLength)를 상한을 지키는 것으로 바꿔 끼운다.

- 상한은 decode_budget() 블록 안에서만, 스레드마다 따로 걸린다. 블록 밖에서는 원래 함수를
  그대로 부른다
- 상한을 넘으면 PDFDecodeLimitError(ValueError)를 던진다. 출력이 상한에 닿는 순간 멈추므로
  메모리는 상한 근처까지만 쓴다
- 풀어 낸 바이트만 묶으면 부족하다. pdfplumber는 글자 하나마다 객체(LTChar, 약 2KB)를 만들어,
  1.5KB PDF가 409KB로 풀린 뒤 글자 40만 개로 0.8GB·38초를 썼다(PR #16 2차 리뷰에서 재현).
  그래서 layout_budget()이 레이아웃 객체(글자·선·그림) 수를 쪽마다, 문서 전체로 묶는다.
  쪽을 다 읽으면 pdfplumber가 객체를 버리므로 메모리는 쪽 하나 분량까지만 쓰고, 문서 전체 상한은
  쪽이 아주 많은 문서의 파싱 시간을 묶는다
- 내용이 없는 쪽도 pdfplumber가 쪽 객체를 만들고 읽는 데 시간과 메모리를 쓴다. 빈 쪽 10만 개짜리
  8MB PDF가 30초·498MB를 썼다(v0.1.0 보안 점검에서 재현, 해제량 0·레이아웃 객체 0).
  check_page_count()가 쪽 객체를 만들기 전에 쪽 수를 센다
- 객체 수도 묶는다(object_budget). 쪽이 하나도 없으면 pdfminer는 xref가 선언한 모든 객체 번호를
  훑는데, 그 수는 파일에 적힌 숫자일 뿐이라 199바이트 PDF가 10^12개를 선언할 수 있다(PR #19
  보안 리뷰에서 재현: 40만 개에 12.6초, 10^12개는 끝나지 않음). check_object_count()가 훑기 전에
  선언된 수를 본다. 객체 스트림(ObjStm)은 선언한 객체(/N)까지만 파싱하고 그 수를 같은 예산에서
  뺀다(pdfminer는 /N과 무관하게 스트림 끝까지 파싱해 들고 있는다). 객체 "하나" 안에 든 배열·
  사전의 크기는 묶지 못한다
- 바꿔 끼울 pdfminer 이름이 없어지면 install()이 PDFGuardUnavailableError로 실패한다. 그냥
  대입하면 pdfminer가 이름을 바꿨을 때 오류 없이 상한이 꺼진다
- CCITTFax 같은 순수 파이썬 디코더의 CPU 시간은 막지 못한다. 이런 위험까지 막으려면 메모리·시간
  상한을 건 별도 프로세스에서 파싱해야 한다(M-3)
"""

import itertools
import logging
import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from typing import Any

from pdfminer import pdftypes
from pdfminer.converter import PDFLayoutAnalyzer
from pdfminer.lzw import LZWDecoder
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFStreamParser
from pdfminer.psparser import PSEOF

logger = logging.getLogger(__name__)
_state = threading.local()
_STEP = 1024 * 1024  # 한 번에 풀어 내는 양


class PDFLimitError(ValueError):
    """PDF 하나가 파싱 자원 상한을 넘었다 (폭탄으로 본다)."""


class PDFDecodeLimitError(PDFLimitError):
    """PDF 스트림을 풀어 낸 크기가 상한을 넘었다 (압축 폭탄으로 본다)."""


class PDFLayoutLimitError(PDFLimitError):
    """레이아웃 객체(글자·선·그림) 수가 쪽 또는 문서의 상한을 넘었다."""


class PDFPageLimitError(PDFLimitError):
    """쪽 수가 상한을 넘었다."""


class PDFObjectLimitError(PDFLimitError):
    """xref와 객체 스트림이 선언한 객체 수가 상한을 넘었다."""


class PDFGuardUnavailableError(RuntimeError):
    """바꿔 끼울 pdfminer 이름이 없다. 상한 없이 PDF를 읽지 않도록 import에서 멈춘다."""


def _require(owner: Any, name: str) -> Any:
    """owner가 직접 가진 속성 name. 없으면(pdfminer가 이름을 바꾸거나 옮겼으면) 실패한다."""
    try:
        return vars(owner)[name]
    except KeyError:
        raise PDFGuardUnavailableError(
            f"pdf_guard cannot limit PDF parsing: pdfminer {owner.__name__}.{name} is missing. "
            "This pdfminer.six version is not supported by axbuilder.ingest.pdf_guard; "
            "update pdf_guard (and its tests) before parsing untrusted PDFs"
        ) from None


def check_page_count(document: PDFDocument, max_pages: int) -> int:
    """쪽 수를 돌려준다. max_pages개를 넘으면 PDFPageLimitError. pdfplumber의 pdf.pages보다 먼저,
    check_object_count()보다 뒤에 부른다.

    카탈로그 /Pages의 /Count는 파일에 적힌 값이라 믿지 않고, pdfminer의 쪽 반복자를 상한+1개까지만
    돈다(쪽 사전만 읽는다. pdfplumber Page 객체와 레이아웃은 만들지 않는다). 쪽 트리에서 쪽이
    하나도 안 나오면 이 반복자는 xref의 모든 객체를 훑는다. 0을 받은 호출자는 pdf.pages를 부르지
    않아야 같은 훑기를 되풀이하지 않는다.
    """
    pages = itertools.islice(PDFPage.create_pages(document), max_pages + 1)
    count = sum(1 for _ in pages)
    if count > max_pages:
        raise PDFPageLimitError(f"the PDF has more than {max_pages:,} pages")
    return count


@contextmanager
def object_budget(max_objects: int) -> Iterator[None]:
    """이 블록 안(같은 스레드)에서 PDF 하나가 선언할 수 있는 객체 수를 max_objects로 제한한다.
    check_object_count()와 객체 스트림 파싱이 이 예산에서 뺀다."""
    previous = (getattr(_state, "objects_limit", None), getattr(_state, "objects_used", 0))
    _state.objects_limit, _state.objects_used = max_objects, 0
    try:
        yield
    finally:
        _state.objects_limit, _state.objects_used = previous


def _charge_objects(count: int) -> None:
    _state.objects_used += max(count, 0)
    if _state.objects_used > _state.objects_limit:
        raise PDFObjectLimitError(f"the PDF declares more than {_state.objects_limit:,} objects")


def check_object_count(document: PDFDocument) -> None:
    """xref들이 선언한 객체 수를 object_budget()에서 뺀다. 넘으면 PDFObjectLimitError.

    pdfplumber.open() 직후, 쪽을 세기 전에 한 번 부른다. xref 스트림은 /Index에 적힌 수만큼
    객체가 있다고 선언할 뿐 데이터가 없어도 되므로, 실제로 훑어 보기 전에 선언된 수로 거절한다.
    """
    if getattr(_state, "objects_limit", None) is None:
        raise RuntimeError("check_object_count() must be called inside object_budget()")
    for xref in document.xrefs:
        ranges = getattr(xref, "ranges", None)  # PDFXRefStream: [(시작 번호, 개수), ...]
        if ranges is not None:
            _charge_objects(sum(max(count, 0) for _, count in ranges))
        else:  # PDFXRef·PDFXRefFallback: 파일에서 실제로 읽은 항목
            _charge_objects(len(xref.get_objids()))


@contextmanager
def decode_budget(max_bytes: int) -> Iterator[None]:
    """이 블록 안(같은 스레드)에서 pdfminer가 풀어 내는 바이트 합계를 max_bytes로 제한한다."""
    previous = getattr(_state, "remaining", None)
    _state.remaining = max_bytes
    try:
        yield
    finally:
        _state.remaining = previous


_LAYOUT_FIELDS = ("page_limit", "page_used", "doc_limit", "doc_used")


@contextmanager
def layout_budget(max_per_page: int, max_per_document: int) -> Iterator[None]:
    """이 블록 안(같은 스레드)에서 pdfminer가 만드는 레이아웃 객체 수를 쪽마다, 합계로 제한한다."""
    previous = [getattr(_state, name, None) for name in _LAYOUT_FIELDS]
    _state.page_limit, _state.page_used = max_per_page, 0
    _state.doc_limit, _state.doc_used = max_per_document, 0
    try:
        yield
    finally:
        for name, value in zip(_LAYOUT_FIELDS, previous, strict=True):
            setattr(_state, name, value)


def find_limit_error(error: BaseException) -> PDFLimitError | None:
    """error나 그 원인 사슬에 있는 PDFLimitError. pdfplumber는 pdfminer 예외를
    PdfminerException으로 감싸 다시 던진다."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, PDFLimitError):
            return current
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _remaining() -> int | None:
    return getattr(_state, "remaining", None)


def _check(size: int, limit: int) -> None:
    if size > limit:
        raise PDFDecodeLimitError("PDF streams expand beyond the decode limit")


def _charge(size: int) -> None:
    _state.remaining -= size


def _drain(inflater: Any, data: bytes, parts: list[bytes], size: int, limit: int) -> int:
    """data를 _STEP씩 풀어 parts에 붙인다 → 지금까지 푼 합계. 합계가 limit을 넘는 순간 멈춘다.

    한 번에 상한까지 풀면 zlib이 출력 조각을 마지막에 이어 붙이느라 상한의 2배를 잠깐 쓴다.
    나눠 풀면 폭탄일 때 메모리는 상한 근처까지만 쓴다.
    """
    pending = data
    while True:
        piece = inflater.decompress(pending, _STEP)
        size += len(piece)
        _check(size, limit)
        parts.append(piece)
        pending = inflater.unconsumed_tail
        # 출력이 _STEP을 꽉 채웠으면 zlib 안에 더 나올 것이 남아 있을 수 있다
        if inflater.eof or (not pending and len(piece) < _STEP):
            return size


def _inflate(data: bytes, wbits: int = zlib.MAX_WBITS, bufsize: int = zlib.DEF_BUF_SIZE) -> bytes:
    """zlib.decompress와 같되(같은 경우에 zlib.error) 상한보다 많이 풀지 않는다."""
    limit = _remaining()
    if limit is None:
        return zlib.decompress(data, wbits, bufsize)
    inflater = zlib.decompressobj(wbits)
    parts: list[bytes] = []
    size = _drain(inflater, data, parts, 0, limit)
    if not inflater.eof:
        # zlib.decompress는 끝나지 않은 스트림에 오류를 낸다. pdfminer는 이때 깨진 스트림용
        # 해제(decompress_corrupted)로 다시 푼다 — 그쪽에서 센다
        raise zlib.error("Error -5 while decompressing data: incomplete or truncated stream")
    _charge(size)
    return b"".join(parts)


class _BoundedZlib:
    """pdftypes 안의 이름 zlib을 대신한다. decompress만 상한을 지키고 나머지는 zlib 그대로."""

    error = zlib.error
    decompress = staticmethod(_inflate)

    def __getattr__(self, name: str) -> Any:
        return getattr(zlib, name)


_original_corrupted = _require(pdftypes, "decompress_corrupted")
_original_rldecode = _require(pdftypes, "rldecode")
_original_lzwdecode = _require(pdftypes, "lzwdecode")


def _inflate_corrupted(data: bytes) -> bytes:
    """pdfminer decompress_corrupted와 같은 규칙(끝 3바이트에서 난 오류만 무시)에 상한을 더했다."""
    limit = _remaining()
    if limit is None:
        return _original_corrupted(data)
    inflater = zlib.decompressobj()
    parts: list[bytes] = []
    head, tail = data[:-3], data[-3:]
    size = _drain(inflater, head, parts, 0, limit)  # 여기서 난 오류는 pdfminer처럼 그대로 던진다
    for i in range(len(tail)):
        try:
            size = _drain(inflater, tail[i : i + 1], parts, size, limit)
        except zlib.error:
            # 체크섬 자리의 오류: 앞에서 푼 내용은 쓴다
            logger.warning("Data-loss while decompressing corrupted data")
            break
    _charge(size)
    return b"".join(parts)


def _lzwdecode(data: bytes) -> bytes:
    limit = _remaining()
    if limit is None:
        return _original_lzwdecode(data)
    parts: list[bytes] = []
    size = 0
    for piece in LZWDecoder(BytesIO(data)).run():  # pdfminer lzwdecode와 같은 디코더
        size += len(piece)
        _check(size, limit)
        parts.append(piece)
    _charge(size)
    return b"".join(parts)


def _runlength_size(data: bytes) -> int:
    """RunLength로 풀었을 때의 크기. 풀지 않고 길이 바이트만 따라가며 센다."""
    size = i = 0
    while i < len(data):
        length = data[i]
        if length == 128:  # EOD
            break
        if length < 128:  # 다음 length+1바이트를 그대로
            size += length + 1
            i += length + 2
        else:  # 다음 1바이트를 257-length번
            size += 257 - length
            i += 2
    return size


def _rldecode(data: bytes) -> bytes:
    limit = _remaining()
    if limit is None:
        return _original_rldecode(data)
    _check(_runlength_size(data), limit)
    out = _original_rldecode(data)
    _charge(len(out))
    return out


# ---- 객체 스트림 ----

_original_get_objects = _require(PDFDocument, "_get_objects")


def _get_objects(self: PDFDocument, stream: pdftypes.PDFStream) -> tuple[list[object], int]:
    """pdfminer _get_objects와 같되 선언한 객체까지만 파싱하고 그 수를 객체 예산에서 뺀다.

    스트림 내용은 번호·위치 쌍 N개(값 2N개) 뒤에 객체 N개다. pdfminer는 i번째 객체를
    objs[2N + i]로 꺼내므로 앞의 3N개만 있으면 된다. /N이 없거나 정수가 아니면 0으로 본다
    (pdfminer는 이때도 끝까지 파싱한다. 그런 스트림의 객체는 여기서는 찾지 못한다).
    """
    if getattr(_state, "objects_limit", None) is None:
        return _original_get_objects(self, stream)
    declared = max(pdftypes.int_value(stream.get("N", 0)), 0)
    _charge_objects(declared)
    parser = PDFStreamParser(stream.get_data())
    parser.set_document(self)
    objs: list[object] = []
    try:
        for _ in range(3 * declared):
            objs.append(parser.nextobject()[1])
    except PSEOF:
        pass
    return objs, declared


# ---- 레이아웃 객체 수 ----

_original_begin_page = _require(PDFLayoutAnalyzer, "begin_page")
_original_render_char = _require(PDFLayoutAnalyzer, "render_char")
_original_paint_path = _require(PDFLayoutAnalyzer, "paint_path")
_original_render_image = _require(PDFLayoutAnalyzer, "render_image")


def _count_layout_object() -> None:
    if getattr(_state, "page_limit", None) is None:
        return
    _state.page_used += 1
    _state.doc_used += 1
    if _state.page_used > _state.page_limit:
        raise PDFLayoutLimitError(f"a page has more than {_state.page_limit:,} layout objects")
    if _state.doc_used > _state.doc_limit:
        raise PDFLayoutLimitError(f"the PDF has more than {_state.doc_limit:,} layout objects")


def _begin_page(self: PDFLayoutAnalyzer, *args: Any, **kwargs: Any) -> None:
    _state.page_used = 0  # 쪽 상한은 쪽마다 다시 센다
    _original_begin_page(self, *args, **kwargs)


def _render_char(self: PDFLayoutAnalyzer, *args: Any, **kwargs: Any) -> float:
    _count_layout_object()
    return _original_render_char(self, *args, **kwargs)


def _paint_path(self: PDFLayoutAnalyzer, *args: Any, **kwargs: Any) -> None:
    # 하위 경로가 여러 개인 path는 pdfminer가 self.paint_path를 하위 경로마다 다시 불러 각각 센다
    _count_layout_object()
    _original_paint_path(self, *args, **kwargs)


def _render_image(self: PDFLayoutAnalyzer, *args: Any, **kwargs: Any) -> None:
    _count_layout_object()
    _original_render_image(self, *args, **kwargs)


def install() -> None:
    """pdftypes의 해제 함수와 PDFLayoutAnalyzer의 객체 생성 함수를 바꿔 끼운다.
    여러 번 불러도 한 번만 바뀐다. pdfplumber의 집계기는 super()로 이 함수들을 부른다.

    바꿀 이름이 하나라도 없으면 아무것도 바꾸지 않고 PDFGuardUnavailableError. pdftypes.zlib은
    pdftypes가 import한 모듈 이름이라, pdfminer가 import 방식을 바꾸면 대입해도 쓰이지 않는다.
    """
    replacements: list[tuple[Any, str, Any]] = [
        (pdftypes, "zlib", _BoundedZlib()),
        (pdftypes, "decompress_corrupted", _inflate_corrupted),
        (pdftypes, "lzwdecode", _lzwdecode),
        (pdftypes, "rldecode", _rldecode),
        (PDFDocument, "_get_objects", _get_objects),
        (PDFLayoutAnalyzer, "begin_page", _begin_page),
        (PDFLayoutAnalyzer, "render_char", _render_char),
        (PDFLayoutAnalyzer, "paint_path", _paint_path),
        (PDFLayoutAnalyzer, "render_image", _render_image),
    ]
    for owner, name, _ in replacements:
        _require(owner, name)
    for owner, name, replacement in replacements:
        setattr(owner, name, replacement)


install()
