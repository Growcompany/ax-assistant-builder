"""PDF 압축 폭탄 방어 (PR #16 보안 리뷰): 스트림을 풀어 낸 바이트 합계에 상한을 둔다.

바이너리 PDF를 커밋하지 않으려고 테스트 안에서 만든다. 글꼴은 내장하지 않는 표준 Helvetica.
"""

import io
import struct
import tracemalloc
import zlib

import pdfplumber
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
    return write_pdf(objs)


def write_pdf(objs: dict[int, bytes]) -> bytes:
    """번호가 1부터 빈틈없이 이어지는 객체들로 PDF 파일(xref 포함)을 만든다."""
    size = len(objs) + 1
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(b"%d 0 obj\n%s\nendobj\n" % (num, objs[num]))
    xref_at = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % size)
    out.write(b"".join(b"%010d 00000 n \n" % offsets[num] for num in range(1, size)))
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_at))
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


# ---- 글자 폭탄 (PR #16 2차 리뷰): 풀린 내용은 작아도 글자 객체가 메모리를 쓴다 ----


def glyph_ops(count: int) -> bytes:
    return b"BT /F1 1 Tf 72 720 Td (" + b"A" * count + b") Tj ET\n"


def test_glyph_bomb_stops_at_page_object_limit(tmp_path, monkeypatch):
    # 1.5KB PDF가 글자 40만 개(0.8GB, 38초)가 됐다. 쪽당 객체 상한에 닿는 순간 멈춰야 한다
    monkeypatch.setattr(loaders, "PDF_MAX_LAYOUT_OBJECTS_PER_PAGE", 1000)
    path = tmp_path / "glyphs.pdf"
    path.write_bytes(make_pdf(glyph_ops(400_000), ("FlateDecode",)))
    assert path.stat().st_size < 2 * 1024

    tracemalloc.start()
    try:
        with pytest.raises(DocumentTooLargeError, match="1,000 layout objects"):
            load_document(path, meta, "bank")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 16 * MB, f"peak {peak:,} bytes"


def test_path_objects_count_toward_page_limit(tmp_path, monkeypatch):
    # 선(path)도 객체를 만든다. 하위 경로가 여러 개인 path는 하위 경로마다 센다
    monkeypatch.setattr(loaders, "PDF_MAX_LAYOUT_OBJECTS_PER_PAGE", 100)
    path = tmp_path / "paths.pdf"
    path.write_bytes(make_pdf(b"0 0 m 1 1 l " * 101 + b"S\n", ("FlateDecode",)))
    with pytest.raises(DocumentTooLargeError, match="100 layout objects"):
        load_document(path, meta, "bank")


def test_page_at_object_limit_still_parses(tmp_path, monkeypatch):
    monkeypatch.setattr(loaders, "PDF_MAX_LAYOUT_OBJECTS_PER_PAGE", 1000)
    path = tmp_path / "ok.pdf"
    path.write_bytes(make_pdf(glyph_ops(1000)))
    assert load_document(path, meta, "bank").pages[0].text == "A" * 1000


def test_layout_budget_is_scoped_to_the_block(tmp_path):
    path = tmp_path / "ok.pdf"
    path.write_bytes(make_pdf(glyph_ops(50)))
    with pdf_guard.layout_budget(10, 1000), pdfplumber.open(path) as pdf:
        with pytest.raises(Exception) as excinfo:
            pdf.pages[0].extract_text()
        assert isinstance(pdf_guard.find_limit_error(excinfo.value), pdf_guard.PDFLayoutLimitError)
    with pdfplumber.open(path) as pdf:  # 블록 밖: 상한 없음
        assert pdf.pages[0].extract_text() == "A" * 50


def make_multipage_pdf(page_contents: list[bytes]) -> bytes:
    """쪽마다 내용 스트림 하나(압축 없음). 객체 번호: 쪽 4, 6, ..., 그 내용 5, 7, ..."""
    page_ids = [4 + 2 * i for i in range(len(page_contents))]
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids)),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for pid, content in zip(page_ids, page_contents, strict=True):
        objs[pid] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (pid + 1)
        )
        objs[pid + 1] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)
    return write_pdf(objs)


def test_page_limit_resets_per_page_but_document_limit_does_not(tmp_path, monkeypatch):
    # 쪽마다 60개: 쪽 상한 100은 넘지 않는다. 3쪽 합계 180 > 문서 상한 150
    monkeypatch.setattr(loaders, "PDF_MAX_LAYOUT_OBJECTS_PER_PAGE", 100)
    monkeypatch.setattr(loaders, "PDF_MAX_LAYOUT_OBJECTS_PER_DOCUMENT", 150)
    path = tmp_path / "pages.pdf"
    path.write_bytes(make_multipage_pdf([glyph_ops(60)] * 2))
    assert [p.text for p in load_document(path, meta, "bank").pages] == ["A" * 60] * 2
    path.write_bytes(make_multipage_pdf([glyph_ops(60)] * 3))
    with pytest.raises(DocumentTooLargeError, match="the PDF has more than 150 layout objects"):
        load_document(path, meta, "bank")


