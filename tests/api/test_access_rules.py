"""접근 규칙(Task 16): 키 없는 /v1/* 는 401, 내 PC 밖에서는 /v1/* 만, CORS는 설정한 출처에만.

미들웨어 순서(바깥 → 안): Host 허용 목록(403) → 접근 규칙(loopback 아님 403, /v1 키 없음 401,
preflight) → Origin·Sec-Fetch-Site(403, 유효한 키가 있으면 건너뜀) → 본문 상한 → 화면 머리글.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from test_app import LAW_TXT, META, WS, RecordingLLM, open_client, setup_indexed
from test_keys_api import bearer, make_key
from test_openai_compat import UNAUTHORIZED

from axbuilder.api.access import is_loopback_client
from axbuilder.api.app import create_app
from axbuilder.retrieval.dense import HashEmbedder
from axbuilder.settings import Settings

OUTSIDE = ("203.0.113.7", 40000)
LOOPBACK_ONLY = {
    "detail": "이 PC에서만 쓸 수 있는 주소입니다. 다른 곳에서는 API 키로 /v1/* 만 쓸 수 있습니다"
}
PROXY_HEADERS = {
    "X-Forwarded-For": "127.0.0.1",
    "Forwarded": "for=127.0.0.1;host=127.0.0.1",
    "X-Real-IP": "127.0.0.1",
    "X-Forwarded-Host": "127.0.0.1",
    "Client-IP": "127.0.0.1",
}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def app(settings):
    return create_app(settings, llm=RecordingLLM("내용 [1]."), embedder=HashEmbedder())


@pytest.fixture
def local(app):
    """이 PC에서 온 요청. 워크스페이스·색인·키를 만들어 둔다."""
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as c:
        setup_indexed(c)
        c.key = make_key(c)["key"]
        c.key_id = c.get("/workspaces/bank/keys").json()[0]["key_id"]
        yield c


@pytest.fixture
def outside(app, local):
    """다른 PC에서 온 요청(같은 앱). lifespan은 local이 맡는다."""
    return TestClient(app, base_url="http://127.0.0.1", client=OUTSIDE)


def asgi_get(app, path: str, client, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    """scope의 client를 마음대로 주려고 ASGI로 직접 부른다(None, 깨진 값 포함)."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8000"),
            *((k.lower().encode(), v.encode()) for k, v in (headers or {}).items()),
        ],
        "server": ("127.0.0.1", 8000),
    }
    if client != "missing":
        scope["client"] = client
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body) if body else {}


# ---- Review Focus 1: 키 없이, 틀린 키로 ----


def test_unauthorized_response_is_the_same_for_every_reason(local):
    revoked = make_key(local, name="폐기할 키")
    assert local.delete(f"/workspaces/bank/keys/{revoked['key_id']}").status_code == 204
    almost = local.key[:-1] + ("a" if local.key[-1] != "a" else "b")
    cases = [
        {},  # 머리글 없음
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": local.key},  # 스킴 없음
        {"Authorization": f"Basic {local.key}"},
        {"Authorization": f"Bearer {local.key} extra"},
        {"Authorization": "Bearer not-a-key"},
        {"Authorization": "Bearer axb_" + "a" * 43},  # 형식은 맞지만 없는 키
        {"Authorization": f"Bearer {almost}"},
        {"Authorization": f"Bearer {local.key[:20]}"},  # 앞부분만
        {"Authorization": f"Bearer {local.key}x"},
        {"Authorization": f"Bearer {local.key.upper()}"},
        {"Authorization": f"Bearer {revoked['key']}"},  # 폐기된 키
        {"X-API-Key": local.key},
        {"Cookie": f"key={local.key}"},
    ]
    responses = []
    for headers in cases:
        responses.append(local.get("/v1/models", headers=headers))
        responses.append(local.post("/v1/ask", json={"question": "보호 한도?"}, headers=headers))
        body = {"model": "bank", "messages": [{"role": "user", "content": "보호 한도?"}]}
        responses.append(local.post("/v1/chat/completions", json=body, headers=headers))
    responses.append(local.get(f"/v1/models?api_key={local.key}"))  # 주소에 실은 키는 받지 않는다
    for r in responses:
        assert r.status_code == 401
        assert r.json() == UNAUTHORIZED
        assert r.headers["www-authenticate"] == "Bearer"
        assert r.content == responses[0].content
    assert local.get("/v1/models", headers=bearer(local.key)).status_code == 200


