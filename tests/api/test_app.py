"""API 테스트: TestClient + 임시 data_dir + FakeLLM + HashEmbedder (네트워크·GPU·모델 없음).

앱은 항상 `with TestClient(app)`로 연다. 끝날 때 lifespan이 Qdrant 로컬 클라이언트를 닫아야
Windows에서 임시 폴더의 잠금 파일이 풀린다.
"""

import io
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

import axbuilder.api.app as app_module
from axbuilder.api.app import create_app
from axbuilder.generation.answer import REFUSAL_TEXT
from axbuilder.generation.llm import FakeLLM, LLMUnavailableError
from axbuilder.models import LLMResponse
from axbuilder.retrieval.dense import HashEmbedder
from axbuilder.settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LAW_TXT = (FIXTURES / "sample_law.txt").read_bytes()  # 제1조, 제2조, 제2조의2 → 청크 3개
WS = {
    "id": "bank",
    "name": "AXB은행(가상)",
    "industry": "finance",
    "roles": {"상담원": ["public"], "준법감시": ["public", "internal"], "견학생": []},
}
META = {
    "doc_id": "law-1",
    "title": "예금자보호법(발췌)",
    "source": "tests/fixtures/sample_law.txt",
    "doc_type": "law",
}
GUIDE_MD = (  # 조항 2개 → 청크 2개 (제목 줄을 두면 머리말 청크가 하나 더 생긴다)
    "제1조(목적) 이 지침은 상담원이 지켜야 할 내부 절차를 정한다. 내부전용표식XYZ\n"
    "제2조(확인) 상담원은 고객 본인 확인 후 상담한다. 내부전용표식XYZ\n"
).encode()
GUIDE_META = {
    "doc_id": "guide-1",
    "title": "상담 응대 지침",
    "source": "tests",
    "doc_type": "internal",
    "access_level": "internal",
}


class RecordingLLM(FakeLLM):
    """FakeLLM과 같되, 마지막 user 메시지(자료 + 질문)를 쉽게 보게 한다."""

    @property
    def last_user(self) -> str:
        return self.calls[-1][1]


class DownLLM:
    """generate()가 언제나 LLMUnavailableError를 던지는 가짜 (Ollama 꺼짐 등)."""

    def __init__(self, message: str) -> None:
        self.message = message

    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        raise LLMUnavailableError(self.message)


class CitingLLM:
    """프롬프트의 [자료] 번호를 읽어, 정해진 조항을 정해진 순서로 인용하는 가짜 LLM."""

    def __init__(self, articles: list[str]) -> None:
        self.articles = articles

    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        # 자료 머리 줄: "[{번호}] {title} {article}"
        number_of = {
            m.group(2): m.group(1) for m in re.finditer(r"^\[(\d+)\] .* (제\S+)$", user, re.M)
        }
        text = " ".join(f"{article} 내용 [{number_of[article]}]." for article in self.articles)
        return LLMResponse(
            text=text, model="citing", input_tokens=0, output_tokens=0, latency_ms=0.0
        )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM("보호한도는 대통령령으로 정합니다 [3].")


def open_client(settings: Settings, llm, embedder=None, **kwargs) -> TestClient:
    app = create_app(settings, llm=llm, embedder=embedder or HashEmbedder())
    return TestClient(app, **kwargs)


@pytest.fixture
def client(settings, llm):
    with open_client(settings, llm) as c:
        yield c


@pytest.fixture
def client_with_down_llm(settings):
    down = DownLLM(
        "Ollama 서버(http://localhost:11434)에 연결할 수 없습니다. "
        "Ollama가 실행 중인지 확인하세요: ollama serve"
    )
    with open_client(settings, down) as c:
        yield c


def upload(client: TestClient, name: str, content: bytes, meta: dict | None = None, ws="bank"):
    return client.post(
        f"/workspaces/{ws}/documents", files={"file": (name, content)}, data=meta or META
    )


