"""색인과 청크 분할 설정 (M-2 Task 25).

index.json에 "chunking": {"max_chars": N | null}을 남긴다. 지금 설정(AXB_MAX_CHUNK_CHARS)과 다르면
화면의 색인 상태는 stale이고, 질문은 409(다시 색인)다. 다른 분할로 만든 청크로 조용히 답하지 않는다.
예전 index.json(필드 없음)은 나누지 않은 색인(max_chars = null)으로 읽는다.
"""

import json
from pathlib import Path

import pytest
from test_app import WS, RecordingLLM, ask, open_client, upload
from test_keys_api import bearer, make_key

from axbuilder.settings import Settings

# 제1조가 600자를 넘는다: max_chars 400이면 ①·②가 따로 → 청크 3개, 나누지 않으면 2개
LONG_LAW = (
    "제1조(목적) ① "
    + "가" * 300
    + "\n② "
    + "나" * 300
    + "\n제2조(정의) 보호 한도는 대통령령으로 정한다."
).encode()
LAW_META = {
    "doc_id": "law-1",
    "title": "예금자보호법(발췌)",
    "source": "tests",
    "doc_type": "law",
    "access_level": "public",
}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM("보호한도는 대통령령으로 정합니다 [1].")


def split(settings: Settings, max_chars: int | None) -> Settings:
    return settings.model_copy(update={"max_chunk_chars": max_chars})


def index_info_path(settings: Settings) -> Path:
    return settings.data_dir / "workspaces" / "bank" / "index" / "index.json"


def build(client) -> int:
    assert client.post("/workspaces", json=WS).status_code == 201
    assert upload(client, "law.txt", LONG_LAW, LAW_META).status_code == 201
    r = client.post("/workspaces/bank/index")
    assert r.status_code == 200
    return r.json()["chunks"]


def index_state(client) -> dict:
    return client.get("/workspaces/bank").json()["index"]


def rewrite_info(settings: Settings, change) -> None:
    path = index_info_path(settings)
    info = json.loads(path.read_text(encoding="utf-8"))
    change(info)
    path.write_text(json.dumps(info), encoding="utf-8")


@pytest.mark.parametrize(("max_chars", "chunks"), [(None, 2), (400, 3)])
def test_index_info_records_the_chunking(settings, llm, max_chars, chunks):
    with open_client(split(settings, max_chars), llm) as client:
        assert build(client) == chunks
        assert index_state(client) == {"state": "ready", "chunks": chunks}
        assert ask(client).status_code == 200
    info = json.loads(index_info_path(settings).read_text(encoding="utf-8"))
    assert info["chunking"] == {"max_chars": max_chars}
    assert info["chunks"] == chunks


@pytest.mark.parametrize(("built_with", "served_with"), [(None, 400), (400, None), (400, 600)])
def test_index_of_another_chunking_is_stale_and_asking_is_409(
    settings, llm, built_with, served_with
):
    with open_client(split(settings, built_with), llm) as client:
        chunks = build(client)
    with open_client(split(settings, served_with), llm) as client:
        assert index_state(client) == {"state": "stale", "chunks": chunks}
        r = ask(client)
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "청크 분할" in detail and "AXB_MAX_CHUNK_CHARS" in detail
        assert "POST /workspaces/bank/index" in detail
        # 다시 색인하면 지금 설정으로 만들어 준비된다
        assert client.post("/workspaces/bank/index").status_code == 200
        assert index_state(client)["state"] == "ready"
        assert ask(client).status_code == 200


def test_index_info_without_chunking_is_an_unsplit_index(settings, llm):
    # 이 필드를 넣기 전의 index.json: 그때는 나누지 않았으므로 기본 설정에서는 그대로 쓴다
    with open_client(settings, llm) as client:
        build(client)
    rewrite_info(settings, lambda info: info.pop("chunking"))
    with open_client(settings, llm) as client:
        assert index_state(client) == {"state": "ready", "chunks": 2}
        assert ask(client).status_code == 200
    with open_client(split(settings, 400), llm) as client:
        assert index_state(client) == {"state": "stale", "chunks": 2}
        assert ask(client).status_code == 409


@pytest.mark.parametrize(
    "chunking", [None, "800", [], {}, {"max_chars": "400"}, {"max_chars": None, "overlap": 0}]
)
def test_unknown_chunking_in_index_info_is_stale(settings, llm, chunking):
    # 어떤 분할로 만들었는지 알 수 없으면 ready라고 하지 않는다(다시 색인하라고 하는 쪽이 안전)
    with open_client(settings, llm) as client:
        build(client)
    rewrite_info(settings, lambda info: info.update(chunking=chunking))
    with open_client(settings, llm) as client:
        assert index_state(client) == {"state": "stale", "chunks": 2}
        assert ask(client).status_code == 409


V1_INDEX_NOT_READY = "색인이 준비되지 않았습니다. 이 어시스턴트의 관리자에게 문의하세요"


def test_v1_paths_also_refuse_an_index_of_another_chunking(settings, llm):
    # PR #24 리뷰: 화면·/ask와 같은 색인 열기(loaded_index)를 지나므로 /v1/*도 409다
    with open_client(settings, llm) as client:
        build(client)
        key = make_key(client)["key"]
    chat = {"model": "bank", "messages": [{"role": "user", "content": "보호 한도?"}]}
    with open_client(split(settings, 400), llm) as client:
        for path, body in (("/v1/ask", {"question": "보호 한도?"}), ("/v1/chat/completions", chat)):
            r = client.post(path, json=body, headers=bearer(key))
            assert r.status_code == 409, (path, r.text)
            error = r.json()["error"]
            assert (error["code"], error["message"]) == ("index_not_ready", V1_INDEX_NOT_READY)
            # 설정 이름·관리 API 경로는 키로 부르는 쪽(이 PC 밖일 수 있다)에 보내지 않는다
            assert "AXB_MAX_CHUNK_CHARS" not in r.text and "/workspaces/" not in r.text
