"""PR #20 1차 리뷰(정확성, 화면 보안)의 백엔드 지적에 대한 회귀 테스트.

1. 잠금 없는 읽기와 파일 바꿔 끼우기가 겹쳐도 500·401이 나지 않는다 (Windows의 공유 위반)
2. 키 폐기·발급은 파일에 쓴 뒤에만 메모리에 반영한다
3. 읽을 수 없는 index.json·문서 기록이 목록·상세를 500으로 만들지 않는다
4. stream: null
5. /docs 기본 꺼짐, 모든 응답의 보안 머리글
6. cross-site GET
"""

import collections
import hashlib
import json
import logging
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import (
    GUIDE_MD,
    GUIDE_META,
    LAW_TXT,
    META,
    WS,
    RecordingLLM,
    open_client,
    setup_indexed,
    upload,
)
from test_keys_api import bearer, make_key
from test_openai_compat import UNAUTHORIZED

import axbuilder.fsutil as fsutil
from axbuilder.api.app import create_app
from axbuilder.retrieval.dense import HashEmbedder
from axbuilder.settings import Settings

LOCAL = ("127.0.0.1", 50000)
OUTSIDE = ("203.0.113.7", 40000)
UI_CSP = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
API_CSP = "default-src 'none'; frame-ancestors 'none'"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings):
    with open_client(settings, RecordingLLM("내용 [1]."), raise_server_exceptions=False) as c:
        yield c


def workspace_dir(settings: Settings, ws="bank"):
    return settings.data_dir / "workspaces" / ws


# ---- 1. 읽기와 쓰기가 겹칠 때 ----


def race(client, actions, seconds: float) -> collections.Counter:
    """actions(이름 → 상태 코드를 돌려주는 함수)를 스레드 하나씩으로 seconds 동안 되풀이한다."""
    stop = threading.Event()
    counts: collections.Counter = collections.Counter()

    def loop(name, action):
        while not stop.is_set():
            counts[(name, action())] += 1

    threads = [threading.Thread(target=loop, args=item) for item in actions.items()]
    for thread in threads:
        thread.start()
    time.sleep(seconds)
    stop.set()
    for thread in threads:
        thread.join()
    return counts


def test_reads_and_config_writes_do_not_fail_each_other(client):
    # Windows에서는 읽는 쪽이 파일을 열고 있으면 os.replace가, 바꿔 끼우는 순간에는 읽기가
    # PermissionError로 실패한다. 수정 전: PATCH의 40%가 500, 유효한 키의 /v1/models가 401
    setup_indexed(client)
    key = make_key(client)["key"]
    turn = [0]

    def patch() -> int:
        turn[0] += 1
        return client.patch("/workspaces/bank", json={"llm_model": f"m{turn[0] % 3}"}).status_code

    counts = race(
        client,
        {
            "patch": patch,
            "detail": lambda: client.get("/workspaces/bank").status_code,
            "detail2": lambda: client.get("/workspaces/bank").status_code,
            "list": lambda: client.get("/workspaces").status_code,
            "v1": lambda: client.get("/v1/models", headers=bearer(key)).status_code,
            "ask": lambda: (
                client.post(
                    "/workspaces/bank/ask", json={"question": "보호 한도?", "role": "상담원"}
                ).status_code
            ),
        },
        seconds=2.5,
    )
    failures = {k: v for k, v in counts.items() if k[1] != 200}
    assert failures == {}, dict(counts)
    assert all(counts[(name, 200)] > 0 for name in ("patch", "detail", "list", "v1", "ask"))
    leftovers = [p.name for p in (client.app.state.service.store.root / "bank").glob(".*.tmp")]
    assert leftovers == []  # 임시 파일이 남지 않았다


def test_reads_and_reindex_do_not_fail_each_other(client):
    setup_indexed(client)
    counts = race(
        client,
        {
            "index": lambda: client.post("/workspaces/bank/index").status_code,
            "list": lambda: client.get("/workspaces").status_code,
            "list2": lambda: client.get("/workspaces").status_code,
            "detail": lambda: client.get("/workspaces/bank").status_code,
        },
        seconds=2.5,
    )
    failures = {k: v for k, v in counts.items() if k[1] != 200}
    assert failures == {}, dict(counts)
    assert counts[("index", 200)] > 0
    assert client.get("/workspaces/bank").json()["index"] == {"state": "ready", "chunks": 3}