# ---- 쪽 수 폭탄 (v0.1.0 보안 점검): 내용 없는 쪽 10만 개(8MB)가 30초·498MB를 썼다 ----


def empty_pages_pdf(pages: int, declared_count: int | None = None) -> bytes:
    """내용 없는 쪽만 있는 PDF. MediaBox는 쪽 트리에서 물려받는다. /Count는 속일 수 있다."""
    kids = b" ".join(b"%d 0 R" % (3 + i) for i in range(pages))
    count = pages if declared_count is None else declared_count
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /MediaBox [0 0 612 792] /Kids [%s] /Count %d >>" % (kids, count),
    }
    for i in range(pages):
        objs[3 + i] = b"<< /Type /Page /Parent 2 0 R >>"
    return write_pdf(objs)


def test_default_page_limit():
    assert loaders.PDF_MAX_PAGES == 5_000


def test_pdf_at_page_limit_still_parses(tmp_path, monkeypatch):
    monkeypatch.setattr(loaders, "PDF_MAX_PAGES", 3)
    path = tmp_path / "ok.pdf"
    path.write_bytes(make_multipage_pdf([glyph_ops(5)] * 3))
    assert [p.text for p in load_document(path, meta, "bank").pages] == ["A" * 5] * 3


@pytest.mark.parametrize("declared_count", [None, 1])
def test_too_many_pages_rejected_before_page_objects_are_built(
    tmp_path, monkeypatch, declared_count
):
    # 카탈로그의 /Count는 공격자가 적는 값이라 믿지 않는다(1이라고 적어도 실제 쪽을 센다).
    # pdfplumber의 Page 객체는 하나도 만들지 않고 거절해야 한다
    monkeypatch.setattr(loaders, "PDF_MAX_PAGES", 3)
    built = []
    original_page = pdfplumber.pdf.Page

    def counting_page(*args, **kwargs):
        built.append(1)
        return original_page(*args, **kwargs)

    monkeypatch.setattr(pdfplumber.pdf, "Page", counting_page)
    path = tmp_path / "pages.pdf"
    path.write_bytes(empty_pages_pdf(4, declared_count))
    with pytest.raises(DocumentTooLargeError, match="more than 3 pages"):
        load_document(path, meta, "bank")
    assert built == []


def test_page_count_stops_iterating_just_past_the_limit(tmp_path):
    # 쪽이 아무리 많아도 상한+1개까지만 돈다 (쪽 트리 전체를 걷지 않는다)
    path = tmp_path / "pages.pdf"
    path.write_bytes(empty_pages_pdf(50))
    with pdfplumber.open(path) as pdf:
        created = []
        original = pdf_guard.PDFPage.create_pages

        def spying(document):
            for page in original(document):
                created.append(page)
                yield page

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(pdf_guard.PDFPage, "create_pages", spying)
            with pytest.raises(pdf_guard.PDFPageLimitError):
                pdf_guard.check_page_count(pdf.doc, 10)
            assert len(created) == 11
            created.clear()
            pdf_guard.check_page_count(pdf.doc, 50)  # 상한과 같으면 통과
            assert len(created) == 50


# ---- 보호가 조용히 꺼지지 않게: 바꿔 끼울 pdfminer 속성이 없으면 install()이 실패한다 ----


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        (pdftypes, "zlib"),
        (pdftypes, "decompress_corrupted"),
        (pdftypes, "lzwdecode"),
        (pdftypes, "rldecode"),
        (pdf_guard.PDFDocument, "_get_objects"),
        (pdf_guard.PDFLayoutAnalyzer, "begin_page"),
        (pdf_guard.PDFLayoutAnalyzer, "render_char"),
        (pdf_guard.PDFLayoutAnalyzer, "paint_path"),
        (pdf_guard.PDFLayoutAnalyzer, "render_image"),
    ],
)
def test_install_fails_loudly_when_pdfminer_attribute_is_missing(monkeypatch, owner, name):
    # pdfminer가 이름을 바꾸면 그냥 대입하는 것으로는 아무 일도 없이 상한이 꺼진다
    monkeypatch.delattr(owner, name)
    with pytest.raises(pdf_guard.PDFGuardUnavailableError, match=name):
        pdf_guard.install()
    assert name not in vars(owner)  # 실패하면 아무것도 바꿔 끼우지 않는다


