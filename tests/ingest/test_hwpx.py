import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from axbuilder.ingest.chunking import chunk_document
from axbuilder.ingest.loaders import load_document
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
    # 구역 하나하나는 50MB 아래여도 압축 해제 합계가 50MB를 넘으면 읽지 않는다 (zip bomb 방지)
    big = section_xml(paragraph("가" * 10) + " " * (26 * 1024 * 1024))
    path = write_hwpx(tmp_path / "bomb.hwpx", {"section0.xml": big, "section1.xml": big})
    assert path.stat().st_size < 1024 * 1024  # 압축하면 작다
    with pytest.raises(ValueError, match="50MB"):
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