def setup_indexed(client: TestClient, *docs: tuple[str, bytes, dict]) -> int:
    """워크스페이스를 만들고 문서를 올린 뒤 색인한다. 청크 수를 돌려준다."""
    assert client.post("/workspaces", json=WS).status_code == 201
    for name, content, meta in docs or [("law.txt", LAW_TXT, META)]:
        assert upload(client, name, content, meta).status_code == 201
    r = client.post("/workspaces/bank/index")
    assert r.status_code == 200
    return r.json()["chunks"]


def ask(client: TestClient, question="보호 한도?", role="상담원", ws="bank"):
    return client.post(f"/workspaces/{ws}/ask", json={"question": question, "role": role})


def hwpx_bytes(paragraphs: list[str]) -> bytes:
    hs = "http://www.hancom.co.kr/hwpml/2011/section"
    hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    body = "".join(f"<hp:p><hp:run><hp:t>{escape(t)}</hp:t></hp:run></hp:p>" for t in paragraphs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><hs:sec xmlns:hs="{hs}" xmlns:hp="{hp}">{body}'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("Contents/section0.xml", xml + "</hs:sec>")
    return buffer.getvalue()


def files_under(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# ---- 계획의 테스트 (완료 조건 명세) ----


def test_end_to_end_ask(client):
    client.post("/workspaces", json=WS)
    client.post("/workspaces/bank/documents", files={"file": ("law.txt", LAW_TXT)}, data=META)
    assert client.post("/workspaces/bank/index").json()["chunks"] == 3
    r = client.post("/workspaces/bank/ask", json={"question": "보호 한도?", "role": "상담원"})
    assert r.status_code == 200 and r.json()["citations"]


def test_upload_unsupported_type_415(client):
    client.post("/workspaces", json=WS)
    r = upload(client, "setup.exe", b"MZ\x90\x00\x03\x00\x00\x00")
    assert r.status_code == 415


def test_ask_unknown_role_400(client):
    setup_indexed(client)
    r = ask(client, role="해커")
    assert r.status_code == 400
    assert "상담원" in r.json()["detail"]  # 고를 수 있는 역할을 알려 준다


@pytest.mark.parametrize("question", ["", "   \n\t"])
def test_ask_empty_question_422(client, question):
    setup_indexed(client)
    assert ask(client, question=question).status_code == 422


def test_ask_returns_503_when_llm_down(client_with_down_llm):
    setup_indexed(client_with_down_llm)
    r = ask(client_with_down_llm)
    assert r.status_code == 503
    assert "ollama serve" in r.json()["detail"]  # 해결 방법은 그대로 알려 준다


# ---- 추가: 상태 코드와 응답 모양 ----


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_create_workspace_201_then_409(client, settings):
    r = client.post("/workspaces", json=WS)
    assert r.status_code == 201 and r.json() == WS
    assert (settings.data_dir / "workspaces" / "bank" / "workspace.yaml").is_file()
    assert client.post("/workspaces", json=WS).status_code == 409


@pytest.mark.parametrize("bad", ["../etc", "Bank", "a", "x" * 33, "a/b", "con"])
def test_create_workspace_invalid_id_422(client, settings, bad):
    assert client.post("/workspaces", json={**WS, "id": bad}).status_code == 422
    assert not (settings.data_dir / "workspaces").exists()


def test_ask_response_shape(client):
    setup_indexed(client)
    r = ask(client)
    body = r.json()
    assert set(body) == {"answer", "citations", "refused", "latency_ms"}
    assert body["refused"] is False and body["latency_ms"] >= 0
    assert body["answer"] == "보호한도는 대통령령으로 정합니다 [1]."
    assert set(body["citations"][0]) == {"chunk_id", "doc_id", "title", "page", "article"}
    assert body["citations"][0]["doc_id"] == "law-1"


def test_ask_citation_numbers_follow_citations_order(settings):
    # PR #13의 계약: 응답 answer 본문의 [i]는 citations[i-1]. 자료 번호가 아니라 인용 순서다
    with open_client(settings, CitingLLM(["제2조의2", "제1조"])) as client:
        setup_indexed(client)
        body = ask(client).json()
    assert body["answer"] == "제2조의2 내용 [1]. 제1조 내용 [2]."
    assert [c["article"] for c in body["citations"]] == ["제2조의2", "제1조"]


def test_ask_refusal(settings):
    with open_client(settings, FakeLLM(REFUSAL_TEXT)) as client:
        setup_indexed(client)
        body = ask(client, question="신용카드 연회비는?").json()
    assert body["refused"] is True and body["citations"] == [] and body["answer"] == REFUSAL_TEXT


def test_ask_before_index_409(client):
    client.post("/workspaces", json=WS)
    upload(client, "law.txt", LAW_TXT)
    r = ask(client)
    assert r.status_code == 409 and "index" in r.json()["detail"]


def test_unknown_workspace_404(client):
    assert upload(client, "law.txt", LAW_TXT).status_code == 404
    assert client.post("/workspaces/bank/index").status_code == 404
    assert ask(client).status_code == 404


@pytest.mark.parametrize("bad", ["%2E%2E", "..%5C..%5Cetc", "con", "BANK", "a", "x" * 33])
def test_invalid_workspace_id_in_path_404(client, settings, bad):
    client.post("/workspaces", json=WS)
    assert upload(client, "law.txt", LAW_TXT, ws=bad).status_code == 404
    assert client.post(f"/workspaces/{bad}/index").status_code == 404
    assert ask(client, ws=bad).status_code == 404
    assert files_under(settings.data_dir) == {"workspaces/bank/workspace.yaml"}


# ---- 추가: 권한 필터 ----


def test_role_filter_applies_before_llm(client, llm):
    # 볼 수 없는 문서는 검색 단계에서 빠져 LLM 프롬프트에도 들어가지 않는다
    setup_indexed(client, ("law.txt", LAW_TXT, META), ("guide.md", GUIDE_MD, GUIDE_META))
    assert ask(client, role="상담원").status_code == 200
    assert "내부전용표식XYZ" not in llm.last_user
    assert ask(client, role="준법감시").status_code == 200
    assert "내부전용표식XYZ" in llm.last_user


def test_role_without_access_refuses_without_llm(client, llm):
    setup_indexed(client)
    body = ask(client, role="견학생").json()
    assert body["refused"] is True and llm.calls == []


# ---- 추가: 업로드 안전 ----


def test_upload_stores_by_doc_id_not_client_filename(client, settings):
    client.post("/workspaces", json=WS)
    r = upload(client, "..\\..\\..\\evil name.TXT", LAW_TXT)
    assert r.status_code == 201 and r.json() == {"doc_id": "law-1"}
    assert files_under(settings.data_dir) == {
        "workspaces/bank/workspace.yaml",
        "workspaces/bank/documents/law-1.txt",
        "workspaces/bank/documents/law-1.json",
    }
    assert not any("evil" in name for name in files_under(settings.data_dir.parent))


@pytest.mark.parametrize("doc_id", ["../x", "a/b", "a\\b", "Law", "con", "x" * 65, "", "-a"])
def test_upload_rejects_bad_doc_id_422(client, settings, doc_id):
    client.post("/workspaces", json=WS)
    assert upload(client, "law.txt", LAW_TXT, {**META, "doc_id": doc_id}).status_code == 422
    assert files_under(settings.data_dir) == {"workspaces/bank/workspace.yaml"}


def test_upload_missing_meta_field_422(client):
    client.post("/workspaces", json=WS)
    meta = {k: v for k, v in META.items() if k != "title"}
    assert upload(client, "law.txt", LAW_TXT, meta).status_code == 422


def test_upload_duplicate_doc_id_409(client):
    client.post("/workspaces", json=WS)
    assert upload(client, "law.txt", LAW_TXT).status_code == 201
    assert upload(client, "law.md", b"# other\n\ntext").status_code == 409


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("law.pdf", b"this is not a pdf"),  # 확장자만 PDF
        ("terms.hwpx", b"%PDF-1.7\n"),  # PK 서명이 아님
        ("notes.txt", b"abc\x00def"),  # NUL 바이트: 텍스트가 아니다
        ("setup.exe", b"MZ"),
        ("noext", b"text"),
    ],
)
def test_upload_signature_or_type_mismatch_415(client, settings, name, content):
    client.post("/workspaces", json=WS)
    assert upload(client, name, content).status_code == 415
    assert files_under(settings.data_dir) == {"workspaces/bank/workspace.yaml"}


