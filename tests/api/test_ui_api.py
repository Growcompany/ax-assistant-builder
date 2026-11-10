"""화면(Task 14)이 쓰는 조회 API·예시 워크스페이스·정적 파일 제공 테스트.

정적 파일의 내용(화면 자체)은 tests/api/test_static_ui.py가 본다. 여기서는 index.html의
내용에 기대지 않고 상태 코드와 머리글만 본다.
"""

import importlib.resources
import json
import mimetypes
from pathlib import Path

import pytest
from test_app import (
    GUIDE_MD,
    GUIDE_META,
    LAW_TXT,
    META,
    WS,
    CitingLLM,
    FlakyEmbedder,
    NamedEmbedder,
    RecordingLLM,
    ask,
    files_under,
    open_client,
    setup_indexed,
    upload,
)

from axbuilder.api.app import CONTENT_SECURITY_POLICY
from axbuilder.generation.answer import REFUSAL_TEXT
from axbuilder.generation.llm import FakeLLM
from axbuilder.models import LLMResponse
from axbuilder.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
CSP = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
DEMO_SOURCE = """\
- doc_id: {doc_id}
  title: 문서 {doc_id}
  source: tests
  doc_type: product
  access_level: public
  path: corpus/bank/synthetic/{doc_id}.md
  format: md
  license: test
"""


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM("보호한도는 대통령령으로 정합니다 [3].")


@pytest.fixture
def client(settings, llm):
    with open_client(settings, llm) as c:
        yield c


def index_state(client, ws="bank") -> dict:
    return client.get(f"/workspaces/{ws}").json()["index"]


def indexed_flags(client, ws="bank") -> dict[str, bool]:
    documents = client.get(f"/workspaces/{ws}").json()["documents_list"]
    return {d["doc_id"]: d["indexed"] for d in documents}


def index_info_path(settings: Settings) -> Path:
    return settings.data_dir / "workspaces" / "bank" / "index" / "index.json"


# ---- GET /workspaces ----


def test_list_workspaces_empty(client):
    r = client.get("/workspaces")
    assert r.status_code == 200 and r.json() == []


def test_list_workspaces_sorted_by_id_with_summary_fields(client):
    assert client.post("/workspaces", json={**WS, "id": "zeta"}).status_code == 201
    assert client.post("/workspaces", json={**WS, "id": "alpha", "name": "알파"}).status_code == 201
    assert upload(client, "law.txt", LAW_TXT, ws="zeta").status_code == 201
    body = client.get("/workspaces").json()
    assert [w["id"] for w in body] == ["alpha", "zeta"]
    assert body[0] == {
        "id": "alpha",
        "name": "알파",
        "industry": "finance",
        "roles": WS["roles"],
        "documents": 0,
        "index": {"state": "none", "chunks": None},
    }
    assert body[1]["documents"] == 1 and body[1]["index"] == {"state": "none", "chunks": None}


def test_index_state_none_ready_stale_ready(client):
    assert client.post("/workspaces", json=WS).status_code == 201
    assert upload(client, "law.txt", LAW_TXT).status_code == 201

    def summary() -> dict:
        (only,) = client.get("/workspaces").json()
        return only

    assert summary()["index"] == {"state": "none", "chunks": None}
    assert client.post("/workspaces/bank/index").json() == {"chunks": 3}
    assert summary()["index"] == {"state": "ready", "chunks": 3}
    assert upload(client, "guide.md", GUIDE_MD, GUIDE_META).status_code == 201
    # 색인 뒤에 올린 문서: 청크 수는 지금 색인의 것 그대로
    assert summary()["index"] == {"state": "stale", "chunks": 3}
    assert summary()["documents"] == 2
    assert client.post("/workspaces/bank/index").json() == {"chunks": 5}
    assert summary()["index"] == {"state": "ready", "chunks": 5}


