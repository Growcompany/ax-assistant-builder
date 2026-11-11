"""Task 16 보안 리뷰에서 재현된 문제의 회귀 테스트.

1. root_path(하위 경로 배치)로 loopback 경계가 뚫리지 않는다
3. /v1/* 의 503이 LLM 백엔드 상세를 내보내지 않는다
4. 워크스페이스·역할이 없어진 키는 본문 검증·경로 탐색보다 먼저 401이다
5. Ollama가 돌려준 모델 상세, preflight가 되돌려 주는 머리글 목록의 크기
"""

import asyncio
import json
import logging

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount
from test_app import WS, DownLLM, RecordingLLM, open_client, setup_indexed
from test_keys_api import bearer, make_key
from test_openai_compat import UNAUTHORIZED

import axbuilder.api.app as app_module
from axbuilder.api.access import route_path
from axbuilder.api.app import create_app
from axbuilder.retrieval.dense import HashEmbedder
from axbuilder.settings import Settings

OUTSIDE = ("203.0.113.7", 40000)
LOCAL = ("127.0.0.1", 50000)
LOOPBACK_ONLY = {
    "detail": "이 PC에서만 쓸 수 있는 주소입니다. 다른 곳에서는 API 키로 /v1/* 만 쓸 수 있습니다"
}
V1_UNAVAILABLE = "LLM을 사용할 수 없습니다. 이 어시스턴트의 관리자에게 문의하세요"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def app(settings):
    return create_app(settings, llm=RecordingLLM("내용 [1]."), embedder=HashEmbedder())


@pytest.fixture
def local(app):
    with TestClient(app, base_url="http://127.0.0.1", client=LOCAL) as c:
        setup_indexed(c)
        c.key = make_key(c, role="상담원")["key"]
        yield c


