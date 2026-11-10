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


# ---- 요소 수 폭탄 (v0.1.0 보안 점검): 빈 <hp:p/>만 20MB(파일 30KB)가 8초·541MB를 썼다 ----


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


def test_hwpx_default_limits():
    assert loaders.HWPX_MAX_EVENTS == 10_000_000
    assert loaders.HWPX_MAX_DEPTH == 200
    assert loaders.HWPX_MAX_SECTIONS == 1_000


def test_hwpx_event_limit_is_exact_and_counts_all_sections(tmp_path, monkeypatch):
    # 파서 이벤트 = 요소 하나에 1 + 속성 수, 글자 조각 하나에 1. 모든 구역을 합쳐 센다.
    # 정확히 상한이면 통과하고 하나 더 많으면 거절한다
    monkeypatch.setattr(loaders, "HWPX_MAX_EVENTS", 100)
    # 구역 0: 뿌리 1 + 빈 문단 49 = 50. 구역 1: 뿌리 1 + 문단 12개 × (요소 3 + 글자 1) + 빈 문단 1
    text_section = section_xml(paragraph("가") * 12 + "<hp:p/>")
    exact = {"section0.xml": empty_paragraphs(49), "section1.xml": text_section}
    path = write_hwpx(tmp_path / "ok.hwpx", exact)
    assert lines_of(path) == ["가"] * 12
    one_more = {"section0.xml": empty_paragraphs(50), "section1.xml": text_section}
    path = write_hwpx(tmp_path / "bomb.hwpx", one_more)
    with pytest.raises(DocumentTooLargeError, match="more than 100 parser events"):
        load_document(path, meta_terms, "bank")
    path = write_hwpx(tmp_path / "bomb.hwpx", {"section0.xml": empty_paragraphs(100)})  # 101
    with pytest.raises(DocumentTooLargeError, match="more than 100 parser events"):
        load_document(path, meta_terms, "bank")
    path = write_hwpx(tmp_path / "ok.hwpx", {"section0.xml": empty_paragraphs(99)})  # 100
    assert lines_of(path) == [""]


def test_hwpx_attributes_count_as_events(tmp_path, monkeypatch):
    # PR #19 보안 리뷰: 요소 하나에 속성 80만 개(1.7MB 파일)가 요소 수 상한을 비켜 269MB를 썼다
    monkeypatch.setattr(loaders, "HWPX_MAX_EVENTS", 10)
    attrs = "".join(f' a{i}=""' for i in range(4))
    body = paragraph("가") + f"<hp:x{attrs}/>"  # 뿌리 1 + 문단 4 + (요소 1 + 속성 4) = 10
    path = write_hwpx(tmp_path / "ok.hwpx", {"section0.xml": section_xml(body)})
    assert lines_of(path) == ["가"]
    body = paragraph("가") + f'<hp:x{attrs} b=""/>'  # 11
    path = write_hwpx(tmp_path / "bomb.hwpx", {"section0.xml": section_xml(body)})
    with pytest.raises(DocumentTooLargeError, match="more than 10 parser events"):
        load_document(path, meta_terms, "bank")


def test_hwpx_text_pieces_count_as_events(tmp_path, monkeypatch):
    # PR #19 보안 리뷰: 파서는 줄바꿈마다 글자 조각을 따로 넘긴다. hp:t 안의 줄바꿈 2천만 개
    # (20KB 파일)가 요소 서너 개로 361MB를 썼다
    monkeypatch.setattr(loaders, "HWPX_MAX_EVENTS", 1000)
    newlines = "<hp:p><hp:t>" + "\n" * 5000 + "</hp:t></hp:p>"
    path = write_hwpx(
        tmp_path / "bomb.hwpx", {"section0.xml": section_xml(paragraph("가") + newlines)}
    )
    with pytest.raises(DocumentTooLargeError, match="more than 1,000 parser events"):
        load_document(path, meta_terms, "bank")
    outside = paragraph("가") + "\n" * 5000  # 문단 밖의 글자 조각도 파서 호출이라 센다
    path = write_hwpx(tmp_path / "bomb.hwpx", {"section0.xml": section_xml(outside)})
    with pytest.raises(DocumentTooLargeError, match="more than 1,000 parser events"):
        load_document(path, meta_terms, "bank")