def test_empty_index_is_ready(client):
    # 문서 없이 색인해도 "색인 뒤 추가된 문서 없음"이다
    assert client.post("/workspaces", json=WS).status_code == 201
    assert client.post("/workspaces/bank/index").json() == {"chunks": 0}
    assert index_state(client) == {"state": "ready", "chunks": 0}


def test_index_state_survives_restart(settings, llm):
    with open_client(settings, llm) as client:
        setup_indexed(client)
    with open_client(settings, llm) as client:  # 색인을 메모리에 올리기 전에도 파일만으로 판정
        assert index_state(client) == {"state": "ready", "chunks": 3}


@pytest.mark.parametrize(
    "rewrite",
    [
        lambda info: info.pop("documents"),  # 예전 형식: 색인에 든 문서 목록이 없다
        lambda info: info.update(documents="law-1"),  # 목록이 아니다
        lambda info: info.update(documents=[1]),
    ],
)
def test_index_info_without_document_list_is_stale(client, settings, rewrite):
    # 무엇이 색인됐는지 모르면 ready라고 하지 않는다(다시 색인하라고 하는 쪽이 안전)
    setup_indexed(client)
    path = index_info_path(settings)
    info = json.loads(path.read_text(encoding="utf-8"))
    rewrite(info)
    path.write_text(json.dumps(info), encoding="utf-8")
    assert index_state(client) == {"state": "stale", "chunks": 3}
    assert indexed_flags(client) == {"law-1": False}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"documents": ["law-1"]}'])
def test_unreadable_index_info_is_stale_not_500(client, settings, content):
    setup_indexed(client)
    index_info_path(settings).write_text(content, encoding="utf-8")
    r = client.get("/workspaces/bank")
    assert r.status_code == 200 and r.json()["index"]["state"] == "stale"
    assert client.get("/workspaces").status_code == 200


def test_index_of_another_embedder_is_stale(settings, llm):
    # 질문하면 409(다시 색인)인 색인을 화면이 "준비됨"으로 보여 주지 않는다
    with open_client(settings, llm, NamedEmbedder("model-a")) as client:
        setup_indexed(client)
        assert index_state(client)["state"] == "ready"
    with open_client(settings, llm, NamedEmbedder("model-b")) as client:
        assert index_state(client) == {"state": "stale", "chunks": 3}
        assert ask(client).status_code == 409


def test_failed_reindex_keeps_stale_state(settings, llm):
    # PR #19: 임베딩에서 실패하면 index.json이 그대로다. 새 문서는 여전히 색인 밖이므로 stale
    embedder = FlakyEmbedder()
    with open_client(settings, llm, embedder, raise_server_exceptions=False) as client:
        setup_indexed(client)
        assert upload(client, "guide.md", GUIDE_MD, GUIDE_META).status_code == 201
        embedder.failing = True
        assert client.post("/workspaces/bank/index").status_code == 500
        assert index_state(client) == {"state": "stale", "chunks": 3}
        assert indexed_flags(client) == {"guide-1": False, "law-1": True}


# ---- GET /workspaces/{id} ----


def test_workspace_detail_lists_documents(client):
    assert client.post("/workspaces", json=WS).status_code == 201
    assert upload(client, "z.md", GUIDE_MD, {**GUIDE_META, "doc_id": "zz-guide"}).status_code == 201
    assert upload(client, "원본 이름.TXT", LAW_TXT).status_code == 201
    r = client.get("/workspaces/bank")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "name", "industry", "roles", "documents", "index", "documents_list"}
    assert body["id"] == "bank" and body["name"] == WS["name"] and body["roles"] == WS["roles"]
    assert body["documents"] == 2 and body["index"] == {"state": "none", "chunks": None}
    assert body["documents_list"] == [  # doc_id 오름차순
        {
            "doc_id": "law-1",
            "title": "예금자보호법(발췌)",
            "source": "tests/fixtures/sample_law.txt",
            "doc_type": "law",
            "access_level": "public",
            "extension": ".txt",
            "size_bytes": len(LAW_TXT),
            "indexed": False,
        },
        {
            "doc_id": "zz-guide",
            "title": "상담 응대 지침",
            "source": "tests",
            "doc_type": "internal",
            "access_level": "internal",
            "extension": ".md",
            "size_bytes": len(GUIDE_MD),
            "indexed": False,
        },
    ]