def test_transient_read_error_is_not_treated_as_an_invalid_key(client, monkeypatch):
    # 유효한 키에 401을 주면 클라이언트가 키를 버린다. 잠깐 읽지 못한 것은 503(다시 시도)이다
    setup_indexed(client)
    key = make_key(client)["key"]
    monkeypatch.setattr(fsutil, "RETRY_DELAYS", (0.0, 0.0))
    store = client.app.state.service.store
    real = store.get
    calls = []

    def locked(workspace_id):
        calls.append(workspace_id)
        raise PermissionError(13, "The process cannot access the file", "workspace.yaml")

    monkeypatch.setattr(store, "get", locked)
    r = client.get("/v1/models", headers=bearer(key))
    assert r.status_code == 503 and calls
    assert r.json()["error"]["type"] == "server_error"
    assert r.json()["error"]["code"] == "temporarily_unavailable"
    assert r.headers["retry-after"] == "1"
    monkeypatch.setattr(store, "get", real)
    assert client.get("/v1/models", headers=bearer(key)).status_code == 200
    # 없는 키는 그때도 401이다(설정 파일을 읽기 전에 끝난다)
    monkeypatch.setattr(store, "get", locked)
    assert client.get("/v1/models", headers=bearer("axb_" + "a" * 43)).json() == UNAUTHORIZED


# ---- 2. 키: 파일에 쓴 뒤에만 반영 ----


def fail_keys_write(monkeypatch) -> None:
    real = fsutil.os.replace

    def failing(src, dst):
        if str(dst).endswith("keys.json"):
            raise PermissionError(5, "Access is denied", str(dst))
        return real(src, dst)

    monkeypatch.setattr(fsutil, "RETRY_DELAYS", (0.0,))
    monkeypatch.setattr(fsutil.os, "replace", failing)


def test_revoke_that_cannot_be_saved_fails_and_keeps_the_key(settings, client, monkeypatch):
    # 수정 전: 메모리에서 먼저 빼서 응답은 500인데 키가 목록에서 사라졌고(다시 폐기할 수 없음),
    # 파일에는 남아 재시작하면 되살아났다
    setup_indexed(client)
    made = make_key(client, role="준법감시")
    with monkeypatch.context() as patched:
        fail_keys_write(patched)
        assert client.delete(f"/workspaces/bank/keys/{made['key_id']}").status_code == 500
    # 폐기되지 않았다: 목록에 그대로 있고 여전히 쓸 수 있다(사용자가 폐기됐다고 믿지 않게)
    assert [k["key_id"] for k in client.get("/workspaces/bank/keys").json()] == [made["key_id"]]
    assert client.get("/v1/models", headers=bearer(made["key"])).status_code == 200
    assert sorted(p.name for p in workspace_dir(settings).glob("*.tmp")) == []
    # 다시 폐기하면 된다
    assert client.delete(f"/workspaces/bank/keys/{made['key_id']}").status_code == 204
    assert client.get("/v1/models", headers=bearer(made["key"])).status_code == 401
    stored = (workspace_dir(settings) / "keys.json").read_text(encoding="utf-8")
    assert hashlib.sha256(made["key"].encode()).hexdigest() not in stored


def test_revoked_key_stays_revoked_after_restart(settings):
    llm = RecordingLLM("내용 [1].")
    with open_client(settings, llm) as client:
        setup_indexed(client)
        made = make_key(client)
        assert client.delete(f"/workspaces/bank/keys/{made['key_id']}").status_code == 204
    with open_client(settings, llm) as client:
        assert client.get("/v1/models", headers=bearer(made["key"])).status_code == 401


def test_create_that_cannot_be_saved_issues_no_key(settings, client, monkeypatch):
    setup_indexed(client)
    kept = make_key(client)
    before = (workspace_dir(settings) / "keys.json").read_bytes()
    with monkeypatch.context() as patched:
        fail_keys_write(patched)
        r = client.post("/workspaces/bank/keys", json={"name": "새 키", "role": "상담원"})
        assert (
            r.status_code == 500 and "axb_" not in r.text
        )  # 저장하지 못한 키의 원문을 주지 않는다
    assert [k["key_id"] for k in client.get("/workspaces/bank/keys").json()] == [kept["key_id"]]
    assert (workspace_dir(settings) / "keys.json").read_bytes() == before
    assert make_key(client, name="새 키")["key"].startswith("axb_")  # 그 뒤에는 정상


