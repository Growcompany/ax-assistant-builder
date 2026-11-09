"""PDF 압축 폭탄 방어 (PR #16 보안 리뷰): 스트림을 풀어 낸 바이트 합계에 상한을 둔다.

바이너리 PDF를 커밋하지 않으려고 테스트 안에서 만든다. 글꼴은 내장하지 않는 표준 Helvetica.
"""

import io
import tracemalloc
import zlib

import pytest
from pdfminer import pdftypes

import axbuilder.ingest.loaders as loaders
from axbuilder.ingest import pdf_guard
from axbuilder.ingest.loaders import DocumentTooLargeError, load_document
from axbuilder.models import DocMeta

MB = 1024 * 1024
meta = DocMeta(doc_id="doc-1", title="문서", source="tests", doc_type="other")


def make_pdf(content: bytes, filters: tuple[str, ...] = ()) -> bytes:
    """내용 스트림 하나짜리 1쪽 PDF. filters 순서대로 압축을 푼다(압축은 거꾸로 건다)."""
    stream = content
    for name in reversed(filters):
        assert name == "FlateDecode"
        stream = zlib.compress(stream, 9)
    filter_entry = b""
    if filters:
        filter_entry = b" /Filter [%s]" % b" ".join(b"/" + f.encode() for f in filters)
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        4: b"<< /Length %d%s >>\nstream\n%s\nendstream" % (len(stream), filter_entry, stream),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(b"%d 0 obj\n%s\nendobj\n" % (num, objs[num]))
    xref_at = out.tell()
    out.write(b"xref\n0 6\n0000000000 65535 f \n")
    out.write(b"".join(b"%010d 00000 n \n" % offsets[num] for num in range(1, 6)))
    out.write(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref_at)
    return out.getvalue()


TEXT_OPS = b"BT /F1 12 Tf 72 720 Td (Hello PDF) Tj ET\n"


def test_small_flate_pdf_still_parses(tmp_path, monkeypatch):
    monkeypatch.setattr(loaders, "PDF_MAX_DECODED", MB)
    path = tmp_path / "ok.pdf"
    path.write_bytes(make_pdf(TEXT_OPS + b" " * 1000, ("FlateDecode",)))
    assert load_document(path, meta, "bank").pages[0].text == "Hello PDF"


@pytest.mark.parametrize("filters", [("FlateDecode",), ("FlateDecode", "FlateDecode")])
def test_flate_bomb_stops_at_limit(tmp_path, monkeypatch, filters):
    # 공백 32MB는 deflate로 수십 KB(두 번 겹치면 더 작다). 상한 1MB에 닿는 순간 멈춰야 한다
    monkeypatch.setattr(loaders, "PDF_MAX_DECODED", MB)
    path = tmp_path / "bomb.pdf"
    path.write_bytes(make_pdf(TEXT_OPS + b" " * (32 * MB), filters))
    assert path.stat().st_size < 64 * 1024

    tracemalloc.start()
    try:
        with pytest.raises(DocumentTooLargeError, match="1MB"):
            load_document(path, meta, "bank")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * MB, f"peak {peak:,} bytes"


def test_budget_is_scoped_to_the_block():
    data = zlib.compress(b"x" * 100)
    with pdf_guard.decode_budget(10):
        with pytest.raises(pdf_guard.PDFDecodeLimitError):
            pdftypes.zlib.decompress(data)
    assert pdftypes.zlib.decompress(data) == b"x" * 100  # 블록 밖: 원래 zlib.decompress


def test_budget_counts_all_streams_of_a_document():
    data = zlib.compress(b"x" * 600)
    with pdf_guard.decode_budget(1000):
        assert pdftypes.zlib.decompress(data) == b"x" * 600
        with pytest.raises(pdf_guard.PDFDecodeLimitError):
            pdftypes.zlib.decompress(data)  # 600 + 600 > 1000


@pytest.mark.parametrize("size", [0, 1, MB - 1, MB, MB + 1, 3 * MB + 5])
def test_bounded_inflate_matches_zlib(size):
    # 1MB씩 나눠 풀어도 결과는 zlib.decompress와 같다 (경계 크기 포함)
    raw = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
    with pdf_guard.decode_budget(4 * MB):
        assert pdftypes.zlib.decompress(zlib.compress(raw)) == raw


def test_bounded_inflate_matches_zlib_errors():
    good = zlib.compress(b"hello" * 100)
    corrupt = good[:-1] + bytes([good[-1] ^ 1])  # 끝의 Adler-32 체크섬이 틀림
    with pdf_guard.decode_budget(MB):
        for bad in (good[:-6], corrupt):  # 잘린 스트림, 체크섬 오류: zlib.decompress도 오류
            with pytest.raises(zlib.error):
                zlib.decompress(bad)
            with pytest.raises(zlib.error):
                pdftypes.zlib.decompress(bad)


@pytest.mark.parametrize("cut", [0, 2, 6, 40])
def test_bounded_corrupted_inflate_matches_pdfminer(cut):
    # pdfminer의 decompress_corrupted와 같은 결과(끝 3바이트에서 난 오류만 무시)여야 한다
    good = zlib.compress(bytes(range(256)) * 20)
    variants = [good[: len(good) - cut], good[:-1] + bytes([good[-1] ^ 1]), good[:20] + b"\0" * 9]
    for data in variants:
        try:
            expected: bytes | type[zlib.error] = pdf_guard._original_corrupted(data)
        except zlib.error:
            expected = zlib.error
        with pdf_guard.decode_budget(MB):
            if expected is zlib.error:
                with pytest.raises(zlib.error):
                    pdftypes.decompress_corrupted(data)
            else:
                assert pdftypes.decompress_corrupted(data) == expected


def test_runlength_checks_size_before_decoding():
    run = bytes([129, 0x20])  # 공백 128개
    data = run * 10 + b"\x80"
    with pdf_guard.decode_budget(10 * 128):
        assert pdftypes.rldecode(data) == b" " * 1280
    with pdf_guard.decode_budget(10 * 128 - 1), pytest.raises(pdf_guard.PDFDecodeLimitError):
        pdftypes.rldecode(data)
    literal = bytes([2]) + b"abc" + run + b"\x80"
    assert pdf_guard._runlength_size(literal) == 3 + 128


def lzw_literals(data: bytes) -> bytes:
    """글자마다 9비트 코드 하나인 LZW 스트림. 코드 폭이 바뀌기 전에 clear(256)를 넣는다."""
    codes: list[int] = []
    for start in range(0, len(data), 250):
        codes.append(256)
        codes.extend(data[start : start + 250])
    codes.append(257)  # EOD
    bits = "".join(f"{code:09b}" for code in codes)
    bits += "0" * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, "big")


def test_lzw_stops_at_limit():
    data = lzw_literals(b"abc" * 400)
    with pdf_guard.decode_budget(MB):
        assert pdftypes.lzwdecode(data) == b"abc" * 400
    with pdf_guard.decode_budget(1000), pytest.raises(pdf_guard.PDFDecodeLimitError):
        pdftypes.lzwdecode(data)
