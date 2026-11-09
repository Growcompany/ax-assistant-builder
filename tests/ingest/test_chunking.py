from pathlib import Path

import pytest

from axbuilder.ingest.chunking import chunk_by_article, chunk_document, chunk_fixed
from axbuilder.ingest.loaders import load_document
from axbuilder.models import DocMeta, Document, Page

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_law.txt"

meta = DocMeta(
    doc_id="law-1",
    title="예금자보호법(발췌)",
    source="tests/fixtures/sample_law.txt",
    doc_type="law",
)
doc = load_document(FIXTURE, meta, "bank")

# 서로 다른 글자 250개: 겹침 구간이 정확히 같은 위치인지 글자로 확인할 수 있다
LONG_TEXT = "".join(chr(0xAC00 + i) for i in range(250))
long_doc = Document(
    meta=meta.model_copy(update={"doc_id": "prod-1", "doc_type": "product"}),
    workspace_id="bank",
    pages=[Page(number=1, text=LONG_TEXT)],
)
# 조항 머리가 있어도 상품설명서는 고정 길이로 나눈다
product_doc = doc.model_copy(update={"meta": meta.model_copy(update={"doc_type": "product"})})


def law(*pages: str) -> Document:
    return Document(
        meta=meta,
        workspace_id="bank",
        pages=[Page(number=i, text=text) for i, text in enumerate(pages, start=1)],
    )


def test_chunk_by_article_splits_on_headers():
    chunks = chunk_by_article(doc)
    assert [c.article for c in chunks] == ["제1조", "제2조", "제2조의2"]
    assert chunks[0].chunk_id == "law-1::0000"
    assert chunks[2].text.startswith("제2조의2(보호한도)")


def test_chunk_fixed_overlap():
    chunks = chunk_fixed(long_doc, size=100, overlap=20)  # 250자 문서
    assert len(chunks) == 3
    assert chunks[0].text[-20:] == chunks[1].text[:20]


def test_chunk_document_picks_article_for_law():
    assert chunk_document(doc)[0].article == "제1조"


def test_chunk_document_falls_back_to_fixed_for_product():
    assert chunk_document(product_doc)[0].article is None


def test_empty_document_yields_no_chunks():
    empty = doc.model_copy(update={"pages": [Page(number=1, text="   ")]})
    assert chunk_document(empty) == []


def test_chunks_carry_document_fields():
    internal = doc.model_copy(update={"meta": meta.model_copy(update={"access_level": "internal"})})
    chunks = chunk_by_article(internal)
    assert [c.chunk_id for c in chunks] == ["law-1::0000", "law-1::0001", "law-1::0002"]
    for c in chunks:
        assert (c.doc_id, c.workspace_id, c.title) == ("law-1", "bank", "예금자보호법(발췌)")
        assert c.access_level == "internal"
        assert c.page == 1


def test_article_text_stops_at_next_header():
    first = chunk_by_article(doc)[0]
    assert first.text == "제1조(목적) 이 법은 예금자를 보호함을 목적으로 한다."


def test_article_page_is_where_header_appears():
    chunks = chunk_by_article(
        law("제1조(목적) 이 법은 예금자를", "보호함을 목적으로 한다.\n제2조(정의) 용어의 뜻")
    )
    assert [(c.article, c.page) for c in chunks] == [("제1조", 1), ("제2조", 2)]
    assert "보호함을 목적으로" in chunks[0].text  # 페이지를 넘어간 조항 본문도 같은 청크


def test_blank_pages_are_skipped_but_page_numbers_kept():
    chunks = chunk_by_article(law("", "제1조(목적) 가", "  \n ", "제2조(정의) 나"))
    assert [c.page for c in chunks] == [2, 4]


def test_cross_reference_at_line_start_is_not_a_header():
    # PDF 줄바꿈으로 "제3조에 따른"처럼 다른 조항을 가리키는 말이 줄 맨 앞에 올 수 있다
    chunks = chunk_by_article(
        law("제1조(목적) 이 법은\n제3조에 따른 보호와\n제2조의 규정을 정한다.\n제2조(정의) 뜻")
    )
    assert [c.article for c in chunks] == ["제1조", "제2조"]


def test_text_before_first_article_is_kept_without_article():
    chunks = chunk_by_article(law("예금자보호법\n제1장 총칙\n제1조(목적) 가\n제2조(정의) 나"))
    assert [c.article for c in chunks] == [None, "제1조", "제2조"]
    assert chunks[0].text == "예금자보호법\n제1장 총칙"
    assert chunks[1].chunk_id == "law-1::0001"


def test_chunk_document_needs_two_headers_for_article_split():
    single = law("제1조(목적) 이 법은 예금자를 보호함을 목적으로 한다.")
    assert chunk_document(single)[0].article is None


@pytest.mark.parametrize("doc_type", ["regulation", "terms"])
def test_chunk_document_uses_articles_for_regulation_and_terms(doc_type):
    typed = doc.model_copy(update={"meta": meta.model_copy(update={"doc_type": doc_type})})
    assert [c.article for c in chunk_document(typed)] == ["제1조", "제2조", "제2조의2"]


def test_chunk_fixed_loses_no_text():
    chunks = chunk_fixed(long_doc, size=100, overlap=20)
    assert chunks[0].text + "".join(c.text[20:] for c in chunks[1:]) == LONG_TEXT
    assert [c.chunk_id for c in chunks] == ["prod-1::0000", "prod-1::0001", "prod-1::0002"]


def test_chunk_fixed_page_is_where_chunk_starts():
    two_pages = law("가" * 60, "나" * 60)  # 이어 붙이면 60 + 줄바꿈 1 + 60 = 121자
    chunks = chunk_fixed(two_pages, size=50, overlap=10)  # 시작 위치 0, 40, 80
    assert [c.page for c in chunks] == [1, 1, 2]
    assert all(c.article is None for c in chunks)


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (100, 100), (100, 150), (100, -1)])
def test_chunk_fixed_rejects_invalid_window(size, overlap):
    with pytest.raises(ValueError):
        chunk_fixed(long_doc, size=size, overlap=overlap)
