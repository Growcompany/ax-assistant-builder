import io
import logging
import unicodedata
from pathlib import Path

import pytest

from axbuilder.ingest.chunking import chunk_document
from axbuilder.ingest.loaders import UnsupportedFileType, load_document
from axbuilder.models import DocMeta

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_law.txt"

meta = DocMeta(
    doc_id="law-1",
    title="예금자보호법(발췌)",
    source="tests/fixtures/sample_law.txt",
    doc_type="law",
)


def make_pdf(pages: list[str]) -> bytes:
    """pdfplumber가 읽을 수 있는 최소 PDF를 바이트로 만든다 (저장소에 바이너리를 두지 않기 위해).

    글꼴 파일 없이 Type0 글꼴 + Identity-H 인코딩을 쓰고, 글자 코드 = 유니코드 코드포인트로 둔 뒤
    ToUnicode CMap으로 되돌린다. 빈 문자열 페이지는 텍스트 연산자가 없는 페이지
    (스캔본과 같은 상태)다.
    """
    chars = sorted({ch for text in pages for ch in text if ch != "\n"})
    bfchar = "\n".join(f"<{ord(c):04X}> <{ord(c):04X}>" for c in chars)
    cmap = (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{len(chars)} beginbfchar\n{bfchar}\nendbfchar\n"
        "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    ).encode("ascii")

    # 객체 번호: 1 카탈로그, 2 페이지 목록, 3 글꼴, 4 CID 글꼴, 5 ToUnicode, 6부터 (페이지, 내용) 쌍
    page_ids = [6 + 2 * i for i in range(len(pages))]
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (" ".join(f"{p} 0 R" for p in page_ids).encode(), len(pages)),
        3: b"<< /Type /Font /Subtype /Type0 /BaseFont /TestFont /Encoding /Identity-H "
        b"/DescendantFonts [4 0 R] /ToUnicode 5 0 R >>",
        4: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /TestFont "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/FontDescriptor << /Type /FontDescriptor /FontName /TestFont /Flags 4 "
        b"/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 900 /Descent -200 "
        b"/CapHeight 700 /StemV 80 >> /DW 1000 >>",
        5: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap),
    }
    for page_id, text in zip(page_ids, pages, strict=True):
        ops = []
        if text:
            ops.append("BT /F1 12 Tf 72 720 Td 14 TL")
            ops += [
                f"<{''.join(f'{ord(c):04X}' for c in line)}> Tj T*" for line in text.split("\n")
            ]
            ops.append("ET")
        content = "\n".join(ops).encode("ascii")
        objs[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (page_id + 1)
        )
        objs[page_id + 1] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(b"%d 0 obj\n%s\nendobj\n" % (num, objs[num]))
    xref_at = out.tell()
    size = max(objs) + 1
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % size)
    out.write(b"".join(b"%010d 00000 n \n" % offsets[num] for num in range(1, size)))
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_at))
    return out.getvalue()


def test_load_txt_as_single_page():
    doc = load_document(FIXTURE, meta, "bank")
    assert len(doc.pages) == 1 and doc.pages[0].number == 1
    assert doc.pages[0].text.startswith("제1조(목적)")
    assert doc.meta == meta and doc.workspace_id == "bank"


def test_unsupported_extension():
    with pytest.raises(UnsupportedFileType):
        load_document(Path("a.hwp"), meta, "bank")


def test_load_markdown(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# 상담 응대 지침\n\n고객에게 먼저 인사한다.\n", encoding="utf-8")
    doc = load_document(path, meta, "bank")
    assert [p.number for p in doc.pages] == [1]
    assert "고객에게 먼저 인사한다." in doc.pages[0].text


def test_extension_is_case_insensitive(tmp_path):
    path = tmp_path / "LAW.TXT"
    path.write_text("제1조(목적) 가", encoding="utf-8")
    assert load_document(path, meta, "bank").pages[0].text == "제1조(목적) 가"


def test_txt_in_cp949_is_decoded(tmp_path):
    # 공공기관에서 받은 한글 텍스트 파일은 UTF-8이 아닌 CP949(EUC-KR)인 경우가 많다
    path = tmp_path / "law.txt"
    path.write_bytes("제1조(목적) 예금자를 보호한다.".encode("cp949"))
    assert load_document(path, meta, "bank").pages[0].text == "제1조(목적) 예금자를 보호한다."


def test_text_is_nfc_normalized(tmp_path):
    # 자모가 풀어진(NFD) 한글은 화면에선 같아 보여도 검색어 "보호"와 문자열이 달라 맞지 않는다
    path = tmp_path / "law.txt"
    path.write_text(unicodedata.normalize("NFD", "제1조(목적) 보호"), encoding="utf-8")
    assert load_document(path, meta, "bank").pages[0].text == "제1조(목적) 보호"


def test_load_pdf_keeps_page_numbers_and_korean_text(tmp_path):
    path = tmp_path / "law.pdf"
    path.write_bytes(
        make_pdf(["제1조(목적) 이 법은 예금자를\n보호함을 목적으로 한다.", "제2조(정의) 용어의 뜻"])
    )
    doc = load_document(path, meta, "bank")
    assert [p.number for p in doc.pages] == [1, 2]
    assert doc.pages[0].text == "제1조(목적) 이 법은 예금자를\n보호함을 목적으로 한다."
    assert [(c.article, c.page) for c in chunk_document(doc)] == [("제1조", 1), ("제2조", 2)]


def test_pdf_without_text_layer_yields_no_chunks(tmp_path, caplog):
    # 스캔본 PDF: 페이지는 있지만 텍스트 층이 없다 → 빈 청크를 색인하지 않고 경고만 남긴다
    path = tmp_path / "scanned.pdf"
    path.write_bytes(make_pdf(["", ""]))
    with caplog.at_level(logging.WARNING, logger="axbuilder.ingest.loaders"):
        doc = load_document(path, meta, "bank")
    assert [p.number for p in doc.pages] == [1, 2]
    assert chunk_document(doc) == []
    assert "no extractable text" in caplog.text