def test_upload_empty_file_422(client, settings):
    client.post("/workspaces", json=WS)
    assert upload(client, "law.txt", b"").status_code == 422
    assert files_under(settings.data_dir) == {"workspaces/bank/workspace.yaml"}


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("law.pdf", b"%PDF-1.7\n%broken body without objects"),
        ("terms.hwpx", b"PK\x03\x04 broken zip"),
        ("notes.txt", "가나다".encode() + b"\xff\xfe\xfd"),  # UTF-8도 CP949도 아님
    ],
)
def test_upload_broken_file_422_without_internals(client, settings, tmp_path, name, content):
    client.post("/workspaces", json=WS)
    r = upload(client, name, content)
    assert r.status_code == 422
    text = r.text
    assert "Traceback" not in text and str(tmp_path) not in text and "upload-" not in text
    assert files_under(settings.data_dir) == {"workspaces/bank/workspace.yaml"}


def test_upload_hwpx_without_sections_422(client):
    client.post("/workspaces", json=WS)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
    assert upload(client, "terms.hwpx", buffer.getvalue()).status_code == 422


def test_upload_without_text_422(client):
    # 텍스트를 뽑을 수 없는 문서(공백뿐, 스캔본 PDF 등)는 색인해도 청크가 0개라 받지 않는다
    client.post("/workspaces", json=WS)
    assert upload(client, "blank.txt", b"  \n\n \t").status_code == 422