def test_bearer_scheme_is_case_insensitive(local):
    assert (
        local.get("/v1/models", headers={"Authorization": f"bearer {local.key}"}).status_code == 200
    )


def test_repeated_authorization_headers_are_rejected(app, local):
    status, body = asgi_get(
        app, "/v1/models", ("127.0.0.1", 1), {"Authorization": f"Bearer {local.key}"}
    )
    assert status == 200
    scope_headers = [
        (b"authorization", f"Bearer {local.key}".encode()),
        (b"authorization", b"Bearer x"),
    ]

    async def call() -> int:
        sent = []
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/v1/models",
            "raw_path": b"/v1/models",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"127.0.0.1"), *scope_headers],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8000),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        return next(m["status"] for m in sent if m["type"] == "http.response.start")

    assert asyncio.run(call()) == 401


def test_v1_needs_a_key_even_from_this_pc(local):
    assert local.get("/v1/models").status_code == 401
    assert local.post("/v1/ask", json={"question": "x"}).status_code == 401


# ---- Sec-Fetch-Site·Origin: 유효한 키가 있을 때만 건너뛴다 ----

BROWSER_CROSS_SITE = {"sec-fetch-site": "cross-site", "origin": "https://app.example"}


def test_valid_key_skips_the_browser_origin_checks(local):
    headers = {**bearer(local.key), **BROWSER_CROSS_SITE}
    assert (
        local.post("/v1/ask", json={"question": "보호 한도?"}, headers=headers).status_code == 200
    )
    assert local.get("/v1/models", headers=headers).status_code == 200


@pytest.mark.parametrize("auth", [{}, {"Authorization": "Bearer axb_" + "a" * 43}])
def test_missing_or_wrong_key_is_401_whatever_the_origin(local, auth):
    # 키가 없거나 틀리면 Origin·Sec-Fetch-Site가 무엇이든 401로 같다(403과 섞이지 않는다)
    for extra in ({}, BROWSER_CROSS_SITE, {"sec-fetch-site": "same-origin"}):
        r = local.post("/v1/ask", json={"question": "x"}, headers={**auth, **extra})
        assert r.status_code == 401 and r.json() == UNAUTHORIZED


def test_host_allow_list_still_applies_to_v1(local):
    headers = {**bearer(local.key), "host": "attacker.example"}
    r = local.get("/v1/models", headers=headers)
    assert r.status_code == 403 and "detail" in r.json()
    assert local.get("/v1/models", headers={"host": "attacker.example"}).status_code == 403


def test_a_key_does_not_unlock_the_management_api_checks(local):
    # 키는 /v1/* 의 인증일 뿐이다. 관리 API의 cross-site 검사는 키 머리글이 있어도 그대로다
    headers = {**bearer(local.key), **BROWSER_CROSS_SITE}
    r = local.post("/workspaces", json={**WS, "id": "evil"}, headers=headers)
    assert r.status_code == 403
    r = local.post("/workspaces/bank/keys", json={"name": "x", "role": "준법감시"}, headers=headers)
    assert r.status_code == 403
    assert [w["id"] for w in local.get("/workspaces").json()] == ["bank"]
    assert len(local.get("/workspaces/bank/keys").json()) == 1


# ---- Review Focus 3: 내 PC 밖에서는 /v1/* 만 ----

