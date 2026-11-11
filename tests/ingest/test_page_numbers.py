"""쪽이 없는 형식(txt·md·hwpx)의 청크에는 쪽 번호를 달지 않는다.

로더는 이런 문서를 Page 하나(number=1)로 읽는다. 그 1을 그대로 청크에 달면 모든 인용이 "p.1"이
되어 뜻 없는 숫자가 프롬프트와 출처 목록에 들어간다. PDF는 쪽 번호가 그대로 남는다.
"""

import sys
from pathlib import Path

import pytest

from axbuilder.generation.answer import build_prompt
from axbuilder.generation.llm import FakeLLM
from axbuilder.ingest.chunking import chunk_by_article, chunk_document, chunk_fixed
from axbuilder.ingest.loaders import load_document
from axbuilder.models import DocMeta, Document, Page, ScoredChunk
from axbuilder.settings import Settings
from axbuilder.workspace import Workspace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from test_app import META, WS, hwpx_bytes, open_client, upload  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_loaders import make_pdf  # noqa: E402

LAW = "제1조(목적) 이 법은 예금자를 보호한다.\n제2조(정의) 용어의 뜻은 다음과 같다.\n"
meta = DocMeta(doc_id="law-1", title="예금자보호법", source="tests", doc_type="law")
product = meta.model_copy(update={"doc_id": "prod-1", "doc_type": "product"})


def write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("law.txt", LAW.encode()),
        ("law.md", LAW.encode()),
        ("law.hwpx", hwpx_bytes(LAW.splitlines())),
    ],
)
def test_formats_without_pages_have_no_chunk_page(tmp_path, name, content):
    doc = load_document(write(tmp_path, name, content), meta, "bank")
    assert doc.paged is False
    assert [p.number for p in doc.pages] == [1]  # 로더의 모양(Page 하나, 번호 1)은 그대로다
    chunks = chunk_document(doc)
    assert [c.article for c in chunks] == ["제1조", "제2조"]
    assert all(c.page is None for c in chunks)
    assert all(c.page is None for c in chunk_by_article(doc))
    assert all(c.page is None for c in chunk_fixed(doc, size=20, overlap=5))


def test_pdf_keeps_page_numbers_even_with_one_page(tmp_path):
    one = load_document(
        write(tmp_path, "one.pdf", make_pdf(["제1조(목적) 가\n제2조(정의) 나"])), meta, "bank"
    )
    assert one.paged is True
    assert [c.page for c in chunk_document(one)] == [1, 1]
    two = load_document(
        write(tmp_path, "two.pdf", make_pdf(["제1조(목적) 가", "제2조(정의) 나"])), meta, "bank"
    )
    assert [(c.article, c.page) for c in chunk_document(two)] == [("제1조", 1), ("제2조", 2)]


def test_documents_built_by_hand_are_paged_by_default():
    # 필드를 더했을 뿐이다: paged를 주지 않은 Document는 전과 같이 쪽 번호를 단다
    doc = Document(meta=product, workspace_id="bank", pages=[Page(number=3, text="가" * 30)])
    assert doc.paged is True
    assert [c.page for c in chunk_document(doc)] == [3]


def test_prompt_header_has_no_page_for_text_documents(tmp_path):
    workspace = Workspace(id="bank", name="은행", industry="f", roles={"a": ["public"]})
    text_doc = load_document(
        write(tmp_path, "prod.md", "예금 상품 설명서 본문".encode()), product, "bank"
    )
    pdf_doc = load_document(
        write(tmp_path, "prod.pdf", make_pdf(["", "예금 상품 설명서 본문"])),
        product.model_copy(update={"doc_id": "prod-2", "title": "PDF 설명서"}),
        "bank",
    )
    chunks = [*chunk_document(text_doc), chunk_document(pdf_doc)[-1]]
    _, user = build_prompt("질문", [ScoredChunk(chunk=c, score=1.0) for c in chunks], workspace)
    assert "[1] 예금자보호법\n예금 상품 설명서 본문" in user  # 쪽 없음: 제목만
    assert "[2] PDF 설명서 p.2\n" in user
    assert "p.1" not in user


def test_api_citations_and_v1_sources_have_no_page_for_text_documents(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    body = "AXB 든든 정기예금은 만기 일시 지급식 상품이다.".encode()
    doc_meta = {**META, "doc_id": "prod-1", "title": "정기예금 설명서", "doc_type": "product"}
    with open_client(settings, FakeLLM("기본금리는 연 2.80%입니다 [1].")) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        assert upload(client, "prod.md", body, doc_meta).status_code == 201
        assert client.post("/workspaces/bank/index").status_code == 200
        asked = client.post(
            "/workspaces/bank/ask", json={"question": "정기예금 금리", "role": "상담원"}
        ).json()
        assert asked["citations"][0]["page"] is None and asked["citations"][0]["article"] is None
        key = client.post("/workspaces/bank/keys", json={"name": "k", "role": "상담원"}).json()[
            "key"
        ]
        chat = client.post(
            "/v1/chat/completions",
            json={"model": "bank", "messages": [{"role": "user", "content": "정기예금 금리"}]},
            headers={"Authorization": f"Bearer {key}"},
        ).json()
    content = chat["choices"][0]["message"]["content"]
    assert content == "기본금리는 연 2.80%입니다 [1].\n\n출처\n[1] 정기예금 설명서"
    assert "p.1" not in content
