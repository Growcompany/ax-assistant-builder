"""모델 선택(Task 15) API 테스트: GET /models, 워크스페이스의 llm_model, 모델별 LLM 클라이언트.

실제 Ollama를 부르지 않는다. Ollama 응답은 httpx.MockTransport로 흉내 낸다.
"""

import base64
import json

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from test_app import WS, RecordingLLM, ask, setup_indexed

from axbuilder.api.app import create_app
from axbuilder.generation.answer import REFUSAL_TEXT
from axbuilder.models import LLMResponse
from axbuilder.retrieval.dense import HashEmbedder
from axbuilder.settings import Settings
from axbuilder.workspace import Workspace, WorkspaceStore

DEFAULT_MODEL = Settings().local_model
TAGS = {
    "models": [
        {
            "name": "qwen3.5:4b-q4_K_M",
            "model": "qwen3.5:4b-q4_K_M",
            "size": 3_300_000_000,
            "details": {"parameter_size": "4.0B", "quantization_level": "Q4_K_M"},
        },
        {"name": "exaone:7.8b", "size": 4_800_000_000, "details": {"parameter_size": "7.8B"}},
        {"name": "hf.co/org/model-GGUF:Q4_K_M"},
    ]
}


def tags_transport(body=TAGS, status: int = 200, seen: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        assert request.method == "GET" and request.url.path == "/api/tags"
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


def raising_transport(exc: Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def open_client(settings: Settings, **kwargs) -> TestClient:
    """create_app에 넘길 인자를 그대로 받는다(llm, llm_factory, ollama_transport)."""
    kwargs.setdefault("embedder", HashEmbedder())
    if "llm" not in kwargs and "llm_factory" not in kwargs:
        kwargs["llm"] = RecordingLLM("내용 [1].")
    return TestClient(create_app(settings, **kwargs), base_url="http://127.0.0.1")


class NamedLLM:
    """자기 모델 이름을 응답에 담는 가짜 LLM."""

    def __init__(self, model: str, reply: str = "내용 [1].") -> None:
        self.model = model
        self.reply = reply
        self.calls = 0

    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text=self.reply, model=self.model, input_tokens=0, output_tokens=0, latency_ms=0.0
        )


class Factory:
    """모델 이름 → NamedLLM. 만든 순서를 기록한다."""

    def __init__(self, reply: str = "내용 [1].") -> None:
        self.reply = reply
        self.created: list[str] = []
        self.clients: dict[str, NamedLLM] = {}

    def __call__(self, model: str) -> NamedLLM:
        self.created.append(model)
        self.clients[model] = NamedLLM(model, self.reply)
        return self.clients[model]


# ---- GET /models ----


def test_models_lists_installed_ollama_models(settings):
    with open_client(settings, ollama_transport=tags_transport()) as client:
        r = client.get("/models")
    assert r.status_code == 200
    assert r.json() == {
        "available": True,
        "default": DEFAULT_MODEL,
        "models": [
            {
                "name": "exaone:7.8b",
                "size_bytes": 4_800_000_000,
                "parameter_size": "7.8B",
                "quantization": None,
            },
            {
                "name": "hf.co/org/model-GGUF:Q4_K_M",
                "size_bytes": None,
                "parameter_size": None,
                "quantization": None,
            },
            {
                "name": "qwen3.5:4b-q4_K_M",
                "size_bytes": 3_300_000_000,
                "parameter_size": "4.0B",
                "quantization": "Q4_K_M",
            },
        ],
        "hint": None,
    }


def test_models_when_ollama_is_down(settings):
    transport = raising_transport(httpx.ConnectError("connection refused"))
    with open_client(settings, ollama_transport=transport) as client:
        r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["models"] == []
    assert body["default"] == DEFAULT_MODEL
    assert body["hint"].startswith("Ollama가 켜져 있지 않습니다")
    assert "ollama serve" in body["hint"]


def test_models_empty_list_tells_how_to_pull_the_default(settings):
    with open_client(settings, ollama_transport=tags_transport({"models": []})) as client:
        body = client.get("/models").json()
    assert body["available"] is True and body["models"] == []
    assert f"ollama pull {DEFAULT_MODEL}" in body["hint"]


def test_models_hint_when_default_model_is_not_installed(settings):
    tags = {"models": [{"name": "exaone:7.8b"}]}
    with open_client(settings, ollama_transport=tags_transport(tags)) as client:
        body = client.get("/models").json()
    assert body["available"] is True and [m["name"] for m in body["models"]] == ["exaone:7.8b"]
    assert f"ollama pull {DEFAULT_MODEL}" in body["hint"]


def test_models_default_without_tag_matches_latest(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", local_model="exaone")
    tags = {"models": [{"name": "exaone:latest"}]}
    with open_client(settings, ollama_transport=tags_transport(tags)) as client:
        body = client.get("/models").json()
    assert body["default"] == "exaone" and body["hint"] is None


@pytest.mark.parametrize(
    "body",
    [
        b"<html>not json",
        b"[1, 2]",
        b'{"models": "nope"}',
        b'{"other": []}',
        b'{"models": [{"size": 1}, "x", {"name": 5}]}',
    ],
)
def test_models_broken_response_is_unavailable(settings, body):
    with open_client(settings, ollama_transport=tags_transport(body)) as client:
        r = client.get("/models")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is False and data["models"] == []
    assert "모델 목록을 읽지 못했습니다" in data["hint"]
    assert "not json" not in data["hint"]  # 받은 응답을 되돌려 주지 않는다


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.ReadError("reset"),
    ],
)
def test_models_timeout_or_transport_error_is_unavailable(settings, exc):
    with open_client(settings, ollama_transport=raising_transport(exc)) as client:
        r = client.get("/models")
    assert r.status_code == 200
    assert r.json()["available"] is False and r.json()["hint"]


