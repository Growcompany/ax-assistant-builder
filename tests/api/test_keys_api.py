"""API 키(Task 16): 발급·목록·폐기, 해시만 저장, 마지막 사용 시각.

키로 부르는 /v1/* 의 응답 모양은 test_openai_compat.py, 접근 규칙은 test_access_rules.py가 본다.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import pytest
from test_app import WS, RecordingLLM, open_client, setup_indexed

import axbuilder.api.keys as keys_module
from axbuilder.api.keys import KeyStore
from axbuilder.settings import Settings
from axbuilder.workspace import Workspace, WorkspaceStore

KEY_FORMAT = re.compile(r"axb_[A-Za-z0-9_-]{43}")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM("내용 [1].")


@pytest.fixture
def client(settings, llm):
    with open_client(settings, llm) as c:
        assert c.post("/workspaces", json=WS).status_code == 201
        yield c


def make_key(client, role="상담원", name="사내 포털", ws="bank") -> dict:
    r = client.post(f"/workspaces/{ws}/keys", json={"name": name, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def keys_file(settings: Settings, ws="bank") -> Path:
    return settings.data_dir / "workspaces" / ws / "keys.json"


def works(client, key: str) -> bool:
    return client.get("/v1/models", headers=bearer(key)).status_code == 200


# ---- 발급 ----


def test_create_key_returns_the_key_once(client):
    r = client.post("/workspaces/bank/keys", json={"name": " 사내 포털 ", "role": "상담원"})
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"key_id", "name", "role", "key", "created_at"}
    assert body["name"] == "사내 포털" and body["role"] == "상담원"
    assert KEY_FORMAT.fullmatch(body["key"])
    assert re.fullmatch(r"[0-9a-f]{16}", body["key_id"])
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
    assert works(client, body["key"])


def test_keys_are_different_each_time(client):
    made = [make_key(client) for _ in range(5)]
    assert len({k["key"] for k in made}) == 5 and len({k["key_id"] for k in made}) == 5


def test_list_keys_never_shows_the_key(client):
    first = make_key(client, name="포털")
    second = make_key(client, role="준법감시", name="감사 도구")
    r = client.get("/workspaces/bank/keys")
    assert r.status_code == 200
    listed = r.json()
    assert [k["key_id"] for k in listed] == [first["key_id"], second["key_id"]]  # 만든 순서
    for item, made in zip(listed, [first, second], strict=True):
        assert set(item) == {"key_id", "name", "role", "created_at", "last_used_at", "prefix"}
        assert item["name"] == made["name"] and item["role"] == made["role"]
        assert item["created_at"] == made["created_at"] and item["last_used_at"] is None
        assert item["prefix"] == made["key"][:8] + "…"  # "axb_" + 4자
        assert made["key"] not in r.text and made["key"][8:] not in r.text


def test_list_keys_of_workspace_without_keys(client):
    assert client.get("/workspaces/bank/keys").json() == []


def test_only_the_hash_is_stored(client, settings):
    made = make_key(client)
    text = keys_file(settings).read_text(encoding="utf-8")
    assert made["key"] not in text and made["key"][8:] not in text
    assert hashlib.sha256(made["key"].encode()).hexdigest() in text
    (stored,) = json.loads(text)["keys"]
    assert set(stored) == {
        "key_id",
        "name",
        "role",
        "hash",
        "prefix",
        "created_at",
        "last_used_at",
    }
    # 데이터 폴더 어디에도 원문이 없다
    for path in settings.data_dir.rglob("*"):
        if path.is_file():
            assert made["key"].encode() not in path.read_bytes()


def test_key_never_appears_in_logs(client, caplog):
    with caplog.at_level(logging.DEBUG):
        made = make_key(client)
        assert works(client, made["key"])
        assert client.get("/v1/models", headers=bearer(made["key"] + "x")).status_code == 401
        assert client.get("/workspaces/bank/keys").status_code == 200
        assert client.delete(f"/workspaces/bank/keys/{made['key_id']}").status_code == 204
        assert client.get("/v1/models", headers=bearer(made["key"])).status_code == 401
    assert made["key"] not in caplog.text and made["key"][8:] not in caplog.text


def test_create_key_unknown_role_422(client, settings):
    r = client.post("/workspaces/bank/keys", json={"name": "x", "role": "없는역할"})
    assert r.status_code == 422 and "상담원" in r.json()["detail"]
    assert not keys_file(settings).exists()


@pytest.mark.parametrize(
    "body",
    [
        {"role": "상담원"},
        {"name": "x"},
        {"name": "", "role": "상담원"},
        {"name": "   ", "role": "상담원"},
        {"name": "a\nb", "role": "상담원"},
        {"name": "a‮b", "role": "상담원"},
        {"name": "a" * 101, "role": "상담원"},
        {"name": 5, "role": "상담원"},
        {"name": "x", "role": "상담원", "workspace": "other"},
        {"name": "x", "role": "상담원", "key": "axb_" + "a" * 43},
        [],
    ],
)
def test_create_key_bad_body_422(client, settings, body):
    assert client.post("/workspaces/bank/keys", json=body).status_code == 422
    assert client.get("/workspaces/bank/keys").json() == []


@pytest.mark.parametrize("ws", ["nope", "%2e%2e", "con", "BANK"])
def test_keys_of_unknown_workspace_404(client, ws):
    assert (
        client.post(f"/workspaces/{ws}/keys", json={"name": "x", "role": "상담원"}).status_code
        == 404
    )
    assert client.get(f"/workspaces/{ws}/keys").status_code == 404
    assert client.delete(f"/workspaces/{ws}/keys/{'0' * 16}").status_code == 404


def test_too_many_keys_409(client, monkeypatch):
    monkeypatch.setattr(keys_module, "MAX_KEYS_PER_WORKSPACE", 2)
    make_key(client)
    make_key(client)
    r = client.post("/workspaces/bank/keys", json={"name": "x", "role": "상담원"})
    assert r.status_code == 409 and len(client.get("/workspaces/bank/keys").json()) == 2


@pytest.mark.parametrize(
    "headers",
    [{"sec-fetch-site": "cross-site"}, {"origin": "https://attacker.example"}],
)
def test_key_management_keeps_the_cross_site_checks(client, headers):
    made = make_key(client)
    created = client.post(
        "/workspaces/bank/keys", json={"name": "x", "role": "준법감시"}, headers=headers
    )
    assert created.status_code == 403
    deleted = client.delete(f"/workspaces/bank/keys/{made['key_id']}", headers=headers)
    assert deleted.status_code == 403
    assert [k["key_id"] for k in client.get("/workspaces/bank/keys").json()] == [made["key_id"]]


# ---- 폐기 ----


def test_revoked_key_stops_working_at_once(client, settings):
    made = make_key(client)
    other = make_key(client, name="남는 키")
    assert works(client, made["key"])
    r = client.delete(f"/workspaces/bank/keys/{made['key_id']}")
    assert r.status_code == 204 and r.content == b""
    assert client.get("/v1/models", headers=bearer(made["key"])).status_code == 401
    assert works(client, other["key"])
    assert [k["key_id"] for k in client.get("/workspaces/bank/keys").json()] == [other["key_id"]]
    assert hashlib.sha256(made["key"].encode()).hexdigest() not in keys_file(settings).read_text(
        encoding="utf-8"
    )
    assert client.delete(f"/workspaces/bank/keys/{made['key_id']}").status_code == 404


@pytest.mark.parametrize("key_id", ["0" * 16, "nope", "%2e%2e", "A" * 16, "0" * 17])
def test_revoke_unknown_key_404(client, key_id):
    made = make_key(client)
    assert client.delete(f"/workspaces/bank/keys/{key_id}").status_code == 404
    assert works(client, made["key"])


def test_key_cannot_be_revoked_through_another_workspace(client):
    assert client.post("/workspaces", json={**WS, "id": "card"}).status_code == 201
    made = make_key(client)
    assert client.delete(f"/workspaces/card/keys/{made['key_id']}").status_code == 404
    assert works(client, made["key"])
    assert client.get("/workspaces/card/keys").json() == []


# ---- 재시작, 색인, 다른 프로세스 ----


def test_keys_survive_restart(settings, llm):
    with open_client(settings, llm) as client:
        setup_indexed(client)
        made = make_key(client)
        revoked = make_key(client)
        assert client.delete(f"/workspaces/bank/keys/{revoked['key_id']}").status_code == 204
    with open_client(settings, llm) as client:
        assert works(client, made["key"])
        assert client.get("/v1/models", headers=bearer(revoked["key"])).status_code == 401
        assert [k["key_id"] for k in client.get("/workspaces/bank/keys").json()] == [made["key_id"]]


def test_lookup_uses_the_memory_index_not_the_files(client, settings):
    # 요청마다 모든 워크스페이스의 keys.json을 훑지 않는다. 색인은 시작·발급·폐기 때만 바뀐다
    made = make_key(client)
    keys_file(settings).unlink()
    assert works(client, made["key"])


def test_lookup_compares_hashes_in_constant_time(client, monkeypatch):
    made = make_key(client)
    seen: list[tuple[str, str]] = []
    real = keys_module.hmac.compare_digest

    def recording(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(keys_module.hmac, "compare_digest", recording)
    assert works(client, made["key"])
    assert client.get("/v1/models", headers=bearer("axb_" + "a" * 43)).status_code == 401
    assert client.get("/v1/models", headers=bearer("short")).status_code == 401
    assert client.get("/v1/models").status_code == 401
    # 있는 키든 없는 키든 머리글이 없든 같은 길: 해시끼리 한 번 비교한다(원문을 비교하지 않는다)
    assert len(seen) == 4
    for a, b in seen:
        assert re.fullmatch(r"[0-9a-f]{64}", a) and re.fullmatch(r"[0-9a-f]{64}", b)


@pytest.mark.parametrize(
    "content",
    ["{not json", "[]", '{"keys": [{"key_id": "x"}]}', '{"keys": [{"hash": "abc"}]}'],
)
def test_unreadable_keys_file_fails_closed(settings, llm, content):
    with open_client(settings, llm) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        assert client.post("/workspaces", json={**WS, "id": "card"}).status_code == 201
        made = make_key(client)
        other = make_key(client, ws="card")
    keys_file(settings).write_text(content, encoding="utf-8")
    with open_client(settings, llm, raise_server_exceptions=False) as client:
        assert client.get("/v1/models", headers=bearer(made["key"])).status_code == 401
        assert works(client, other["key"])  # 다른 워크스페이스는 그대로
        # 깨진 파일을 빈 목록으로 보고 덮어쓰지 않는다
        assert client.get("/workspaces/bank/keys").status_code == 500
        r = client.post("/workspaces/bank/keys", json={"name": "x", "role": "상담원"})
        assert r.status_code == 500
    assert keys_file(settings).read_text(encoding="utf-8") == content


# ---- 마지막 사용 시각: 요청마다 파일을 쓰지 않는다 ----


def test_last_used_is_kept_in_memory_and_written_on_shutdown(settings, llm):
    with open_client(settings, llm) as client:
        assert client.post("/workspaces", json=WS).status_code == 201
        made = make_key(client)
        before = keys_file(settings).read_bytes()
        for _ in range(5):
            assert works(client, made["key"])
        (listed,) = client.get("/workspaces/bank/keys").json()
        assert datetime.fromisoformat(listed["last_used_at"]).tzinfo is not None
        assert keys_file(settings).read_bytes() == before  # 요청 5번에 파일은 그대로
    (stored,) = json.loads(keys_file(settings).read_text(encoding="utf-8"))["keys"]
    assert stored["last_used_at"] == listed["last_used_at"]  # 종료할 때 기록
    with open_client(settings, llm) as client:
        (again,) = client.get("/workspaces/bank/keys").json()
        assert again["last_used_at"] == listed["last_used_at"]


def test_failed_requests_do_not_count_as_use(client):
    made = make_key(client)
    assert client.get("/v1/models", headers=bearer(made["key"] + "x")).status_code == 401
    assert client.get("/workspaces/bank/keys").json()[0]["last_used_at"] is None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_last_used_is_flushed_at_most_once_per_interval(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.create(Workspace(id="bank", name="은행", industry="f", roles={"a": ["public"]}))
    clock = Clock()
    keys = KeyStore(store, flush_seconds=60.0, clock=clock)
    record, raw = keys.create("bank", "이름", "a")
    path = tmp_path / "bank" / "keys.json"

    def stored_last_used() -> str | None:
        return json.loads(path.read_text(encoding="utf-8"))["keys"][0]["last_used_at"]

    assert keys.authenticate(raw) == record
    keys.touch(record)
    clock.now += 59
    keys.touch(record)
    assert stored_last_used() is None
    clock.now += 2  # 마지막으로 쓴 지 60초가 지났다
    keys.touch(record)
    first = stored_last_used()
    assert first is not None
    written = path.read_bytes()
    clock.now += 30
    keys.touch(record)
    assert path.read_bytes() == written
    keys.flush()  # 종료 때
    assert stored_last_used() is not None
    # 다시 읽은 저장소도 같은 키를 안다
    assert KeyStore(store).authenticate(raw) is not None
    assert KeyStore(store).authenticate(raw[:-1] + ("a" if raw[-1] != "a" else "b")) is None


@pytest.mark.parametrize("presented", [None, "", "axb_", "x" * 10_000, "한글", "axb_" + "a" * 43])
def test_authenticate_rejects_anything_that_is_not_an_issued_key(tmp_path, presented):
    store = WorkspaceStore(tmp_path)
    store.create(Workspace(id="bank", name="은행", industry="f", roles={"a": ["public"]}))
    keys = KeyStore(store)
    keys.create("bank", "이름", "a")
    assert keys.authenticate(presented) is None