def test_install_is_idempotent_and_keeps_guards():
    pdf_guard.install()
    assert isinstance(pdftypes.zlib, pdf_guard._BoundedZlib)
    assert pdftypes.rldecode is pdf_guard._rldecode
    assert pdf_guard.PDFLayoutAnalyzer.render_char is pdf_guard._render_char


# ---- 객체 수 (PR #19 보안 리뷰): xref가 선언한 객체 수, 객체 스트림(ObjStm) ----


def xref_stream_pdf(
    objs: dict[int, bytes], entries: list[tuple[int, int, int] | None], size: int, root: int
) -> bytes:
    """xref 스트림(PDF 1.5)을 쓰는 PDF. entries의 None은 그 번호 객체의 파일 위치로 채운다.

    xref 스트림 자신은 objs 다음 번호를 쓴다. /Index는 [0 size]: size가 entries보다 크면 데이터
    없이 객체 수만 선언한 것이 된다.
    """
    out = io.BytesIO()
    out.write(b"%PDF-1.5\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(b"%d 0 obj\n%s\nendobj\n" % (num, objs[num]))
    xref_id = max(objs) + 1
    xref_at = out.tell()
    data = b""
    for num, entry in enumerate(entries):
        if entry is None:
            entry = (1, xref_at if num == xref_id else offsets[num], 0)
        data += struct.pack(">BIB", *entry)
    out.write(
        b"%d 0 obj\n<< /Type /XRef /W [1 4 1] /Size %d /Index [0 %d] /Root %d 0 R /Length %d >>\n"
        b"stream\n%s\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n"
        % (xref_id, size, size, root, len(data), data, xref_at)
    )
    return out.getvalue()


def xref_scan_pdf(declared: int) -> bytes:
    """카탈로그에 /Pages가 없고 xref가 객체 declared개를 선언한다(데이터는 3개뿐, 200바이트 안팎).

    쪽이 하나도 안 나오면 pdfminer는 xref의 모든 객체 번호를 훑어 /Type /Page를 찾는다.
    """
    return xref_stream_pdf({1: b"<< /Type /Catalog >>"}, [(0, 0, 0), None, None], declared, 1)


def objstm_pdf(count: int, trailing: bytes = b"", second: bytes = b"null") -> bytes:
    """카탈로그(객체 2)가 객체 스트림(객체 1) 안에 있는 1쪽 PDF.

    count는 스트림이 선언하는 객체 수(/N). count가 2 이상이면 둘째 객체(객체 6)는 second이고
    나머지는 선언만 한다. trailing은 선언한 객체들 뒤에 붙이는 내용이다.
    """
    catalog = b"<< /Type /Catalog /Pages 3 0 R >> "
    pairs = b"2 0 " + (b"6 %d " % len(catalog) if count >= 2 else b"")
    raw = pairs + catalog + (second + b" " if count >= 2 else b"") + trailing
    packed = zlib.compress(raw, 9)
    objs = {
        1: b"<< /Type /ObjStm /N %d /First %d /Filter /FlateDecode /Length %d >>\nstream\n%s\n"
        b"endstream" % (count, len(pairs), len(packed), packed),
        3: b"<< /Type /Pages /Count 1 /Kids [4 0 R] >>",
        4: b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 10 10] >>",
    }
    second_entry = (2, 1, 1) if count >= 2 else (0, 0, 0)
    entries = [(0, 0, 0), None, (2, 1, 0), None, None, None, second_entry]  # 5번은 xref 스트림
    return xref_stream_pdf(objs, entries, 7, 2)


def test_default_object_limit():
    assert loaders.PDF_MAX_OBJECTS == 1_000_000


def test_xref_declaring_too_many_objects_is_rejected_without_scanning(tmp_path, monkeypatch):
    # 199바이트 PDF가 객체 10^12개를 선언하면 쪽 세기가 끝나지 않았다(객체 하나에 약 35µs).
    # 선언한 수가 상한을 넘으면 훑기 전에 거절한다
    monkeypatch.setattr(loaders, "PDF_MAX_OBJECTS", 1000)
    scanned = []
    original = pdf_guard.PDFPage.create_pages
    monkeypatch.setattr(
        pdf_guard.PDFPage, "create_pages", lambda doc: scanned.append(1) or original(doc)
    )
    path = tmp_path / "xref.pdf"
    path.write_bytes(xref_scan_pdf(10**12))
    assert path.stat().st_size < 256
    with pytest.raises(DocumentTooLargeError, match="more than 1,000 objects"):
        load_document(path, meta, "bank")
    assert scanned == []
    path.write_bytes(xref_scan_pdf(1001))
    with pytest.raises(DocumentTooLargeError, match="more than 1,000 objects"):
        load_document(path, meta, "bank")
    assert scanned == []