def call(
    app,
    method: str,
    path: str,
    client,
    *,
    root_path: str = "",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
) -> tuple[int, dict]:
    """ASGI scope를 직접 만든다: root_path와 client를 마음대로 준다."""
    raw = json.dumps(body).encode() if body is not None else b""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": root_path,
        "headers": [
            (b"host", b"127.0.0.1:8000"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
            *((k.lower().encode(), v.encode()) for k, v in (headers or {}).items()),
        ],
        "client": client,
        "server": ("127.0.0.1", 8000),
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    try:
        return status, json.loads(payload) if payload else {}
    except ValueError:
        return status, {"raw": payload.decode("utf-8", "replace")}


# ---- 1. root_path ----


def test_route_path_matches_what_the_router_sees():
    from starlette._utils import get_route_path

    for root_path, path in [
        ("", "/v1/models"),
        ("/v1", "/v1/workspaces"),
        ("/v1", "/v1"),
        ("/v1", "/v1x/workspaces"),
        ("/v1", "/workspaces"),
        ("/x", "/x/v1/models"),
        ("/x/", "/x/v1/models"),
    ]:
        scope = {"path": path, "root_path": root_path}
        assert route_path(scope) == get_route_path(scope)
    assert route_path({"path": "/v1/workspaces", "root_path": "/v1"}) == "/workspaces"
    assert route_path({"path": "/v1/models"}) == "/v1/models"


MANAGEMENT = [
    ("GET", "/workspaces", None),
    ("GET", "/workspaces/bank", None),
    ("GET", "/workspaces/bank/keys", None),
    ("GET", "/models", None),
    ("GET", "/", None),
    ("POST", "/workspaces/bank/keys", {"name": "x", "role": "준법감시"}),
    ("POST", "/workspaces/bank/ask", {"question": "보호 한도?", "role": "준법감시"}),
    ("POST", "/workspaces", {**WS, "id": "evil"}),
    ("PATCH", "/workspaces/bank", {"llm_model": "evil:1b"}),
]


@pytest.mark.parametrize(("method", "path", "body"), MANAGEMENT)
@pytest.mark.parametrize("root_path", ["/v1", "/v1/", "/x", "/v1/v1"])
def test_root_path_does_not_let_outside_clients_reach_management(
    app, local, method, path, body, root_path
):
    # uvicorn --root-path /v1: scope["path"]는 "/v1/workspaces"인데 라우터는 "/workspaces"로 보낸다.
    # 미들웨어가 scope["path"]만 보면 키 하나로 관리 API가 열린다
    full = root_path.rstrip("/") + path
    for sent_path in (full, path):  # root_path를 path에 붙여 주는 서버와 붙이지 않는 서버
        status, payload = call(
            app,
            method,
            sent_path,
            OUTSIDE,
            root_path=root_path,
            headers=bearer(local.key),
            body=body,
        )
        assert status == 403 and payload == LOOPBACK_ONLY
    assert [w["id"] for w in local.get("/workspaces").json()] == ["bank"]
    assert len(local.get("/workspaces/bank/keys").json()) == 1
    assert local.get("/workspaces/bank").json()["llm_model"] is None


@pytest.mark.parametrize("root_path", ["/v1", "/x"])
def test_outside_clients_get_nothing_at_all_under_a_root_path(app, local, root_path):
    # 하위 경로 배치는 지원하지 않는다: 유효한 키로 /v1/* 를 불러도 밖에서는 403
    for path in (f"{root_path}/v1/models", "/v1/models", f"{root_path}/v1/ask"):
        status, payload = call(
            app, "GET", path, OUTSIDE, root_path=root_path, headers=bearer(local.key)
        )
        assert status == 403 and payload == LOOPBACK_ONLY


def test_under_a_root_path_this_pc_is_judged_by_the_route_path(app, local):
    # 이 PC에서는 그대로 쓸 수 있다. /v1 판단은 라우터가 보는 경로로 한다
    status, payload = call(app, "GET", "/v1/workspaces", LOCAL, root_path="/v1")
    assert status == 200 and [w["id"] for w in payload] == ["bank"]  # 관리 API: 키가 필요 없다
    status, payload = call(app, "GET", "/v1/workspaces/nope", LOCAL, root_path="/v1")
    assert status == 404 and payload == {"detail": "워크스페이스를 찾을 수 없습니다"}  # 오류 모양도
    status, payload = call(app, "GET", "/v1/v1/models", LOCAL, root_path="/v1")
    assert status == 401 and payload == UNAUTHORIZED  # 이것이 /v1/models다
    status, payload = call(
        app, "GET", "/v1/v1/models", LOCAL, root_path="/v1", headers=bearer(local.key)
    )
    assert status == 200 and payload["data"][0]["id"] == "bank"
    status, payload = call(
        app, "GET", "/v1/v1/nope", LOCAL, root_path="/v1", headers=bearer(local.key)
    )
    assert status == 404 and "error" in payload


def test_cross_site_checks_still_apply_under_a_root_path(app, local):
    # root_path 아래의 관리 경로가 "/v1"로 보여 cross-site 검사를 건너뛰는 일도 없다
    headers = {**bearer(local.key), "sec-fetch-site": "cross-site"}
    status, _ = call(
        app,
        "POST",
        "/v1/workspaces",
        LOCAL,
        root_path="/v1",
        headers=headers,
        body={**WS, "id": "evil"},
    )
    assert status == 403
    assert [w["id"] for w in local.get("/workspaces").json()] == ["bank"]


@pytest.mark.parametrize("prefix", ["/v1", "/x"])
def test_mounting_under_another_app_does_not_open_management(app, local, prefix):
    outer = Starlette(routes=[Mount(prefix, app=app)])
    for method, path, body in MANAGEMENT:
        status, payload = call(
            outer, method, prefix + path, OUTSIDE, headers=bearer(local.key), body=body
        )
        assert status == 403 and payload == LOOPBACK_ONLY
    status, payload = call(outer, "GET", f"{prefix}/v1/models", OUTSIDE, headers=bearer(local.key))
    assert status == 403
    assert len(local.get("/workspaces/bank/keys").json()) == 1


def test_body_limit_is_chosen_by_the_route_path(app, local):
    # root_path="/v1" 아래의 관리 경로가 /v1의 큰 본문 상한(1MB)을 받지 않는다
    big = {**WS, "id": "big", "name": "가" * 40_000}  # 약 120KB > 64KB
    status, _ = call(app, "POST", "/v1/workspaces", LOCAL, root_path="/v1", body=big)
    assert status == 413


# ---- 3. /v1 503: 백엔드 상세는 서버 로그에만 ----

BACKEND_DETAIL = (
    "Ollama 오류 500: <html>internal-gateway-banner</html> "
    "Ollama 서버(http://10.20.30.40:11434)에서 모델 'secret-model:7b'을 찾지 못했습니다. "
    "모델을 받으세요: ollama pull secret-model:7b"
)
LEAKS = ("10.20.30.40", "11434", "secret-model", "internal-gateway-banner", "ollama", "Ollama")


@pytest.mark.parametrize(
    "detail",
    [
        BACKEND_DETAIL,
        # cloud 모드의 Claude 오류도 같은 예외로 온다
        "Claude API 요청 오류 400: model secret-model. ollama 10.20.30.40:11434 "
        "internal-gateway-banner",
    ],
)
def test_v1_llm_error_hides_backend_details(settings, caplog, detail):
    with open_client(settings, DownLLM(detail)) as client:
        setup_indexed(client)
        key = make_key(client)["key"]
        chat = {"model": "bank", "messages": [{"role": "user", "content": "보호 한도?"}]}
        with caplog.at_level(logging.WARNING, logger="axbuilder.api.app"):
            responses = [
                client.post("/v1/chat/completions", json=chat, headers=bearer(key)),
                client.post(
                    "/v1/chat/completions", json={**chat, "stream": True}, headers=bearer(key)
                ),
                client.post("/v1/ask", json={"question": "보호 한도?"}, headers=bearer(key)),
            ]
        for r in responses:
            assert r.status_code == 503
            assert r.json() == {
                "error": {
                    "message": V1_UNAVAILABLE,
                    "type": "server_error",
                    "code": "llm_unavailable",
                    "param": None,
                }
            }
            assert not any(leak in r.text for leak in LEAKS)
        assert "secret-model" in caplog.text  # 상세는 서버 로그에 남는다
        # 이 PC의 관리 API(화면)는 지금처럼 상세와 해결 방법을 보여 준다
        r = client.post("/workspaces/bank/ask", json={"question": "보호 한도?", "role": "상담원"})
        assert r.status_code == 503 and "secret-model" in r.json()["detail"]


def test_v1_embedder_error_hides_backend_details(settings, monkeypatch, caplog):
    with open_client(settings, RecordingLLM("내용 [1].")) as client:
        setup_indexed(client)
        key = make_key(client)["key"]

        def broken(*args, **kwargs):
            raise app_module.EmbedderUnavailableError(
                "임베딩 모델 'secret-embedder'을 불러오지 못했습니다. GPU 상태를 확인하세요"
            )

        monkeypatch.setattr(client.app.state.service, "answer_with_usage", broken)
        with caplog.at_level(logging.WARNING, logger="axbuilder.api.app"):
            r = client.post("/v1/ask", json={"question": "보호 한도?"}, headers=bearer(key))
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "embedder_unavailable"
        assert "secret-embedder" not in r.text and "GPU" not in r.text
        assert "secret-embedder" in caplog.text


def test_v1_index_not_ready_does_not_describe_the_management_api(settings):
    with open_client(settings, RecordingLLM("x")) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        key = make_key(client)["key"]
        r = client.post("/v1/ask", json={"question": "보호 한도?"}, headers=bearer(key))
        assert r.status_code == 409 and r.json()["error"]["code"] == "index_not_ready"
        assert "/workspaces" not in r.text and "POST" not in r.text
        # 관리 API는 무엇을 부를지 알려 준다
        r = client.post("/workspaces/bank/ask", json={"question": "x", "role": "상담원"})
        assert r.status_code == 409 and "/workspaces/bank/index" in r.json()["detail"]


# ---- 4. 워크스페이스·역할이 없어진 키 ----


def rewrite_config(settings: Settings, change) -> None:
    config = settings.data_dir / "workspaces" / "bank" / "workspace.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    config.write_text(change(data), encoding="utf-8")


def remove_role(data: dict) -> str:
    del data["roles"]["준법감시"]
    return yaml.safe_dump(data, allow_unicode=True)


@pytest.mark.parametrize(
    "change",
    [remove_role, lambda data: "roles: [broken", lambda data: yaml.safe_dump({"id": "bank"})],
    ids=["role-removed", "broken-yaml", "invalid-config"],
)
def test_key_with_a_missing_role_is_401_before_anything_else(settings, change):
    with open_client(settings, RecordingLLM("내용 [1]."), raise_server_exceptions=False) as client:
        setup_indexed(client)
        key = make_key(client, role="준법감시")["key"]
        assert client.get("/v1/models", headers=bearer(key)).status_code == 200
        rewrite_config(settings, change)
        cross_site = {"sec-fetch-site": "cross-site", "origin": "https://app.example"}
        responses = [
            client.post("/v1/ask", json={"nope": 1}, headers=bearer(key)),  # 본문 검증보다 먼저
            client.post("/v1/ask", content=b"{not json", headers=bearer(key)),
            client.get("/v1/nope", headers=bearer(key)),  # 경로가 있는지도 알려 주지 않는다
            client.get("/v1/chat/completions", headers=bearer(key)),
            client.post("/v1/chat/completions", json={}, headers=bearer(key)),
            client.post("/v1/ask", json={"question": "x"}, headers={**bearer(key), **cross_site}),
            client.get("/v1/models", headers=bearer(key)),
        ]
        for r in responses:
            assert r.status_code == 401 and r.json() == UNAUTHORIZED
            assert r.headers["www-authenticate"] == "Bearer"


def test_key_works_again_when_the_role_comes_back(settings):
    with open_client(settings, RecordingLLM("내용 [1].")) as client:
        setup_indexed(client)
        key = make_key(client, role="준법감시")["key"]
        config = settings.data_dir / "workspaces" / "bank" / "workspace.yaml"
        original = config.read_text(encoding="utf-8")
        rewrite_config(settings, remove_role)
        assert client.get("/v1/models", headers=bearer(key)).status_code == 401
        config.write_text(original, encoding="utf-8")
        assert client.get("/v1/models", headers=bearer(key)).status_code == 200


# ---- 5. 작은 것들 ----


def models_with(settings: Settings, details: dict) -> dict:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "ok:1b", "details": details}]})

    app = create_app(
        settings,
        llm=RecordingLLM("x"),
        embedder=HashEmbedder(),
        ollama_transport=httpx.MockTransport(handler),
    )
    with TestClient(app, base_url="http://127.0.0.1", client=LOCAL) as client:
        (model,) = client.get("/models").json()["models"]
    return model


