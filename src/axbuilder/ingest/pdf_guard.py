"""PDF 압축 폭탄 방어: 문서 하나에서 pdfminer가 풀어 낸 스트림 바이트 합계에 상한을 둔다.

pdfminer(pdfplumber가 쓰는 파서)는 FlateDecode 스트림을 zlib.decompress(data)로 한 번에 푼다.
deflate는 최대 약 1000배로 풀리고 필터를 겹치면 곱으로 커진다. 100KB PDF가 100MB, 1MB가 1GB로
풀렸다(PR #16 보안 리뷰에서 재현). pdfminer에는 상한 옵션이 없어서, pdftypes 모듈이 부르는 해제
함수(Flate, 깨진 Flate, LZW, RunLength)를 상한을 지키는 것으로 바꿔 끼운다.

- 상한은 decode_budget() 블록 안에서만, 스레드마다 따로 걸린다. 블록 밖에서는 원래 함수를
  그대로 부른다
- 상한을 넘으면 PDFDecodeLimitError(ValueError)를 던진다. 출력이 상한에 닿는 순간 멈추므로
  메모리는 상한 근처까지만 쓴다
- CCITTFax 같은 순수 파이썬 디코더의 CPU 시간, 풀린 내용으로 만드는 객체 수는 막지 못한다.
  이런 위험까지 막으려면 메모리·시간 상한을 건 별도 프로세스에서 파싱해야 한다(M-3)
"""

import logging
import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from typing import Any

from pdfminer import pdftypes
from pdfminer.lzw import LZWDecoder

logger = logging.getLogger(__name__)
_state = threading.local()
_STEP = 1024 * 1024  # 한 번에 풀어 내는 양


class PDFDecodeLimitError(ValueError):
    """PDF 스트림을 풀어 낸 크기가 상한을 넘었다 (압축 폭탄으로 본다)."""


@contextmanager
def decode_budget(max_bytes: int) -> Iterator[None]:
    """이 블록 안(같은 스레드)에서 pdfminer가 풀어 내는 바이트 합계를 max_bytes로 제한한다."""
    previous = getattr(_state, "remaining", None)
    _state.remaining = max_bytes
    try:
        yield
    finally:
        _state.remaining = previous


def hit_decode_limit(error: BaseException) -> bool:
    """error나 그 원인 사슬에 PDFDecodeLimitError가 있나. pdfplumber는 pdfminer 예외를
    PdfminerException으로 감싸 다시 던진다."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, PDFDecodeLimitError):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False


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


_original_corrupted = pdftypes.decompress_corrupted
_original_rldecode = pdftypes.rldecode
_original_lzwdecode = pdftypes.lzwdecode


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


def install() -> None:
    """pdftypes의 해제 함수를 바꿔 끼운다. 여러 번 불러도 한 번만 바뀐다."""
    pdftypes.zlib = _BoundedZlib()  # type: ignore[assignment]
    pdftypes.decompress_corrupted = _inflate_corrupted
    pdftypes.lzwdecode = _lzwdecode
    pdftypes.rldecode = _rldecode


install()