def test_upload_hwpx_and_md(client):
    terms = hwpx_bytes(["제1조(목적) 이 약관은 예금거래를 정한다.", "제2조(정의) 예금이란 …"])
    meta = {**META, "doc_id": "terms-1", "title": "예금거래기본약관", "doc_type": "terms"}
    md = b"# \xec\x83\x81\xed\x92\x88\n\n| \xea\xb8\xb0\xea\xb0\x84 | \xea\xb8\x88\xeb\xa6\xac |\n"
    product = {**META, "doc_id": "prod-1", "title": "상품설명서", "doc_type": "product"}
    n = setup_indexed(client, ("terms.HWPX", terms, meta), ("prod.md", md, product))
    assert n == 3  # 약관 조항 2개 + 상품설명서 고정 길이 1개


def test_upload_too_large_413(monkeypatch, settings, llm):
    # 실제로 읽은 바이트로 판단한다(엔드포인트에서 1MB씩 복사하며 센다)
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 1024)
    with open_client(settings, llm) as client:
        client.post("/workspaces", json=WS)
        assert upload(client, "law.txt", b"a" * 1024).status_code == 201
        r = upload(client, "law.txt", b"a" * 1025, {**META, "doc_id": "law-2"})
        assert r.status_code == 413
    assert "workspaces/bank/documents/law-2.txt" not in files_under(settings.data_dir)


def test_request_body_limit_ignores_content_length(monkeypatch, settings, llm):
    # 본문 전체 상한(파일 + 양식 여유분)은 Content-Length가 아니라 받은 바이트로 센다
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 1024)
    monkeypatch.setattr(app_module, "UPLOAD_OVERHEAD_BYTES", 1024)
    with open_client(settings, llm) as client:
        client.post("/workspaces", json=WS)
        body = b"--b\r\n" + b"x" * 5000
        r = client.post(
            "/workspaces/bank/documents",
            content=body,
            headers={"Content-Type": "multipart/form-data; boundary=b", "Content-Length": "10"},
        )
        assert r.status_code == 413
        assert upload(client, "law.txt", b"a" * 5000).status_code == 413
    assert files_under(settings.data_dir) == {"workspaces/bank/workspace.yaml"}


