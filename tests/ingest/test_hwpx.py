import struct
import tracemalloc
import zipfile
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

import axbuilder.ingest.loaders as loaders
from axbuilder.ingest.chunking import chunk_document
from axbuilder.ingest.loaders import DocumentTooLargeError, load_document
from axbuilder.models import DocMeta

HS = "http://www.hancom.co.kr/hwpml/2011/section"
HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"

meta_terms = DocMeta(
    doc_id="terms-1",
    title="예금거래기본약관",
    source="tests (최소 HWPX)",
    doc_type="terms",
)


def paragraph(text: str) -> str:
    return f"<hp:p><hp:run><hp:t>{escape(text)}</hp:t></hp:run></hp:p>"


def section_xml(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<hs:sec xmlns:hs="{HS}" xmlns:hp="{HP}">{body}</hs:sec>'
    )


def write_hwpx(path: Path, sections: dict[str, str]) -> Path:
    """HWPX(ZIP)의 최소 구조: mimetype + Contents/section{N}.xml.

    바이너리 파일을 커밋하지 않으려고 테스트 안에서 만든다.
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        for name, xml in sections.items():
            zf.writestr(f"Contents/{name}", xml)
    return path


def make_hwpx(tmp_path: Path, sections: list[list[str]]) -> Path:
    bodies = {
        f"section{i}.xml": section_xml("".join(paragraph(t) for t in texts))
        for i, texts in enumerate(sections)
    }
    return write_hwpx(tmp_path / "terms.hwpx", bodies)


def lines_of(path: Path) -> list[str]:
    doc = load_document(path, meta_terms, "bank")
    assert [p.number for p in doc.pages] == [1]  # HWPX는 쪽 정보가 없어 전체를 Page 1개로
    return doc.pages[0].text.split("\n")


def test_hwpx_one_paragraph_per_line_and_articles(tmp_path):
    path = make_hwpx(tmp_path, [["제1조(목적) 이 약관은 ...", "제2조(정의) 이 약관에서 ..."]])
    doc = load_document(path, meta_terms, "bank")
    assert [c.article for c in chunk_document(doc)] == ["제1조", "제2조"]
    assert doc.pages[0].text == "제1조(목적) 이 약관은 ...\n제2조(정의) 이 약관에서 ..."


def test_hwpx_sections_in_numeric_order(tmp_path):
    # ZIP 안에는 section0, section1, section10, section2 ... 순서(사전순)로 넣는다
    names = sorted(f"section{i}.xml" for i in range(11))
    bodies = {name: section_xml(paragraph(f"구역 {name[7:-4]}")) for name in names}
    path = write_hwpx(tmp_path / "terms.hwpx", bodies)
    assert lines_of(path) == [f"구역 {i}" for i in range(11)]


def test_hwpx_table_paragraph_not_duplicated(tmp_path):
    # 표 셀의 문단은 바깥 문단 안에 중첩된다. hp:t는 가장 가까운 hp:p에만 붙는다
    table = (
        "<hp:p><hp:run><hp:t>표 앞 글</hp:t></hp:run><hp:run><hp:tbl><hp:tr>"
        "<hp:tc><hp:subList>" + paragraph("셀 하나") + "</hp:subList></hp:tc>"
        "<hp:tc><hp:subList>" + paragraph("셀 둘") + "</hp:subList></hp:tc>"
        "</hp:tr></hp:tbl></hp:run></hp:p>"
    )
    path = write_hwpx(tmp_path / "terms.hwpx", {"section0.xml": section_xml(table)})
    assert lines_of(path) == ["표 앞 글", "셀 하나", "셀 둘"]


def test_hwpx_runs_join_and_space_elements_become_spaces(tmp_path):
    body = (
        "<hp:p><hp:run><hp:t>제1조</hp:t></hp:run><hp:run><hp:t>(목적)</hp:t></hp:run>"
        "<hp:run><hp:t> 이 약관은<hp:fwSpace/>예금<hp:tab/>거래에   적용한다.</hp:t>"
        "</hp:run></hp:p>"
        "<hp:p><hp:run/></hp:p>" + paragraph("제2조(정의) 가")  # 빈 문단은 줄을 만들지 않는다
    )
    path = write_hwpx(tmp_path / "terms.hwpx", {"section0.xml": section_xml(body)})
    assert lines_of(path) == ["제1조(목적) 이 약관은 예금 거래에 적용한다.", "제2조(정의) 가"]


def test_hwpx_rejects_oversized_sections(tmp_path):
    # 구역 하나하나는 50MB 아래여도 풀어 읽은 합계가 50MB를 넘으면 거절한다 (zip bomb 방지)
    big = section_xml(paragraph("가" * 10) + " " * (26 * 1024 * 1024))
    path = write_hwpx(tmp_path / "bomb.hwpx", {"section0.xml": big, "section1.xml": big})
    assert path.stat().st_size < 1024 * 1024  # 압축하면 작다
    with pytest.raises(ValueError, match="50MB"):
        load_document(path, meta_terms, "bank")


def test_hwpx_rejects_dtd_entity_expansion(tmp_path):
    # PR #16 보안 리뷰: 내부 엔티티는 XML 바이트 상한(50MB)을 우회해 풀린다. 이 4KB 구역은
    # 막지 않으면 텍스트 2백만 자(6MB)가 된다(겹치면 수백 MB). DTD가 나오면 바로 멈춘다
    entities = '<!ENTITY a "' + "가" * 1000 + '"><!ENTITY b "' + "&a;" * 100 + '">'
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<!DOCTYPE hs:sec [{entities}]>"
        f'<hs:sec xmlns:hs="{HS}" xmlns:hp="{HP}">'
        + "<hp:p><hp:run><hp:t>제1조(목적) "
        + "&b;" * 20
        + "</hp:t></hp:run></hp:p></hs:sec>"
    )
    path = write_hwpx(tmp_path / "entities.hwpx", {"section0.xml": xml})
    assert path.stat().st_size < 8 * 1024

    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="DTD"):
            load_document(path, meta_terms, "bank")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024, f"peak {peak:,} bytes"


def forge_size_and_crc(path: Path, name: str, size: int, crc: int) -> None:
    """ZIP 헤더(로컬 헤더와 중앙 디렉터리)의 압축 해제 크기·CRC를 거짓 값으로 바꾼다."""
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as zf:
        local = zf.getinfo(name).header_offset
    struct.pack_into("<I", data, local + 14, crc)
    struct.pack_into("<I", data, local + 22, size)
    central = data.find(b"PK\x01\x02")
    while central != -1:
        name_len = struct.unpack_from("<H", data, central + 28)[0]
        if data[central + 46 : central + 46 + name_len] == name.encode():
            struct.pack_into("<I", data, central + 16, crc)
            struct.pack_into("<I", data, central + 24, size)
        central = data.find(b"PK\x01\x02", central + 46)
    path.write_bytes(data)


def test_hwpx_forged_small_size_does_not_inflate_whole_section(tmp_path):
    # PR #8 리뷰: 헤더에 작은 크기(와 그에 맞춘 CRC)를 적고 실제로는 64MB가 풀리는 구역.
    # zf.read()는 구역 전체를 한 번에 푼 뒤에야 헤더 크기로 자른다 → 1MB씩 풀어 읽어야 한다
    xml = section_xml(paragraph("제1조(목적) 가")).encode()
    path = tmp_path / "forged.hwpx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        with zf.open("Contents/section0.xml", "w") as member:
            member.write(xml)
            for _ in range(64):
                member.write(b" " * (1024 * 1024))
    forge_size_and_crc(path, "Contents/section0.xml", len(xml), zlib.crc32(xml))
    assert path.stat().st_size < 1024 * 1024

    tracemalloc.start()
    try:
        doc = load_document(path, meta_terms, "bank")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert doc.pages[0].text == "제1조(목적) 가"
    assert peak < 16 * 1024 * 1024, f"peak {peak:,} bytes"


@pytest.mark.parametrize("compression", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_hwpx_rejects_sections_not_stored_or_deflated(tmp_path, compression):
    # zipfile은 bzip2·lzma를 풀 때 한 번에 풀 양의 상한이 없다 (몇 KB가 수백 MB로).
    # 한컴오피스는 구역을 deflate로 저장한다
    path = tmp_path / "bz.hwpx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Contents/section0.xml", section_xml(paragraph("가")), compress_type=compression
        )
    with pytest.raises(ValueError, match="compression"):
        load_document(path, meta_terms, "bank")


def test_hwpx_that_is_not_a_zip_raises_value_error(tmp_path):
    path = tmp_path / "broken.hwpx"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(ValueError):
        load_document(path, meta_terms, "bank")


def test_hwpx_without_sections_raises_value_error(tmp_path):
    path = write_hwpx(tmp_path / "empty.hwpx", {})
    with pytest.raises(ValueError):
        load_document(path, meta_terms, "bank")


# ---- 요소 수 폭탄 (v0.1.0 보안 점검): 빈 <hp:p/>만 20MB(파일 30KB)가 9초·544MB를 썼다 ----


def test_hwpx_text_after_nested_table_stays_in_outer_paragraph(tmp_path):
    # 바깥 문단의 줄은 문단이 시작한 자리에 놓이고, 표 뒤에 이어지는 글도 그 줄에 붙는다
    body = (
        "<hp:p><hp:run><hp:t>앞</hp:t></hp:run><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList>"
        + paragraph("셀")
        + "<hp:p><hp:run/></hp:p>"
        + "</hp:subList></hp:tc></hp:tr></hp:tbl></hp:run><hp:run><hp:t>뒤</hp:t></hp:run></hp:p>"
        + paragraph("다음 문단")
    )
    path = write_hwpx(tmp_path / "terms.hwpx", {"section0.xml": section_xml(body)})
    assert lines_of(path) == ["앞뒤", "셀", "다음 문단"]


def test_hwpx_text_element_edge_cases(tmp_path):
    body = (
        "<hp:t>문단 밖 글자는 버린다</hp:t>"
        # hp:t 안의 다른 요소: 그 안의 글자는 버리고 뒤에 이어지는 글자는 살린다.
        # 공백류 요소는 hp:t 바로 아래에 있을 때만 공백이 된다
        '<hp:p><hp:run><hp:t>가<hp:markpenBegin color="#FFFF00">표시<hp:tab/></hp:markpenBegin>'
        "나<hp:lineBreak/>다<hp:p><hp:run><hp:t>안쪽 문단</hp:t></hp:run></hp:p>라</hp:t>"
        "</hp:run></hp:p>"
    )
    path = write_hwpx(tmp_path / "terms.hwpx", {"section0.xml": section_xml(body)})
    assert lines_of(path) == ["가나 다라"]


def empty_paragraphs(count: int) -> str:
    return section_xml("<hp:p/>" * count)


def test_hwpx_rejects_too_many_elements(tmp_path, monkeypatch):
    # 요소는 모든 구역을 합쳐 센다 (구역마다 뿌리 hs:sec 하나 + 문단들)
    monkeypatch.setattr(loaders, "HWPX_MAX_ELEMENTS", 100)
    ok = {"section0.xml": empty_paragraphs(49), "section1.xml": section_xml(paragraph("가") * 16)}
    path = write_hwpx(tmp_path / "ok.hwpx", ok)  # 50 + (1 + 16 * 3) = 99
    assert lines_of(path) == ["가"] * 16
    bomb = {"section0.xml": empty_paragraphs(49), "section1.xml": empty_paragraphs(50)}
    path = write_hwpx(tmp_path / "bomb.hwpx", bomb)  # 50 + 51 = 101
    with pytest.raises(DocumentTooLargeError, match="more than 100 XML elements"):
        load_document(path, meta_terms, "bank")


def test_hwpx_empty_elements_do_not_build_a_tree(tmp_path):
    # 상한 아래여도 요소마다 객체를 남기면 메모리가 요소 수에 비례한다(ElementTree로 읽던 때는
    # 이 파일의 tracemalloc 최고치가 31.7MB, 지금은 2.9MB). 글자만 모으고 요소는 남기지 않는다
    assert loaders.HWPX_MAX_ELEMENTS >= 200_001
    path = write_hwpx(tmp_path / "many.hwpx", {"section0.xml": empty_paragraphs(200_000)})
    assert path.stat().st_size < 16 * 1024

    tracemalloc.start()
    try:
        doc = load_document(path, meta_terms, "bank")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert doc.pages[0].text == ""
    assert peak < 8 * 1024 * 1024, f"peak {peak:,} bytes"