# ---- 3. 읽을 수 없는 파일 ----


@pytest.mark.parametrize("content", [b"\xff\xfe\x00{", b"\x80abc", b"", b"{", b"null", b"[]"])
def test_unreadable_index_info_does_not_break_the_list(settings, client, content):
    setup_indexed(client)
    assert client.post("/workspaces", json={**WS, "id": "card"}).status_code == 201
    (workspace_dir(settings) / "index" / "index.json").write_bytes(content)
    r = client.get("/workspaces")
    assert r.status_code == 200 and [w["id"] for w in r.json()] == ["bank", "card"]
    assert r.json()[0]["index"] == {"state": "stale", "chunks": None}
    detail = client.get("/workspaces/bank")
    assert detail.status_code == 200 and detail.json()["index"]["state"] == "stale"
    # 다시 색인하면 돌아온다
    assert client.post("/workspaces/bank/index").status_code == 200
    assert client.get("/workspaces/bank").json()["index"] == {"state": "ready", "chunks": 3}


def broken_records():
    return [
        b"\xff\xfe\x00{",
        b"{not json",
        b"[]",
        b"{}",
        json.dumps({"meta": {"doc_id": "guide-1"}, "suffix": ".md", "bytes": 1}).encode(),
        json.dumps(
            {"meta": {**GUIDE_META, "doc_id": "other"}, "suffix": ".md", "bytes": 1}
        ).encode(),
        json.dumps({"meta": GUIDE_META, "suffix": ".exe", "bytes": 1}).encode(),
        json.dumps({"meta": GUIDE_META, "suffix": ".md", "bytes": "big"}).encode(),
    ]


@pytest.mark.parametrize("content", broken_records())
def test_broken_document_record_is_skipped_everywhere(settings, client, caplog, content):
    # 문서 기록 하나가 깨져도 워크스페이스를 열 수 있다. 목록·상세·색인이 같은 문서 집합을 본다
    setup_indexed(client, ("law.txt", LAW_TXT, META), ("guide.md", GUIDE_MD, GUIDE_META))
    (workspace_dir(settings) / "documents" / "guide-1.json").write_bytes(content)
    with caplog.at_level(logging.ERROR, logger="axbuilder.api.app"):
        (summary,) = client.get("/workspaces").json()
        detail = client.get("/workspaces/bank")
    assert summary["documents"] == 1
    assert detail.status_code == 200 and detail.json()["documents"] == 1
    assert [d["doc_id"] for d in detail.json()["documents_list"]] == ["law-1"]
    assert "guide-1.json" in caplog.text  # 서버 로그에는 남는다
    # 색인에는 guide-1이 들어 있으므로 "지금 문서와 색인이 다르다"
    assert summary["index"]["state"] == "stale"
    r = client.post("/workspaces/bank/index")
    assert r.status_code == 200 and r.json() == {"chunks": 3}  # law-1만
    assert client.get("/workspaces/bank").json()["index"] == {"state": "ready", "chunks": 3}
    asked = client.post("/workspaces/bank/ask", json={"question": "내부 절차", "role": "준법감시"})
    assert asked.status_code == 200 and "내부전용표식XYZ" not in asked.text


# ---- 4. stream: null ----


def test_chat_stream_null_means_false(client):
    setup_indexed(client)
    key = make_key(client)["key"]
    body = {
        "model": "bank",
        "messages": [{"role": "user", "content": "보호 한도?"}],
        "stream": None,
    }
    r = client.post("/v1/chat/completions", json=body, headers=bearer(key))
    assert r.status_code == 200 and r.json()["object"] == "chat.completion"
    body["stream"] = "yes"
    assert client.post("/v1/chat/completions", json=body, headers=bearer(key)).status_code == 400


# ---- 5. /docs 기본 꺼짐, 모든 응답의 보안 머리글 ----


def test_api_docs_are_off_by_default(client):
    assert Settings().api_docs is False
    for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_api_docs_can_be_turned_on(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", api_docs=True)
    with open_client(settings, RecordingLLM("x")) as client:
        for path in ("/docs", "/redoc"):
            r = client.get(path)
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
            # 켠 사람의 선택이다: Swagger가 CDN 스크립트를 쓰므로 스크립트는 막지 않는다. 그래도
            # 다른 사이트의 프레임 안에서는 뜨지 않는다
            assert r.headers["x-frame-options"] == "DENY"
            assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
            assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("1", True), ("on", True), ("false", False), ("0", False)],
)
def test_api_docs_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("AXB_API_DOCS", value)
    assert Settings.from_env().api_docs is expected
    monkeypatch.setenv("AXB_API_DOCS", "")
    assert Settings.from_env().api_docs is False