def test_workspace_detail_indexed_flag(client):
    setup_indexed(client)
    assert indexed_flags(client) == {"law-1": True}
    assert upload(client, "guide.md", GUIDE_MD, GUIDE_META).status_code == 201
    assert indexed_flags(client) == {"guide-1": False, "law-1": True}
    assert client.post("/workspaces/bank/index").status_code == 200
    assert indexed_flags(client) == {"guide-1": True, "law-1": True}


def test_workspace_detail_does_not_expose_storage_details(client):
    setup_indexed(client)
    text = client.get("/workspaces/bank").text
    assert "sha256" not in text and "uploaded_at" not in text


def test_workspace_detail_unknown_404(client):
    r = client.get("/workspaces/nope")
    assert r.status_code == 404 and r.json() == {"detail": "워크스페이스를 찾을 수 없습니다"}


@pytest.mark.parametrize("bad", ["%2e%2e", "..%5Cetc", "con", "A", "BANK", "a" * 33])
def test_workspace_detail_invalid_id_404(client, bad):
    # "/workspaces/.."은 httpx가 보내기 전에 "/"로 정리하므로 인코딩한 것으로 본다
    assert client.post("/workspaces", json=WS).status_code == 201
    assert client.get(f"/workspaces/{bad}").status_code == 404


def test_read_routes_reject_foreign_host(client):
    assert client.post("/workspaces", json=WS).status_code == 201
    for path in ("/workspaces", "/workspaces/bank"):
        assert client.get(path, headers={"host": "attacker.example"}).status_code == 403


# ---- ask의 passages ----


def test_ask_passages_match_citations(settings):
    with open_client(settings, CitingLLM(["제2조의2", "제1조"])) as client:
        setup_indexed(client)
        body = ask(client).json()
    assert [c["article"] for c in body["citations"]] == ["제2조의2", "제1조"]
    assert len(body["passages"]) == len(body["citations"]) == 2
    assert all(set(p) == {"chunk_id", "text"} for p in body["passages"])
    assert [p["chunk_id"] for p in body["passages"]] == [c["chunk_id"] for c in body["citations"]]
    assert "보호한도는 대통령령으로 정한다" in body["passages"][0]["text"]
    assert "예금자를 보호함을 목적으로 한다" in body["passages"][1]["text"]
    assert "보호한도" not in body["passages"][1]["text"]


def test_ask_passages_after_restart(settings):
    # 재시작 뒤에는 Qdrant에서 다시 읽은 청크로 답한다. 그때도 원문이 같이 온다
    with open_client(settings, CitingLLM(["제1조"])) as client:
        setup_indexed(client)
    with open_client(settings, CitingLLM(["제1조"])) as client:
        body = ask(client).json()
    assert [p["chunk_id"] for p in body["passages"]] == [body["citations"][0]["chunk_id"]]
    assert "예금자를 보호함을 목적으로 한다" in body["passages"][0]["text"]


class CiteEverythingLLM:
    """프롬프트에 들어온 자료를 전부 인용한다(볼 수 있는 것은 다 응답에 나오게)."""

    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        text = " ".join(f"내용 [{n}]." for n in range(1, 21))
        return LLMResponse(text=text, model="all", input_tokens=0, output_tokens=0, latency_ms=0.0)


