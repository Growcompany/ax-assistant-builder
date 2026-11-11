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
# 조항 머리가 없는 상품설명서는 고정 길이로 나눈다
# (Task 3부터 조항 머리가 2개 이상이면 doc_type과 무관하게 조항 단위)
product_doc = Document(
    meta=meta.model_copy(update={"doc_id": "prod-2", "doc_type": "product"}),
    workspace_id="bank",
    pages=[
        Page(
            number=1, text="# 정기예금 상품설명서\n기본금리는 연 2.80%입니다.\n- 제1조에 따른 우대"
        )
    ],
)


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
        assert c.page is None  # txt는 쪽이 없다 (tests/ingest/test_page_numbers.py)


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


@pytest.mark.parametrize("doc_type", ["internal", "product", "other"])
def test_internal_doc_with_articles_is_split_by_article(doc_type):
    # 가상 내부 지침도 제N조 형식이다. doc_type이 아니라 조항 머리 개수로 정한다
    typed = doc.model_copy(update={"meta": meta.model_copy(update={"doc_type": doc_type})})
    assert [c.article for c in chunk_document(typed)] == ["제1조", "제2조", "제2조의2"]


def test_chunk_document_keeps_preamble_before_first_article():
    # PR #5 리뷰 m6: 내부 지침 제1조 앞의 머리말(대외비 표시, 가상 문서 고지)을 버리지 않는다
    guide = law(
        "본 문서는 포트폴리오용 가상 문서입니다\n\n# 상담 응대 지침\n\n"
        "- 문서 구분: 내부 지침 (대외비, 외부 제공 금지)\n\n"
        "제1조(목적) 가\n\n제2조(적용 범위) 나"
    )
    chunks = chunk_document(guide)
    assert [c.article for c in chunks] == [None, "제1조", "제2조"]
    assert chunks[0].text.startswith("본 문서는 포트폴리오용 가상 문서입니다")
    assert "대외비" in chunks[0].text
    assert chunks[1].text == "제1조(목적) 가"


def test_addenda_articles_are_prefixed():
    chunks = chunk_document(
        law("제1조(목적) 가\n제2조(정의) 나\n부칙\n제1조(시행일) 다\n제2조(경과조치) 라")
    )
    assert [c.article for c in chunks] == ["제1조", "제2조", "부칙 제1조", "부칙 제2조"]
    assert chunks[1].text == "제2조(정의) 나"  # 부칙 머리 줄이 본문 마지막 조항에 붙지 않는다
    assert chunks[2].text == "부칙\n제1조(시행일) 다"  # 부칙 머리 줄은 그 부칙의 첫 조항과 함께
    assert chunks[3].text == "제2조(경과조치) 라"


def test_addenda_of_law_page_with_amendment_numbers():
    # 법령 원문: 개정마다 부칙이 있고, 조항 없는 부칙(본문만)도 있다
    chunks = chunk_document(
        law(
            "제1조(목적) 가\n제2조(정의) 나\n"
            "부 칙 <법률 제5540호, 1998. 5. 25.>\n이 법은 공포한 날부터 시행한다.\n"
            "부칙 <법률 제21296호, 2025. 12. 30.>\n"
            "제1조(시행일) 이 법은 공포 후 6개월이 경과한 날부터 시행한다.\n"
            "제2조 (적용례) 다"
        )
    )
    assert [c.article for c in chunks] == ["제1조", "제2조", "부칙", "부칙 제1조", "부칙 제2조"]
    assert chunks[2].text == "부 칙 <법률 제5540호, 1998. 5. 25.>\n이 법은 공포한 날부터 시행한다."
    assert chunks[3].text.startswith("부칙 <법률 제21296호, 2025. 12. 30.>\n제1조(시행일)")


def test_line_starting_with_addenda_reference_is_not_addenda_header():
    # "부칙 제7조의 개정규정은"처럼 부칙 조항을 가리키는 말이 줄 맨 앞에 와도 부칙 머리가 아니다
    chunks = chunk_document(law("제1조(목적) 가\n부칙 제7조의 개정규정은 나\n제2조(정의) 다"))
    assert [c.article for c in chunks] == ["제1조", "제2조"]


def test_addenda_amendments_of_other_laws_are_not_articles():
    # PR #8 리뷰: 부칙의 "다른 법률의 개정"은 다른 법의 조 번호로 시작하는 지시문과, 그 지시로
    # 새로 쓰는 조문을 담는다. 이 줄들은 앞 부칙 조항의 본문이지 부칙 조항이 아니다
    amendments = (
        "제2조(다른 법률의 개정) ① 상법 일부를 다음과 같이 개정한다.\n"
        "제275조 및 제446조제50호를 각각 삭제한다.\n"
        "② 보험업법 일부를 다음과 같이 개정한다.\n"
        "제18조 중 “금융위원회”를 “금융감독원”으로 한다.\n"
        "제109조 각 호 외의 부분 중 “제59조제1항”을 “제23조제1항”으로 한다.\n"
        "③ 은행법 일부를 다음과 같이 개정한다.\n"
        "제5장에 제33조의5를 다음과 같이 신설한다.\n"
        "제33조의5(사채등의 등록) 사채의 소유자는 등록할 수 있다.\n"
        "제5조를 다음과 같이 한다.\n"
        "제5조 (특례) 중앙회의 신용사업부문은 은행으로 본다."
    )
    chunks = chunk_document(
        law(
            "제1조(목적) 가\n제2조(정의) 나\n부칙 <법률 제9999호, 2020. 1. 1.>\n"
            "제1조(시행일) 이 법은 공포한 날부터 시행한다.\n"
            f"{amendments}\n"
            # 부칙 조항을 줄여 보여 주는 줄은 진짜 부칙 조항이다
            "제3조 및 제4조 생략\n제5조 삭제 <2021. 1. 1.>\n제6조 부터 제7조까지 생략"
        )
    )
    assert [c.article for c in chunks] == [
        "제1조",
        "제2조",
        "부칙 제1조",
        "부칙 제2조",
        "부칙 제3조",
        "부칙 제5조",
        "부칙 제6조",
    ]
    assert chunks[3].text == amendments


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