def test_models_http_error_is_unavailable(settings):
    transport = tags_transport({"error": "boom"}, status=500)
    with open_client(settings, ollama_transport=transport) as client:
        body = client.get("/models").json()
    assert body["available"] is False and "모델 목록을 읽지 못했습니다" in body["hint"]


def test_models_skips_names_that_cannot_be_chosen(settings):
    # 이름 형식에 맞지 않는 항목은 PATCH로 고를 수 없으므로 목록에 싣지 않는다
    tags = {"models": [{"name": "ok:1b"}, {"name": "bad name"}, {"name": ""}, {"name": "a" * 129}]}
    with open_client(settings, ollama_transport=tags_transport(tags)) as client:
        body = client.get("/models").json()
    assert [m["name"] for m in body["models"]] == ["ok:1b"]


def test_models_ignores_wrong_typed_details(settings):
    tags = {"models": [{"name": "ok:1b", "size": "big", "details": ["x"]}]}
    with open_client(settings, ollama_transport=tags_transport(tags)) as client:
        body = client.get("/models").json()
    assert body["models"] == [
        {"name": "ok:1b", "size_bytes": None, "parameter_size": None, "quantization": None}
    ]


def test_models_request_moves_url_credentials_to_auth(tmp_path, monkeypatch):
    # PR #19: 계정 정보는 URL이 아니라 Basic 인증으로 보내고, 프록시 환경변수를 따르지 않는다
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    settings = Settings(data_dir=tmp_path / "data", ollama_url="http://user:s3cret@ollama.test")
    seen: list[httpx.Request] = []
    with open_client(settings, ollama_transport=tags_transport(seen=seen)) as client:
        assert client.get("/models").json()["available"] is True
    (request,) = seen
    assert str(request.url) == "http://ollama.test/api/tags"
    expected = "Basic " + base64.b64encode(b"user:s3cret").decode()
    assert request.headers["authorization"] == expected