def test_public_role_passages_have_no_internal_text(settings):
    with open_client(settings, CiteEverythingLLM()) as client:
        setup_indexed(client, ("law.txt", LAW_TXT, META), ("guide.md", GUIDE_MD, GUIDE_META))
        public = ask(client, question="상담원 내부 절차 확인", role="상담원")
        internal = ask(client, question="상담원 내부 절차 확인", role="준법감시")
    assert public.status_code == 200 and public.json()["passages"]
    assert "내부전용표식XYZ" not in public.text
    assert all(p["chunk_id"].startswith("law-1::") for p in public.json()["passages"])
    # 같은 질문을 볼 수 있는 역할이 하면 내부 문서 원문이 온다(위 검사가 헛돌지 않는다)
    assert any("내부전용표식XYZ" in p["text"] for p in internal.json()["passages"])


def test_refusal_has_no_passages(settings):
    with open_client(settings, FakeLLM(REFUSAL_TEXT)) as client:
        setup_indexed(client)
        body = ask(client, question="신용카드 연회비는?").json()
    assert body["refused"] is True and body["citations"] == [] and body["passages"] == []


def test_role_without_access_has_no_passages(client):
    setup_indexed(client)
    body = ask(client, role="견학생").json()
    assert body["refused"] is True and body["passages"] == []


def test_answer_without_citations_has_no_passages(settings):
    # 인용 없이 답하면 검색된 청크가 있어도 원문을 싣지 않는다(passages는 citations의 원문)
    with open_client(settings, FakeLLM("인용 없는 답입니다.")) as client:
        setup_indexed(client)
        body = ask(client).json()
    assert body["refused"] is False and body["citations"] == [] and body["passages"] == []


# ---- POST /demo-workspace ----


def write_demo_corpus(root: Path, bodies: dict[str, str]) -> None:
    """root/corpus/bank/에 sources.yaml과 가상 문서를 만든다 (doc_id → 본문)."""
    folder = root / "corpus" / "bank" / "synthetic"
    folder.mkdir(parents=True, exist_ok=True)
    for doc_id, body in bodies.items():
        (folder / f"{doc_id}.md").write_bytes(body.encode())
    listing = "".join(DEMO_SOURCE.format(doc_id=doc_id) for doc_id in bodies)
    (root / "corpus" / "bank" / "sources.yaml").write_text(listing, encoding="utf-8")


def test_demo_workspace_registers_repository_documents(monkeypatch, client):
    monkeypatch.chdir(REPO_ROOT)
    r = client.post("/demo-workspace")
    assert r.status_code == 201 and r.json() == {"id": "demo", "documents": 6}
    body = client.get("/workspaces/demo").json()
    assert body["name"] == "AXB은행(가상)"
    assert body["roles"] == {"상담원": ["public"], "준법감시": ["public", "internal"]}
    assert body["documents"] == 6 and body["index"] == {"state": "none", "chunks": None}
    levels = [d["access_level"] for d in body["documents_list"]]
    assert levels.count("public") == 3 and levels.count("internal") == 3
    assert {d["doc_id"] for d in body["documents_list"]} == {
        "guide-complaints",
        "guide-counseling",
        "guide-elderly-customers",
        "prod-fixed-deposit",
        "prod-free-savings",
        "prod-mortgage-loan",
    }
    assert all(d["extension"] == ".md" and d["size_bytes"] > 0 for d in body["documents_list"])
    assert all(d["indexed"] is False for d in body["documents_list"])


def test_demo_workspace_can_be_indexed_and_asked(monkeypatch, settings):
    monkeypatch.chdir(REPO_ROOT)
    with open_client(settings, RecordingLLM("내용 [1].")) as client:
        assert client.post("/demo-workspace").status_code == 201
        assert client.post("/workspaces/demo/index").json()["chunks"] > 6
        assert index_state(client, "demo")["state"] == "ready"
        r = ask(client, question="정기예금 기본금리", role="상담원", ws="demo")
        assert r.status_code == 200 and r.json()["passages"]


def test_demo_workspace_second_time_409(monkeypatch, client):
    monkeypatch.chdir(REPO_ROOT)
    assert client.post("/demo-workspace").status_code == 201
    r = client.post("/demo-workspace")
    assert r.status_code == 409 and "demo" in r.json()["detail"]
    assert client.get("/workspaces/demo").json()["documents"] == 6  # 그대로