MANAGEMENT_REQUESTS = [
    ("GET", "/", None),
    ("GET", "/static/index.html", None),
    ("GET", "/health", None),
    ("GET", "/docs", None),
    ("GET", "/openapi.json", None),
    ("GET", "/models", None),
    ("GET", "/workspaces", None),
    ("GET", "/workspaces/bank", None),
    ("GET", "/workspaces/bank/keys", None),
    ("POST", "/workspaces", {**WS, "id": "evil"}),
    ("POST", "/demo-workspace", None),
    ("POST", "/workspaces/bank/index", None),
    ("POST", "/workspaces/bank/ask", {"question": "보호 한도?", "role": "준법감시"}),
    ("POST", "/workspaces/bank/keys", {"name": "x", "role": "준법감시"}),
    ("PATCH", "/workspaces/bank", {"llm_model": "evil:1b"}),
    ("GET", "/nope", None),
]


@pytest.mark.parametrize(("method", "path", "body"), MANAGEMENT_REQUESTS)
@pytest.mark.parametrize("with_key", [False, True])
def test_outside_clients_cannot_reach_management_or_ui(
    local, outside, method, path, body, with_key
):
    headers = {**PROXY_HEADERS, **(bearer(local.key) if with_key else {})}
    r = outside.request(method, path, json=body, headers=headers)
    assert r.status_code == 403 and r.json() == LOOPBACK_ONLY
    # 바뀐 것이 없다
    assert [w["id"] for w in local.get("/workspaces").json()] == ["bank"]
    assert local.get("/workspaces/bank").json()["llm_model"] is None
    assert len(local.get("/workspaces/bank/keys").json()) == 1


def test_outside_clients_cannot_upload_or_revoke(local, outside):
    r = outside.post(
        "/workspaces/bank/documents",
        files={"file": ("law.txt", LAW_TXT)},
        data={**META, "doc_id": "evil"},
        headers=PROXY_HEADERS,
    )
    assert r.status_code == 403
    assert outside.delete(f"/workspaces/bank/keys/{local.key_id}").status_code == 403
    assert local.get("/workspaces/bank").json()["documents"] == 1
    assert local.get("/v1/models", headers=bearer(local.key)).status_code == 200


def test_outside_clients_can_use_v1_with_a_key(local, outside):
    headers = bearer(local.key)
    assert outside.get("/v1/models", headers=headers).status_code == 200
    r = outside.post("/v1/ask", json={"question": "보호 한도?"}, headers=headers)
    assert r.status_code == 200 and r.json()["citations"]
    body = {"model": "bank", "messages": [{"role": "user", "content": "보호 한도?"}]}
    assert outside.post("/v1/chat/completions", json=body, headers=headers).status_code == 200
    assert outside.get("/v1/models").status_code == 401
    assert outside.get("/v1/models", headers=PROXY_HEADERS).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/v1/../workspaces",
        "/v1/%2e%2e/workspaces",
        "/v1/..%2fworkspaces",
        "/v1//workspaces",
        "//workspaces",
        "/V1/../workspaces",
        "/v1x/workspaces",
        "/v10/models",
        "/v1",
        "/v1/",
        "/workspaces/../v1/models",
        "/static/../v1/../workspaces",
    ],
)
def test_outside_clients_cannot_slip_past_with_path_tricks(app, local, path):
    for target in (path, path.replace("%2e", ".").replace("%2f", "/")):
        status, body = asgi_get(app, target, OUTSIDE, bearer(local.key))
        assert status in (403, 404)
        assert "error" in body or body == LOOPBACK_ONLY  # 워크스페이스 목록 등은 나오지 않는다
        assert "bank" not in json.dumps(body, ensure_ascii=False)