def test_api_docs_env_rejects_other_values(monkeypatch):
    monkeypatch.setenv("AXB_API_DOCS", "maybe")
    with pytest.raises(ValueError):
        Settings.from_env()


def assert_locked_down(response, csp: str = API_CSP, corp: str = "same-origin") -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == csp
    assert response.headers["cross-origin-resource-policy"] == corp


def test_every_response_has_the_security_headers(settings, client):
    setup_indexed(client)
    key = make_key(client)["key"]
    outside = TestClient(client.app, base_url="http://127.0.0.1", client=OUTSIDE)
    responses = {
        200: client.get("/health"),
        "list": client.get("/workspaces"),
        "openapi": client.get("/openapi.json"),
        404: client.get("/workspaces/nope"),
        "no route": client.get("/nope"),
        405: client.put("/workspaces"),
        422: client.post("/workspaces", json={}),
        201: client.post("/workspaces", json={**WS, "id": "card"}),
        "foreign host": client.get("/health", headers={"host": "attacker.example"}),
        "foreign origin": client.get("/health", headers={"origin": "https://attacker.example"}),
        "cross-site post": client.post(
            "/workspaces", json=WS, headers={"sec-fetch-site": "cross-site"}
        ),
        "outside": outside.get("/workspaces"),
        401: client.get("/v1/models"),
        "v1": client.get("/v1/models", headers=bearer(key)),
        "v1 400": client.post("/v1/ask", json={}, headers=bearer(key)),
        "v1 404": client.get("/v1/nope", headers=bearer(key)),
        "sse": client.post(
            "/v1/chat/completions",
            json={"model": "bank", "messages": [{"role": "user", "content": "x"}], "stream": True},
            headers=bearer(key),
        ),
    }
    expected_status = {
        200: 200, "list": 200, "openapi": 200, 404: 404, "no route": 404, 405: 405, 422: 422,
        201: 201, "foreign host": 403, "foreign origin": 403, "cross-site post": 403,
        "outside": 403, 401: 401, "v1": 200, "v1 400": 400, "v1 404": 404, "sse": 200,
    }  # fmt: skip
    for name, response in responses.items():
        assert response.status_code == expected_status[name], name
        assert_locked_down(response)


def test_internal_error_response_has_the_security_headers(settings, client):
    assert client.post("/workspaces", json=WS).status_code == 201
    make_key(client)
    (workspace_dir(settings) / "keys.json").write_text("{broken", encoding="utf-8")
    with open_client(settings, RecordingLLM("x"), raise_server_exceptions=False) as restarted:
        r = restarted.get("/workspaces/bank/keys")
        assert r.status_code == 500
        assert_locked_down(r)


def test_ui_responses_have_the_ui_policy(client):
    for path in ("/", "/static/index.html", "/static/no-such-file.js"):
        r = client.get(path)
        assert_locked_down(r, csp=UI_CSP)
        assert r.headers["cache-control"] == "no-cache"