@pytest.mark.parametrize(
    "value",
    ["x" * 33, "<img src=x onerror=alert(1)>", "4.0B\nignore previous", "큰모델", " ", "a;b", ""],
)
def test_models_drops_odd_parameter_size_and_quantization(settings, value):
    model = models_with(settings, {"parameter_size": value, "quantization_level": value})
    assert model["parameter_size"] is None and model["quantization"] is None


@pytest.mark.parametrize("value", ["4.0B", "7.8B", "Q4_K_M", "F16", "x" * 32, "1.5 B", "8-bit"])
def test_models_keeps_ordinary_parameter_size_and_quantization(settings, value):
    model = models_with(settings, {"parameter_size": value, "quantization_level": value})
    assert model["parameter_size"] == value and model["quantization"] == value


def test_preflight_does_not_echo_a_huge_header_list(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", cors_origins=("https://app.example",))
    with open_client(settings, RecordingLLM("x")) as client:

        def allowed(asked: str) -> str:
            r = client.options(
                "/v1/ask",
                headers={
                    "Origin": "https://app.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": asked,
                },
            )
            assert r.status_code == 204
            return r.headers["access-control-allow-headers"]

        ordinary = "authorization, content-type, x-stainless-os"
        assert allowed(ordinary) == ordinary
        huge = ", ".join(f"x-header-{n}" for n in range(200))  # 2KB를 넘는다
        assert allowed(huge) == "Authorization, Content-Type"