def test_hwpx_whitespace_runs_are_collapsed_while_reading(tmp_path):
    # 상한 아래의 공백 조각도 쌓아 두지 않는다(어차피 공백 하나로 줄어든다). 결과는 그대로다
    body = (
        "<hp:p><hp:t>\n \t가\n\n\n나 \n<hp:tab/>\n<hp:tab/>다\u3000\n</hp:t>"
        "<hp:t>\n라</hp:t><hp:t>마</hp:t></hp:p>"
        + "<hp:p><hp:t>"
        + "\n" * 300_000
        + "</hp:t></hp:p>"
    )
    path = write_hwpx(tmp_path / "terms.hwpx", {"section0.xml": section_xml(body)})
    tracemalloc.start()
    try:
        lines = lines_of(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert lines == ["가 나 다 라마"]
    # 조각을 그대로 쌓으면 문단 하나에 30만 개가 남는다 (바꾸기 전 최고치 5.8MB)
    assert peak < 4 * 1024 * 1024, f"peak {peak:,} bytes"


def test_hwpx_rejects_deep_nesting(tmp_path, monkeypatch):
    # PR #19 보안 리뷰: hp:p를 999,990단 중첩한 19KB 파일이 264MB를 썼다. 실제 문서는 12단이다
    monkeypatch.setattr(loaders, "HWPX_MAX_DEPTH", 10)

    def nested(depth: int) -> str:  # 뿌리(1단) 아래로 문단 depth - 2단, 그 안에 hp:t
        return section_xml("<hp:p>" * (depth - 2) + "<hp:t>가</hp:t>" + "</hp:p>" * (depth - 2))

    path = write_hwpx(tmp_path / "ok.hwpx", {"section0.xml": nested(10)})
    assert lines_of(path) == ["가"]
    path = write_hwpx(tmp_path / "deep.hwpx", {"section0.xml": nested(11)})
    with pytest.raises(DocumentTooLargeError, match="nested deeper than 10"):
        load_document(path, meta_terms, "bank")
    inside_text = section_xml("<hp:p><hp:t>" + "<a>" * 8 + "</a>" * 8 + "</hp:t></hp:p>")  # 11단
    path = write_hwpx(tmp_path / "deep.hwpx", {"section0.xml": inside_text})
    with pytest.raises(DocumentTooLargeError, match="nested deeper than 10"):
        load_document(path, meta_terms, "bank")


def test_hwpx_rejects_too_many_sections(tmp_path, monkeypatch):
    # PR #19 보안 리뷰: 구역 파일 40만 개가 21.8초·301MB를 썼다. 실제 문서는 구역이 몇 개다
    monkeypatch.setattr(loaders, "HWPX_MAX_SECTIONS", 3)
    sections = {f"section{i}.xml": section_xml(paragraph(f"구역 {i}")) for i in range(4)}
    path = write_hwpx(tmp_path / "many.hwpx", sections)
    with pytest.raises(DocumentTooLargeError, match="more than 3 sections"):
        load_document(path, meta_terms, "bank")
    del sections["section3.xml"]
    path = write_hwpx(tmp_path / "ok.hwpx", sections)
    assert lines_of(path) == ["구역 0", "구역 1", "구역 2"]


def test_hwpx_empty_elements_do_not_build_a_tree(tmp_path):
    # 상한 아래여도 요소마다 객체를 남기면 메모리가 요소 수에 비례한다(ElementTree로 읽던 때는
    # 이 파일의 tracemalloc 최고치가 31.7MB, 지금은 2.9MB). 글자만 모으고 요소는 남기지 않는다
    assert loaders.HWPX_MAX_EVENTS >= 200_001
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


def test_hwpx_rejects_one_huge_start_tag_before_its_attributes_are_built(tmp_path):
    # 이벤트 수만으로는 부족했다: 파서는 시작 태그를 끝까지 읽은 뒤에야 속성 사전을 만들어 넘기므로,
    # 속성 80만 개짜리 태그 하나는 이벤트 상한 아래에서 269MB를 쓰고 통과했다.
    # 파서가 이벤트 없이 삼킨 바이트(끝나지 않은 토큰)에 상한을 둔다
    assert loaders.HWPX_MAX_TOKEN_BYTES == 256 * 1024
    attrs = "".join(f' a{i:x}=""' for i in range(200_000))
    body = paragraph("가") + f"<hp:x{attrs}/>"
    path = write_hwpx(tmp_path / "attrs.hwpx", {"section0.xml": section_xml(body)})

    tracemalloc.start()
    try:
        with pytest.raises(DocumentTooLargeError, match="XML token"):
            load_document(path, meta_terms, "bank")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024, f"peak {peak:,} bytes"


def test_hwpx_long_tokens_under_the_limit_and_long_text_still_parse(tmp_path):
    # 글자는 읽는 대로 조각으로 넘어오므로 긴 본문(2MB)은 "긴 토큰"이 아니다
    long_value = "A" * (100 * 1024)
    body = f'<hp:p note="{long_value}"><hp:t>가</hp:t></hp:p>' + paragraph("나" * 700_000)
    path = write_hwpx(tmp_path / "ok.hwpx", {"section0.xml": section_xml(body)})
    assert lines_of(path) == ["가", "나" * 700_000]
    comments = paragraph("가") + "<!--x-->" * 100_000  # 이벤트를 내지 않는 토큰이 800KB 이어진다
    path = write_hwpx(tmp_path / "comments.hwpx", {"section0.xml": section_xml(comments)})
    with pytest.raises(DocumentTooLargeError, match="XML token"):
        load_document(path, meta_terms, "bank")


def test_hwpx_rejects_too_many_distinct_names(tmp_path, monkeypatch):
    # XML 파서는 처음 보는 요소·속성 이름을 표에 넣어 두고 지우지 않는다. 이름이 전부 다른 요소
    # 100만 개(이벤트 상한 안)가 257MB를 썼다
    assert loaders.HWPX_MAX_NAMES == 10_000
    monkeypatch.setattr(loaders, "HWPX_MAX_NAMES", 8)
    # 이름 8개: hs:sec, hp:p, hp:run, hp:t, hp:x, a0, a1, 그리고 다른 이름공간의 x
    body = paragraph("가") + '<hp:x a0="" a1=""/><x xmlns="urn:other"/>' + paragraph("나")
    path = write_hwpx(tmp_path / "ok.hwpx", {"section0.xml": section_xml(body)})
    assert lines_of(path) == ["가", "나"]
    for extra in ("<hp:y/>", '<hp:x a2=""/>', '<x xmlns="urn:another"/>'):
        path = write_hwpx(tmp_path / "names.hwpx", {"section0.xml": section_xml(body + extra)})
        with pytest.raises(DocumentTooLargeError, match="more than 8 distinct"):
            load_document(path, meta_terms, "bank")


LINESEG = (
    '<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" '
    'baseline="850" spacing="600" horzpos="0" horzsize="42520" flags="393216"/></hp:linesegarray>'
)


def real_paragraph(text: str) -> str:
    """한컴오피스가 쓰는 모양의 문단: 속성과 줄 배치 정보(linesegarray)가 붙는다."""
    return (
        '<hp:p id="0" paraPrIDRef="3" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="5"><hp:t>{escape(text)}</hp:t></hp:run>{LINESEG}</hp:p>'
    )


def real_table(rows: int, cols: int) -> str:
    """한컴오피스가 쓰는 모양의 표. 칸 하나가 파서 이벤트 55개쯤이다(요소 10 + 속성 44 + 글자 1)."""

    def cell(row: int, col: int) -> str:
        return (
            '<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" '
            'borderFillIDRef="4"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
            'vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" '
            'textHeight="0" hasTextRef="0" hasNumRef="0">'
            + real_paragraph(f"행{row}열{col} 금리 {row % 7}.{col:02d}%")
            + f'</hp:subList><hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
            '<hp:cellSpan colSpan="1" rowSpan="1"/><hp:cellSz width="7000" height="1500"/>'
            '<hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>'
        )

    body = "".join(
        "<hp:tr>" + "".join(cell(r, c) for c in range(cols)) + "</hp:tr>" for r in range(rows)
    )
    return (
        '<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
        '<hp:run charPrIDRef="0"><hp:tbl id="1" zOrder="0" numberingType="TABLE" '
        f'textWrap="TOP_AND_BOTTOM" pageBreak="CELL" repeatHeader="1" rowCnt="{rows}" '
        f'colCnt="{cols}" cellSpacing="0" borderFillIDRef="3" noAdjust="0">'
        '<hp:sz width="42000" widthRelTo="ABSOLUTE" height="3000" heightRelTo="ABSOLUTE" '
        f'protect="0"/>{body}</hp:tbl><hp:t>표 뒤 글자</hp:t></hp:run></hp:p>'
    )


def test_hwpx_large_ordinary_table_is_not_rejected(tmp_path):
    # PR #19 2차 리뷰: 이벤트 상한 100만은 표 위주의 정상 문서를 약 1만 8천 칸부터 막았다
    # (3,500행 × 6열에서 거절). 칸 하나가 이벤트 55개쯤이라 금리표가 긴 문서는 쉽게 넘는다
    rows, cols = 3500, 6
    body = real_paragraph("머리") + real_table(rows, cols)
    path = write_hwpx(tmp_path / "table.hwpx", {"section0.xml": section_xml(body)})
    lines = lines_of(path)
    # 줄 순서는 문단이 시작한 순서: 머리, 표를 품은 문단(표 뒤 글자), 칸들(행 → 열)
    assert lines[:3] == ["머리", "표 뒤 글자", "행0열0 금리 0.00%"]
    assert lines[2:] == [
        f"행{r}열{c} 금리 {r % 7}.{c:02d}%" for r in range(rows) for c in range(cols)
    ]
    assert len(lines) == 2 + rows * cols