def test_cors_responses_may_be_read_cross_origin(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", cors_origins=("https://app.example",))
    with open_client(settings, RecordingLLM("내용 [1].")) as client:
        setup_indexed(client)
        key = make_key(client)["key"]
        allowed = client.get("/v1/models", headers={**bearer(key), "Origin": "https://app.example"})
        assert allowed.headers["access-control-allow-origin"] == "https://app.example"
        assert_locked_down(allowed, corp="cross-origin")
        unauthorized = client.get("/v1/models", headers={"Origin": "https://app.example"})
        assert unauthorized.status_code == 401
        assert_locked_down(unauthorized, corp="cross-origin")
        preflight = client.options(
            "/v1/ask",
            headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST"},
        )
        assert preflight.status_code == 204
        assert preflight.headers["cross-origin-resource-policy"] == "cross-origin"
        # 설정하지 않은 출처, Origin이 없는 요청, 관리 API는 same-origin 그대로
        other = client.get("/v1/models", headers={**bearer(key), "Origin": "https://evil.example"})
        assert_locked_down(other)
        assert_locked_down(client.get("/v1/models", headers=bearer(key)))
        assert_locked_down(client.get("/workspaces"))


# ---- 6. cross-site GET ----

SCRIPT_TAG = {
    "sec-fetch-site": "cross-site",
    "sec-fetch-mode": "no-cors",
    "sec-fetch-dest": "script",
}
LINK = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}
FRAME = {"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "iframe"}


@pytest.fixture
def no_ollama(settings):
    """/models 핸들러가 돌면 Ollama를 부른다. 불렀는지 세어 둔다."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"models": []})

    app = create_app(
        settings,
        llm=RecordingLLM("내용 [1]."),
        embedder=HashEmbedder(),
        ollama_transport=httpx.MockTransport(handler),
    )
    with TestClient(app, base_url="http://127.0.0.1", client=LOCAL) as c:
        setup_indexed(c)
        make_key(c)
        c.ollama_calls = calls
        yield c


@pytest.mark.parametrize(
    "path",
    [
        "/workspaces",
        "/workspaces/bank",
        "/workspaces/bank/keys",
        "/workspaces/nope/keys",
        "/models",
        "/health",
        "/openapi.json",
        "/static/index.html",
        "/nope",
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        SCRIPT_TAG,
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"},
        {**LINK, "sec-fetch-dest": "image"},
        FRAME,
    ],
    ids=["script", "bare", "same-site", "image", "iframe"],
)
def test_cross_site_get_is_rejected_before_the_handler(no_ollama, path, headers):
    r = no_ollama.get(path, headers=headers)
    assert r.status_code == 403
    assert r.json() == {"detail": "다른 사이트에서 보낸 요청은 받지 않습니다"}
    assert_locked_down(r, csp=UI_CSP if path.startswith("/static") else API_CSP)
    assert no_ollama.ollama_calls == []  # /models가 Ollama까지 가지 않았다
    assert no_ollama.head(path, headers=headers).status_code == 403


def test_cross_site_script_tag_cannot_tell_whether_a_workspace_exists(no_ollama):
    # <script src="http://127.0.0.1:8000/workspaces/{id}/keys">의 onload/onerror로 id가 있는지
    # 알아내는 것: 있는 id와 없는 id의 응답이 같고, 구형 브라우저용으로 nosniff·CORP도 붙는다
    existing = no_ollama.get("/workspaces/bank/keys", headers=SCRIPT_TAG)
    missing = no_ollama.get("/workspaces/nope/keys", headers=SCRIPT_TAG)
    assert existing.status_code == missing.status_code == 403
    assert existing.content == missing.content
    plain = no_ollama.get("/workspaces/bank/keys")  # Sec-Fetch-* 를 보내지 않는 클라이언트
    assert plain.status_code == 200
    assert_locked_down(plain)


def test_links_from_other_sites_can_still_open_the_ui(no_ollama):
    assert no_ollama.get("/", headers=LINK).status_code == 200
    # 프레임 안에서 여는 것, 화면이 아닌 주소로 가는 링크는 받지 않는다
    assert no_ollama.get("/", headers=FRAME).status_code == 403
    assert no_ollama.get("/workspaces", headers=LINK).status_code == 403
    assert no_ollama.get("/models", headers=LINK).status_code == 403
    assert no_ollama.ollama_calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"sec-fetch-site": "same-origin", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty"},
        {"sec-fetch-site": "none", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"},
    ],
    ids=["no-headers", "same-origin", "typed-or-bookmark"],
)
def test_same_origin_and_direct_gets_still_work(no_ollama, headers):
    for path in (
        "/",
        "/static/index.html",
        "/workspaces",
        "/workspaces/bank",
        "/models",
        "/health",
    ):
        assert no_ollama.get(path, headers=headers).status_code == 200, path


def test_cross_site_get_with_a_valid_key_reaches_v1(no_ollama):
    key = make_key(no_ollama)["key"]
    headers = {**bearer(key), "sec-fetch-site": "cross-site", "sec-fetch-mode": "cors"}
    assert no_ollama.get("/v1/models", headers=headers).status_code == 200
    assert no_ollama.get("/v1/models", headers=SCRIPT_TAG).status_code == 401


def test_upload_still_works_next_to_these_checks(client):
    assert client.post("/workspaces", json=WS).status_code == 201
    assert upload(client, "law.txt", LAW_TXT).status_code == 201