def test_xref_at_object_limit_is_scanned_only_once(tmp_path, monkeypatch):
    # 상한에 딱 맞으면 통과한다. 쪽이 0개면 pdf.pages(같은 훑기를 다시 한다)를 부르지 않는다
    monkeypatch.setattr(loaders, "PDF_MAX_OBJECTS", 1000)
    scans = []
    original = pdf_guard.PDFPage.create_pages
    monkeypatch.setattr(
        pdf_guard.PDFPage, "create_pages", lambda doc: scans.append(1) or original(doc)
    )
    path = tmp_path / "xref.pdf"
    path.write_bytes(xref_scan_pdf(1000))
    assert load_document(path, meta, "bank").pages == []
    assert scans == [1]


def test_classic_xref_tables_count_toward_object_limit(tmp_path, monkeypatch):
    path = tmp_path / "pages.pdf"
    path.write_bytes(make_multipage_pdf([glyph_ops(5)] * 3))  # 객체 9개 + 빈 0번 항목
    monkeypatch.setattr(loaders, "PDF_MAX_OBJECTS", 9)
    assert len(load_document(path, meta, "bank").pages) == 3
    monkeypatch.setattr(loaders, "PDF_MAX_OBJECTS", 8)
    with pytest.raises(DocumentTooLargeError, match="more than 8 objects"):
        load_document(path, meta, "bank")


def test_object_stream_document_still_parses(tmp_path):
    path = tmp_path / "objstm.pdf"
    path.write_bytes(objstm_pdf(2, second=b"<< /Kind (second) >>"))
    assert [p.text for p in load_document(path, meta, "bank").pages] == [""]
    with pdf_guard.object_budget(100), pdfplumber.open(path) as pdf:
        assert pdf.doc.getobj(6) == {"Kind": b"second"}
        everything, declared = pdf_guard._original_get_objects(pdf.doc, pdf.doc.getobj(1))
        # 선언한 객체 뒤에 아무것도 없으면 pdfminer 원래 결과와 같다 (PDFObjRef는 ==가 없어 repr로)
        assert repr(pdf.doc._parsed_objs[1]) == repr((everything, declared))
        assert len(everything) == 6 and declared == 2


def test_object_stream_parsing_stops_at_declared_objects(tmp_path):
    # pdfminer는 객체 스트림을 /N과 무관하게 끝까지 파싱해 모두 들고 있는다. 선언한 객체 뒤에
    # "[]"를 채운 8.5KB PDF가 여는 단계에서 23.6초·343MB를 썼다. 쓰이는 것은 앞의 3N개
    # (번호·위치 쌍 2N + 객체 N)뿐이다
    path = tmp_path / "objstm.pdf"
    path.write_bytes(objstm_pdf(1, trailing=b"[]" * 50_000))
    assert path.stat().st_size < 2048
    with pdf_guard.object_budget(100), pdfplumber.open(path) as pdf:
        parsed, declared = pdf.doc._parsed_objs[1]
        assert declared == 1 and len(parsed) == 3
        assert len(pdf.pages) == 1
    with pdfplumber.open(path) as pdf:  # 블록 밖: pdfminer 원래 동작
        assert len(pdf.doc._parsed_objs[1][0]) == 50_003


def test_object_stream_declared_objects_count_toward_object_limit(tmp_path, monkeypatch):
    path = tmp_path / "objstm.pdf"
    path.write_bytes(objstm_pdf(2))
    monkeypatch.setattr(loaders, "PDF_MAX_OBJECTS", 9)  # xref 7개 + 스트림이 선언한 2개
    assert len(load_document(path, meta, "bank").pages) == 1
    monkeypatch.setattr(loaders, "PDF_MAX_OBJECTS", 8)
    with pytest.raises(DocumentTooLargeError, match="more than 8 objects"):
        load_document(path, meta, "bank")
    path.write_bytes(objstm_pdf(10**9))  # 선언만 큰 스트림
    monkeypatch.setattr(loaders, "PDF_MAX_OBJECTS", 1000)
    with pytest.raises(DocumentTooLargeError, match="more than 1,000 objects"):
        load_document(path, meta, "bank")


def test_object_budget_is_scoped_to_the_block(tmp_path):
    path = tmp_path / "xref.pdf"
    path.write_bytes(xref_scan_pdf(50))
    with pdfplumber.open(path) as pdf:
        with pdf_guard.object_budget(49), pytest.raises(pdf_guard.PDFObjectLimitError):
            pdf_guard.check_object_count(pdf.doc)
        with pdf_guard.object_budget(50):
            pdf_guard.check_object_count(pdf.doc)
        with pytest.raises(RuntimeError, match="object_budget"):
            pdf_guard.check_object_count(pdf.doc)  # 블록 밖에서 부르면 상한 없이 통과하지 않는다