def test_models_hint_hides_url_credentials(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", ollama_url="http://user:s3cret@ollama.test")
    transport = raising_transport(httpx.ReadError("failed http://user:s3cret@ollama.test/api/tags"))
    with open_client(settings, ollama_transport=transport) as client:
        r = client.get("/models")
    assert r.json()["available"] is False
    assert "s3cret" not in r.text and "user:" not in r.text


def test_models_in_cloud_mode_lists_the_configured_claude_model(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", llm_mode="cloud")
    # Ollama는 부르지 않는다(부르면 transport가 실패한다)
    transport = raising_transport(AssertionError("must not call Ollama in cloud mode"))
    with open_client(settings, ollama_transport=transport) as client:
        body = client.get("/models").json()
    assert body["available"] is True and body["default"] == settings.claude_model
    assert body["models"] == [
        {
            "name": settings.claude_model,
            "size_bytes": None,
            "parameter_size": None,
            "quantization": None,
        }
    ]
    assert "고를 수 없습니다" in body["hint"]


def test_models_rejects_foreign_host(settings):
    with open_client(settings, ollama_transport=tags_transport()) as client:
        assert client.get("/models", headers={"host": "attacker.example"}).status_code == 403


# ---- llm_model 저장·조회 ----


def test_create_workspace_without_llm_model_uses_server_default(settings):
    with open_client(settings) as client:
        r = client.post("/workspaces", json=WS)
        assert r.status_code == 201 and r.json() == {**WS, "llm_model": None}
        assert client.get("/workspaces/bank").json()["llm_model"] is None
        assert client.get("/workspaces").json()[0]["llm_model"] is None


def test_create_workspace_with_llm_model(settings):
    with open_client(settings) as client:
        r = client.post("/workspaces", json={**WS, "llm_model": "exaone:7.8b"})
        assert r.status_code == 201 and r.json()["llm_model"] == "exaone:7.8b"
        assert client.get("/workspaces/bank").json()["llm_model"] == "exaone:7.8b"
    with open_client(settings) as client:  # 재시작 뒤에도
        assert client.get("/workspaces").json()[0]["llm_model"] == "exaone:7.8b"


BAD_MODEL_NAMES = [
    "",
    " ",
    "bad name",
    "-leading",
    ".leading",
    "a" * 129,
    "x\nollama pull evil",
    "x;rm -rf",
    "x`id`",
    "x$(id)",
    "모델",
    "x‮",
    5,
    ["a"],
]


@pytest.mark.parametrize("bad", BAD_MODEL_NAMES)
def test_create_workspace_rejects_bad_model_name(settings, bad):
    with open_client(settings) as client:
        assert client.post("/workspaces", json={**WS, "llm_model": bad}).status_code == 422
        assert client.get("/workspaces").json() == []


@pytest.mark.parametrize(
    "name", ["a", "qwen3.5:4b-q4_K_M", "hf.co/org/model-GGUF:Q4_K_M", "A" + "b" * 127]
)
def test_patch_sets_llm_model(settings, name):
    with open_client(settings) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        r = client.patch("/workspaces/bank", json={"llm_model": name})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {
            "id",
            "name",
            "industry",
            "roles",
            "llm_model",
            "documents",
            "index",
        }
        assert body["llm_model"] == name and body["id"] == "bank" and body["roles"] == WS["roles"]
        assert client.get("/workspaces/bank").json()["llm_model"] == name


def test_patch_null_returns_to_server_default_and_keeps_other_fields(settings):
    with open_client(settings) as client:
        setup_indexed(client)
        assert (
            client.patch("/workspaces/bank", json={"llm_model": "exaone:7.8b"}).status_code == 200
        )
        r = client.patch("/workspaces/bank", json={"llm_model": None})
        assert r.status_code == 200 and r.json()["llm_model"] is None
        body = client.get("/workspaces/bank").json()
        assert body["name"] == WS["name"] and body["industry"] == WS["industry"]
        assert body["roles"] == WS["roles"] and body["documents"] == 1
        assert body["index"] == {"state": "ready", "chunks": 3}  # 모델을 바꿔도 색인은 그대로


@pytest.mark.parametrize("bad", BAD_MODEL_NAMES)
def test_patch_rejects_bad_model_name(settings, bad):
    with open_client(settings) as client:
        assert client.post("/workspaces", json={**WS, "llm_model": "keep:1b"}).status_code == 201
        assert client.patch("/workspaces/bank", json={"llm_model": bad}).status_code == 422
        assert client.get("/workspaces/bank").json()["llm_model"] == "keep:1b"


@pytest.mark.parametrize("body", [{}, {"llm_model": "a:1b", "name": "새 이름"}, {"roles": {}}, []])
def test_patch_accepts_only_llm_model(settings, body):
    # 이름·역할은 PATCH로 바꿀 수 없다. 모르는 필드를 조용히 무시하지 않는다
    with open_client(settings) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        assert client.patch("/workspaces/bank", json=body).status_code == 422
        saved = client.get("/workspaces/bank").json()
        assert saved["name"] == WS["name"] and saved["llm_model"] is None


@pytest.mark.parametrize("workspace_id", ["nope", "%2e%2e", "con", "BANK"])
def test_patch_unknown_workspace_404(settings, workspace_id):
    with open_client(settings) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        r = client.patch(f"/workspaces/{workspace_id}", json={"llm_model": "a:1b"})
        assert r.status_code == 404
        assert client.get("/workspaces/bank").json()["llm_model"] is None


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "attacker.example"},
        {"sec-fetch-site": "cross-site"},
        {"origin": "https://attacker.example"},
    ],
)
def test_patch_rejects_foreign_host_and_cross_site(settings, headers):
    with open_client(settings) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        r = client.patch("/workspaces/bank", json={"llm_model": "a:1b"}, headers=headers)
        assert r.status_code == 403
        assert client.get("/workspaces/bank").json()["llm_model"] is None