# ---- 추가: 색인 캐시·재색인·재시작 ----


def test_index_unknown_workspace_404_and_empty_index(client):
    assert client.post("/workspaces/bank/index").status_code == 404
    client.post("/workspaces", json=WS)
    r = client.post("/workspaces/bank/index")
    assert r.status_code == 200 and r.json() == {"chunks": 0}
    body = ask(client).json()  # 색인은 있지만 비어 있으면 검색 결과가 없어 거절
    assert body["refused"] is True


def test_reindex_reopens_qdrant_and_picks_up_new_documents(client, settings, llm):
    assert setup_indexed(client) == 3
    assert (settings.data_dir / "workspaces" / "bank" / "index" / "qdrant").is_dir()
    assert upload(client, "guide.md", GUIDE_MD, GUIDE_META).status_code == 201
    # 같은 Qdrant 폴더를 다시 연다: 이전 클라이언트를 닫지 않으면 잠금 때문에 실패한다
    assert client.post("/workspaces/bank/index").json() == {"chunks": 5}
    assert ask(client, role="준법감시").status_code == 200
    assert "내부전용표식XYZ" in llm.last_user


def test_index_survives_restart(settings):
    # 재시작하면 메모리 캐시는 비지만, 폴더의 Qdrant 색인을 열고 그 청크로 BM25를 다시 만든다.
    # 같은 질문에 같은 자료(같은 순서)가 프롬프트에 들어가면 두 검색기가 그대로 복원된 것이다
    first = RecordingLLM("내용 [1].")
    with open_client(settings, first) as client:
        setup_indexed(client, ("law.txt", LAW_TXT, META), ("guide.md", GUIDE_MD, GUIDE_META))
        assert ask(client, role="준법감시").status_code == 200
    second = RecordingLLM("내용 [1].")
    with open_client(settings, second) as client:
        r = ask(client, role="준법감시")
        assert r.status_code == 200 and r.json()["citations"]
    assert second.last_user == first.last_user


def test_restart_with_different_embedder_needs_reindex(settings, llm):
    with open_client(settings, llm, HashEmbedder(dim=64)) as client:
        setup_indexed(client)
    with open_client(settings, llm, HashEmbedder(dim=32)) as client:
        r = ask(client)
        assert r.status_code == 409 and "index" in r.json()["detail"]
        assert client.post("/workspaces/bank/index").json() == {"chunks": 3}
        assert ask(client).status_code == 200


# ---- 추가: 오류 응답에 내부 정보를 넣지 않는다 ----


def test_llm_error_detail_hides_local_paths(settings):
    down = DownLLM(
        "Claude API 인증 정보를 쓸 수 없습니다(Config file at "
        "C:\\Users\\hwan\\.config\\anthropic\\configs\\default.json could not be read; "
        "see /home/hwan/.config/anthropic/credentials/default.json). "
        "`ant auth login`으로 다시 로그인하거나 ANTHROPIC_API_KEY를 설정하세요"
    )
    with open_client(settings, down) as client:
        setup_indexed(client)
        r = ask(client)
    detail = r.json()["detail"]
    assert r.status_code == 503 and "ant auth login" in detail
    assert "C:\\Users" not in detail and "/home/hwan" not in detail and "hwan" not in detail


def test_internal_error_500_without_traceback(settings, llm, tmp_path):
    with open_client(settings, llm, raise_server_exceptions=False) as client:
        setup_indexed(client)
        config = settings.data_dir / "workspaces" / "bank" / "workspace.yaml"
        config.write_text("id: other\nname: x\nindustry: x\nroles: {}\n", encoding="utf-8")
        r = ask(client)
    assert r.status_code == 500
    assert "Traceback" not in r.text and str(tmp_path) not in r.text and "other" not in r.text