def test_demo_workspace_409_when_id_taken_keeps_existing(monkeypatch, client):
    monkeypatch.chdir(REPO_ROOT)
    assert client.post("/workspaces", json={**WS, "id": "demo", "name": "내 것"}).status_code == 201
    assert upload(client, "law.txt", LAW_TXT, ws="demo").status_code == 201
    assert client.post("/demo-workspace").status_code == 409
    body = client.get("/workspaces/demo").json()
    assert body["name"] == "내 것" and [d["doc_id"] for d in body["documents_list"]] == ["law-1"]


def test_demo_workspace_without_corpus_404(monkeypatch, client, settings, tmp_path):
    monkeypatch.chdir(tmp_path)  # corpus/bank/sources.yaml이 없는 곳
    r = client.post("/demo-workspace")
    assert r.status_code == 404
    assert tmp_path.name not in r.text  # 서버 경로를 응답에 넣지 않는다
    assert client.get("/workspaces").json() == []
    assert not (settings.data_dir / "workspaces" / "demo").exists()


def test_demo_workspace_with_missing_document_file_404(monkeypatch, client, settings, tmp_path):
    write_demo_corpus(tmp_path, {"doc-a": "제1조(목적) 본문.\n", "doc-b": "제1조(목적) 본문.\n"})
    (tmp_path / "corpus" / "bank" / "synthetic" / "doc-b.md").unlink()
    monkeypatch.chdir(tmp_path)
    assert client.post("/demo-workspace").status_code == 404
    assert client.get("/workspaces").json() == []
    assert not (settings.data_dir / "workspaces" / "demo").exists()


def test_demo_workspace_without_repository_documents_404(monkeypatch, client, tmp_path):
    write_demo_corpus(tmp_path, {})
    (tmp_path / "corpus" / "bank" / "sources.yaml").write_text(
        "- doc_id: law\n  title: 법\n  source: s\n  doc_type: law\n"
        "  url: https://example.invalid/law\n  format: html\n  license: l\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert client.post("/demo-workspace").status_code == 404  # 받아 오지도, 만들지도 않는다
    assert client.get("/workspaces").json() == []


def test_demo_workspace_failure_leaves_nothing_and_can_be_retried(
    monkeypatch, settings, llm, tmp_path
):
    # 두 번째 문서가 업로드 검증(빈 파일)에 걸린다. 반쯤 만든 워크스페이스가 남으면 다시 눌러도
    # 409라 고칠 길이 없다. 만든 것을 모두 지워, 고친 뒤 다시 누르면 처음부터 만들어지게 한다
    write_demo_corpus(tmp_path, {"doc-a": "제1조(목적) 본문.\n", "doc-b": ""})
    monkeypatch.chdir(tmp_path)
    with open_client(settings, llm, raise_server_exceptions=False) as client:
        r = client.post("/demo-workspace")
        assert r.status_code == 500 and "doc-b" in r.json()["detail"]
        assert tmp_path.name not in r.text
        assert client.get("/workspaces").json() == []
        assert client.get("/workspaces/demo").status_code == 404
        assert not (settings.data_dir / "workspaces" / "demo").exists()

        write_demo_corpus(tmp_path, {"doc-a": "제1조(목적) 본문.\n", "doc-b": "제1조(목적) 둘.\n"})
        r = client.post("/demo-workspace")
        assert r.status_code == 201 and r.json() == {"id": "demo", "documents": 2}
        assert client.get("/workspaces/demo").json()["documents"] == 2


def test_demo_workspace_uses_upload_validation(monkeypatch, settings, llm, tmp_path):
    # 업로드와 같은 경로: 텍스트 파일에 NUL이 있으면 받지 않는다(서명 검사)
    write_demo_corpus(tmp_path, {"doc-a": "제1조(목적) 본문.\x00\n"})
    monkeypatch.chdir(tmp_path)
    with open_client(settings, llm, raise_server_exceptions=False) as client:
        assert client.post("/demo-workspace").status_code == 500
        assert client.get("/workspaces").json() == []


def test_demo_workspace_stores_like_an_upload(monkeypatch, client, settings, tmp_path):
    write_demo_corpus(tmp_path, {"doc-a": "제1조(목적) 본문.\n"})
    monkeypatch.chdir(tmp_path)
    assert client.post("/demo-workspace").status_code == 201
    assert files_under(settings.data_dir / "workspaces" / "demo") == {
        "workspace.yaml",
        "documents/doc-a.md",
        "documents/doc-a.json",
    }


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "attacker.example"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
        {"origin": "https://attacker.example"},
    ],
)
def test_demo_workspace_rejects_foreign_host_and_cross_site(monkeypatch, client, headers):
    monkeypatch.chdir(REPO_ROOT)
    assert client.post("/demo-workspace", headers=headers).status_code == 403
    assert client.get("/workspaces").json() == []