def test_patch_leaves_no_temporary_files(settings):
    with open_client(settings) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        assert client.patch("/workspaces/bank", json={"llm_model": "a:1b"}).status_code == 200
    folder = settings.data_dir / "workspaces" / "bank"
    assert [p.name for p in folder.iterdir()] == ["workspace.yaml"]
    saved = yaml.safe_load((folder / "workspace.yaml").read_text(encoding="utf-8"))
    assert saved == {**WS, "llm_model": "a:1b"}


def test_old_workspace_file_without_llm_model_still_loads(settings):
    # 필드가 생기기 전에 만든 workspace.yaml
    folder = settings.data_dir / "workspaces" / "bank"
    folder.mkdir(parents=True)
    (folder / "workspace.yaml").write_text(
        yaml.safe_dump(WS, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    assert WorkspaceStore(settings.data_dir / "workspaces").get("bank").llm_model is None
    with open_client(settings) as client:
        assert client.get("/workspaces/bank").json()["llm_model"] is None
        assert client.patch("/workspaces/bank", json={"llm_model": "a:1b"}).status_code == 200
        assert client.get("/workspaces").json()[0]["llm_model"] == "a:1b"


def test_workspace_model_field_is_optional_for_other_callers():
    # eval CLI·scripts처럼 llm_model 없이 만드는 곳이 그대로 돈다
    ws = Workspace(id="bank", name="은행", industry="finance", roles={"a": ["public"]})
    assert ws.llm_model is None
    with pytest.raises(ValueError):
        Workspace(id="bank", name="은행", industry="finance", roles={}, llm_model="bad name")


# ---- ask: 워크스페이스의 모델로 답한다 ----


def test_ask_uses_workspace_model_and_reports_it(settings):
    factory = Factory()
    with open_client(settings, llm_factory=factory) as client:
        setup_indexed(client)
        first = ask(client).json()
        assert first["model"] == DEFAULT_MODEL  # llm_model이 없으면 서버 기본 모델

        assert (
            client.patch("/workspaces/bank", json={"llm_model": "exaone:7.8b"}).status_code == 200
        )
        second = ask(client).json()
        assert second["model"] == "exaone:7.8b" and second["refused"] is False
        assert factory.clients["exaone:7.8b"].calls == 1

        assert client.patch("/workspaces/bank", json={"llm_model": None}).status_code == 200
        assert ask(client).json()["model"] == DEFAULT_MODEL
    assert factory.clients[DEFAULT_MODEL].calls == 2


def test_llm_clients_are_made_once_per_model(settings):
    factory = Factory()
    with open_client(settings, llm_factory=factory) as client:
        setup_indexed(client)
        assert client.post("/workspaces", json={**WS, "id": "other"}).status_code == 201
        for _ in range(3):
            assert ask(client).status_code == 200
        assert client.patch("/workspaces/bank", json={"llm_model": "b:1b"}).status_code == 200
        for _ in range(2):
            assert ask(client).status_code == 200
    assert sorted(factory.created) == sorted({DEFAULT_MODEL, "b:1b"})
    assert len(factory.created) == 2


def test_two_workspaces_answer_with_their_own_models(settings):
    factory = Factory()
    with open_client(settings, llm_factory=factory) as client:
        setup_indexed(client)
        assert client.patch("/workspaces/bank", json={"llm_model": "a:1b"}).status_code == 200
        assert client.post("/workspaces", json={**WS, "id": "card"}).status_code == 201
        assert client.post("/workspaces/card/index").status_code == 200
        assert ask(client).json()["model"] == "a:1b"
        # 문서가 없어 LLM을 부르지 않고 거절해도, 답할 모델의 이름은 알려 준다
        refused = ask(client, ws="card").json()
        assert refused["refused"] is True and refused["model"] == DEFAULT_MODEL


def test_injected_llm_answers_for_every_model(settings):
    # 기존 테스트의 방식(create_app(settings, llm=가짜)): 모델 설정과 무관하게 그 LLM이 답한다
    llm = RecordingLLM("내용 [1].")
    with open_client(settings, llm=llm) as client:
        setup_indexed(client)
        assert ask(client).json()["model"] == "fake"
        assert client.patch("/workspaces/bank", json={"llm_model": "a:1b"}).status_code == 200
        assert ask(client).json()["model"] == "fake"
    assert len(llm.calls) == 2


def test_refusal_reports_the_model_that_answered(settings):
    factory = Factory(reply=REFUSAL_TEXT)
    with open_client(settings, llm_factory=factory) as client:
        setup_indexed(client)
        assert client.patch("/workspaces/bank", json={"llm_model": "a:1b"}).status_code == 200
        body = ask(client).json()
    assert body["refused"] is True and body["model"] == "a:1b"


def test_cloud_mode_ignores_workspace_model(tmp_path):
    # 로컬 모델 이름을 Claude API에 보내지 않는다
    settings = Settings(data_dir=tmp_path / "data", llm_mode="cloud")
    factory = Factory()
    with open_client(settings, llm_factory=factory) as client:
        setup_indexed(client)
        assert (
            client.patch("/workspaces/bank", json={"llm_model": "exaone:7.8b"}).status_code == 200
        )
        assert ask(client).json()["model"] == settings.claude_model
    assert factory.created == [settings.claude_model]


def test_cloud_mode_reports_the_model_that_really_answered(tmp_path):
    # Claude는 대체 모델이 답하면 그 이름을 돌려준다. 응답의 model은 실제로 답한 쪽
    settings = Settings(data_dir=tmp_path / "data", llm_mode="cloud")
    with open_client(settings, llm_factory=lambda name: NamedLLM("claude-fallback")) as client:
        setup_indexed(client)
        assert ask(client).json()["model"] == "claude-fallback"


def chat_transport(seen: list[dict], missing: set[str] = frozenset()) -> httpx.MockTransport:
    """Ollama /api/chat 흉내. missing의 모델은 Ollama처럼 404로 답한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        seen.append(payload)
        model = payload["model"]
        if model in missing:
            error = f'model "{model}" not found, try pulling it first'
            return httpx.Response(404, json={"error": error})
        return httpx.Response(
            200, json={"model": model, "message": {"content": "내용 [1]."}, "done_reason": "stop"}
        )

    return httpx.MockTransport(handler)


def test_default_factory_makes_ollama_clients_per_model_with_same_options(settings):
    # 주입 없이(실행 때와 같은 경로) 워크스페이스 모델로 Ollama를 부른다. 옵션은 기본 모델과 같다
    seen: list[dict] = []
    app = create_app(settings, embedder=HashEmbedder(), ollama_transport=chat_transport(seen))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        setup_indexed(client)
        assert ask(client).json()["model"] == DEFAULT_MODEL
        assert (
            client.patch("/workspaces/bank", json={"llm_model": "exaone:7.8b"}).status_code == 200
        )
        assert ask(client).json()["model"] == "exaone:7.8b"
    assert [payload["model"] for payload in seen] == [DEFAULT_MODEL, "exaone:7.8b"]
    for payload in seen:
        assert payload["think"] is False and payload["stream"] is False
        assert payload["options"]["num_ctx"] == 8192
    assert seen[0]["options"] == seen[1]["options"]


def test_ask_with_model_missing_in_ollama_503_tells_how_to_pull(settings):
    seen: list[dict] = []
    transport = chat_transport(seen, missing={"exaone:7.8b"})
    app = create_app(settings, embedder=HashEmbedder(), ollama_transport=transport)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        setup_indexed(client)
        assert (
            client.patch("/workspaces/bank", json={"llm_model": "exaone:7.8b"}).status_code == 200
        )
        r = ask(client)
        assert r.status_code == 503
        assert "ollama pull exaone:7.8b" in r.json()["detail"]
        assert "찾지 못했습니다" in r.json()["detail"]
        # 다른 모델로 바꾸면 바로 답한다(서버를 다시 켤 필요가 없다)
        assert client.patch("/workspaces/bank", json={"llm_model": None}).status_code == 200
        assert ask(client).status_code == 200


def test_ask_when_ollama_is_down_503(settings):
    transport = raising_transport(httpx.ConnectError("connection refused"))
    app = create_app(settings, embedder=HashEmbedder(), ollama_transport=transport)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        setup_indexed(client)
        r = ask(client)
    assert r.status_code == 503 and "ollama serve" in r.json()["detail"]