def test_forwarded_headers_do_not_change_who_the_client_is(local, outside):
    # 이 PC에서 온 요청이 프록시 머리글로 밖이라고 해도 그대로 받는다(읽지 않는다)
    away = {"X-Forwarded-For": "8.8.8.8", "Forwarded": "for=8.8.8.8", "X-Real-IP": "8.8.8.8"}
    assert local.get("/workspaces", headers=away).status_code == 200
    # 밖에서 온 요청이 프록시 머리글로 이 PC라고 해도 막는다
    for name, value in PROXY_HEADERS.items():
        assert outside.get("/workspaces", headers={name: value}).status_code == 403


@pytest.mark.parametrize(
    ("client", "allowed"),
    [
        (("127.0.0.1", 50000), True),
        (("127.5.6.7", 1), True),
        (("::1", 50000), True),
        (("::ffff:127.0.0.1", 50000), True),
        (["127.0.0.1", 50000], True),
        (("10.0.0.5", 50000), False),
        (("192.168.0.10", 50000), False),
        (("0.0.0.0", 50000), False),
        (("::ffff:10.0.0.5", 50000), False),
        (("fe80::1", 50000), False),
        (("testclient", 50000), False),  # Starlette TestClient의 기본값도 예외가 아니다
        (("localhost", 50000), False),  # 이름은 풀지 않는다
        (("127.0.0.1.attacker.example", 1), False),
        (("", 0), False),
        ((), False),
        (None, False),
        ("missing", False),
        ("127.0.0.1", False),
        ((None, None), False),
        ((127, 1), False),
    ],
)
def test_only_loopback_addresses_count_as_this_pc(app, local, client, allowed):
    if client != "missing":
        assert is_loopback_client(client) is allowed
    status, body = asgi_get(app, "/workspaces", client)
    assert status == (200 if allowed else 403)
    if not allowed:
        assert body == LOOPBACK_ONLY
        # 주소를 알 수 없어도 키가 있으면 /v1/* 는 된다
        assert asgi_get(app, "/v1/models", client, bearer(local.key))[0] == 200


def test_default_test_client_address_is_not_loopback(app, local):
    # 운영 코드에 "testclient" 예외가 없다: 주소를 맞추지 않은 TestClient는 밖에서 온 것이다
    plain = TestClient(app, base_url="http://127.0.0.1")
    assert plain.get("/health").status_code == 403


# ---- CORS: AXB_CORS_ORIGINS에 있는 출처에만 ----

CORS_HEADERS = (
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-allow-credentials",
)


@pytest.fixture
def cors(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", cors_origins=("https://app.example", "http://localhost:3000")
    )
    with open_client(settings, RecordingLLM("내용 [1].")) as c:
        setup_indexed(c)
        c.key = make_key(c)["key"]
        yield c


def preflight(client, origin: str, path="/v1/chat/completions", **headers):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
            **headers,
        },
    )


def test_cors_is_off_by_default(local):
    assert Settings().cors_origins == ()
    r = local.get("/v1/models", headers={**bearer(local.key), "Origin": "https://app.example"})
    assert r.status_code == 200
    assert not any(name in r.headers for name in CORS_HEADERS)
    r = preflight(local, "https://app.example")
    assert not any(name in r.headers for name in CORS_HEADERS)


@pytest.mark.parametrize("origin", ["https://app.example", "http://localhost:3000"])
def test_cors_headers_for_a_configured_origin(cors, origin):
    r = cors.get("/v1/models", headers={**bearer(cors.key), "Origin": origin})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert "origin" in r.headers["vary"].lower()
    assert "access-control-allow-credentials" not in r.headers
    # 오류 응답도 그 출처의 스크립트가 읽을 수 있어야 한다
    r = cors.get("/v1/models", headers={"Origin": origin})
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == origin
    r = cors.post("/v1/ask", json={}, headers={**bearer(cors.key), "Origin": origin})
    assert r.status_code == 400 and r.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://app.example",  # 스킴이 다르다
        "https://app.example:8443",  # 포트가 다르다
        "https://app.example.evil.example",
        "https://sub.app.example",
        "http://localhost:3001",
        "null",
        "*",
        "https://app.example, https://evil.example",
    ],
)
def test_no_cors_headers_for_other_origins(cors, origin):
    r = cors.get("/v1/models", headers={**bearer(cors.key), "Origin": origin})
    assert r.status_code == 200  # 키가 인증이다. 다만 브라우저는 응답을 스크립트에 주지 않는다
    assert not any(name in r.headers for name in CORS_HEADERS)
    r = preflight(cors, origin)
    assert not any(name in r.headers for name in CORS_HEADERS)
    assert r.status_code == 204 and r.content == b""