def test_demo_workspace_allows_same_origin_browser_request(monkeypatch, client):
    monkeypatch.chdir(REPO_ROOT)
    headers = {"sec-fetch-site": "same-origin", "origin": "http://127.0.0.1"}
    assert client.post("/demo-workspace", headers=headers).status_code == 201


# ---- 정적 파일 ----


def assert_ui_headers(response) -> None:
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"


def test_csp_constant_matches_the_plan():
    assert CONTENT_SECURITY_POLICY == CSP


def test_root_serves_index_html_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert_ui_headers(r)


def test_static_file_has_security_headers(client):
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert_ui_headers(r)
    assert r.content == client.get("/").content


def test_static_missing_file_404_with_security_headers(client):
    r = client.get("/static/no-such-file.js")
    assert r.status_code == 404
    assert_ui_headers(r)


@pytest.mark.parametrize(
    "path",
    [
        "/static/../app.py",
        "/static/%2e%2e/app.py",
        "/static/..%2fapp.py",
        "/static/..%5capp.py",
        "/static/%2e%2e/%2e%2e/workspace.py",
        "/static//app.py",
        "/static/js/../../app.py",
        "/static/C:/Windows/win.ini",
        "/static/Z:/x.txt",
        "/static/%5c%5cserver%5cshare%5cx.txt",
    ],
)
def test_static_path_traversal_404(settings, llm, path):
    # httpx는 보내기 전에 "/../"를 정리하므로, 정리되지 않는 인코딩된 경로도 함께 본다
    with open_client(settings, llm, raise_server_exceptions=False) as client:
        r = client.get(path)
    assert r.status_code == 404
    assert "create_app" not in r.text


def test_static_and_root_reject_foreign_host(client):
    for path in ("/", "/static/index.html"):
        r = client.get(path, headers={"host": "attacker.example"})
        assert r.status_code == 403


def test_api_and_docs_responses_have_no_csp(client):
    # Swagger(/docs)는 CDN 스크립트를 쓴다. CSP를 붙이면 깨지므로 화면 경로에만 붙인다
    for path in ("/docs", "/health", "/workspaces"):
        r = client.get(path)
        assert r.status_code == 200 and "content-security-policy" not in r.headers


def test_static_files_are_package_data():
    index = importlib.resources.files("axbuilder.api") / "static" / "index.html"
    assert index.is_file()
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"axbuilder.api" = ["static/**"]' in pyproject


def test_script_and_style_types_do_not_depend_on_the_windows_registry():
    # Windows는 레지스트리에 따라 .js를 text/plain으로 알려 주기도 한다. nosniff와 함께면
    # 브라우저가 모듈 스크립트를 실행하지 않는다
    assert mimetypes.guess_type("app.js")[0] == "text/javascript"
    assert mimetypes.guess_type("app.css")[0] == "text/css"
    assert mimetypes.guess_type("favicon.svg")[0] == "image/svg+xml"