def test_preflight_passes_without_a_key_for_a_configured_origin(cors):
    r = preflight(cors, "https://app.example", **{"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 204 and r.content == b""
    assert r.headers["access-control-allow-origin"] == "https://app.example"
    methods = {m.strip() for m in r.headers["access-control-allow-methods"].split(",")}
    assert {"GET", "POST"} <= methods
    allowed = {h.strip().lower() for h in r.headers["access-control-allow-headers"].split(",")}
    assert {"authorization", "content-type"} <= allowed
    assert "origin" in r.headers["vary"].lower()
    assert "access-control-allow-credentials" not in r.headers
    assert r.headers["access-control-allow-origin"] != "*"


def test_preflight_allows_the_headers_the_browser_asks_for(cors):
    # openai JS SDK는 x-stainless-* 머리글을 붙인다
    asked = "authorization, content-type, x-stainless-os, x-stainless-lang"
    r = preflight(cors, "https://app.example", **{"Access-Control-Request-Headers": asked})
    allowed = {h.strip().lower() for h in r.headers["access-control-allow-headers"].split(",")}
    assert {"x-stainless-os", "x-stainless-lang", "authorization"} <= allowed
    # 머리글 이름이 아닌 글자는 되돌려 주지 않는다
    r = preflight(cors, "https://app.example", **{"Access-Control-Request-Headers": "a\tb<script>"})
    assert "<script>" not in r.headers.get("access-control-allow-headers", "")


def test_options_without_preflight_headers_still_needs_a_key(cors):
    r = cors.options("/v1/models", headers={"Origin": "https://app.example"})
    assert r.status_code == 401


def test_cors_does_not_open_the_management_api(cors):
    r = preflight(cors, "https://app.example", path="/workspaces")
    assert r.status_code == 403
    assert not any(name in r.headers for name in CORS_HEADERS)
    r = cors.get("/workspaces", headers={"Origin": "https://app.example"})
    assert r.status_code == 403 and "access-control-allow-origin" not in r.headers
    r = cors.post(
        "/workspaces", json={**WS, "id": "evil"}, headers={"Origin": "https://app.example"}
    )
    assert r.status_code == 403


# ---- 설정: AXB_CORS_ORIGINS ----


def test_cors_origins_from_env(monkeypatch):
    monkeypatch.setenv("AXB_CORS_ORIGINS", " https://App.Example , http://localhost:3000,, ")
    assert Settings.from_env().cors_origins == ("https://app.example", "http://localhost:3000")
    monkeypatch.setenv("AXB_CORS_ORIGINS", "")
    assert Settings.from_env().cors_origins == ()


@pytest.mark.parametrize(
    "bad",
    [
        "*",
        "https://*.example.com",
        "null",
        "app.example",
        "https://app.example/",
        "https://app.example/path",
        "https://user:pw@app.example",
        "ftp://app.example",
        "https://app.example:99999",
        "https://app.example?x=1",
        "https://app.example https://b.example",
    ],
)
def test_cors_origins_rejects_wildcards_and_non_origins(monkeypatch, bad):
    monkeypatch.setenv("AXB_CORS_ORIGINS", bad)
    with pytest.raises(ValueError):
        Settings.from_env()
    with pytest.raises(ValueError):
        Settings(cors_origins=(bad,))
