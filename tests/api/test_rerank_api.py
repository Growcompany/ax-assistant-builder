"""서버의 리랭커(Task 22): 설정으로 켜는 선택 기능. 가짜 Reranker만 쓴다(모델·네트워크 없음).

- 꺼져 있으면(none) 이전과 같은 경로, 같은 응답
- 켜면 워크스페이스 검색기를 RerankRetriever로 감싼다. 리랭커는 서버에 하나, 처음 질문 때 만든다
- 불러오지 못하면 503 (리랭커 없이 조용히 답하지 않는다)
- 권한 필터는 base 검색에서 걸린다. 리랭커는 걸러진 본문만 받는다
- 색인(index.json, stale 판정)과 무관하다
"""

import logging
import re

import pytest
from fastapi.testclient import TestClient
from test_app import (
    GUIDE_MD,
    GUIDE_META,
    LAW_TXT,
    META,
    WS,
    RecordingLLM,
    ask,
    open_client,
    setup_indexed,
    upload,
)
from test_keys_api import bearer, make_key

import axbuilder.api.app as app_module
from axbuilder.api.app import V1_SEARCH_UNAVAILABLE, create_app
from axbuilder.retrieval.dense import HashEmbedder
from axbuilder.retrieval.hybrid import HybridRetriever
from axbuilder.retrieval.rerank import RERANKER_MODELS, RerankerUnavailableError
from axbuilder.settings import Settings

INTERNAL_MARK = "내부전용표식XYZ"
QUESTION = "상담원 내부 절차 확인"
MIXED = (("law.txt", LAW_TXT, META), ("guide.md", GUIDE_MD, GUIDE_META))
LOAD_ERROR = "cannot load reranker model secret/reranker: OSError: no such model"


class ReversingReranker:
    """base 순위를 뒤집는 가짜 리랭커: 뒤에 온 본문일수록 점수가 높다."""

    name = "fake/reversing"

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, question: str, passages: list[str]) -> list[float]:
        self.calls.append((question, list(passages)))
        return [float(i) for i in range(len(passages))]


class BrokenReranker(ReversingReranker):
    def score(self, question: str, passages: list[str]) -> list[float]:
        raise RerankerUnavailableError(LOAD_ERROR)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def llm() -> RecordingLLM:
    return RecordingLLM("내용 [1].")


def open_reranked(settings: Settings, llm, reranker=None) -> TestClient:
    app = create_app(settings, llm=llm, embedder=HashEmbedder(), reranker=reranker)
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def prompt_articles(llm: RecordingLLM) -> list[str]:
    """프롬프트에 들어간 자료의 조항을 들어간 순서대로."""
    return re.findall(r"^\[\d+\] .* (제\S+)$", llm.last_user, re.M)


def comparable(response) -> dict:
    assert response.status_code == 200, response.text
    body = response.json()
    body.pop("latency_ms")
    return body


@pytest.fixture
def fake_cross_encoder(monkeypatch) -> list[ReversingReranker]:
    """설정으로 켠 서버가 만드는 CrossEncoderReranker를 가짜로 바꾼다. 만든 것을 차례로 모은다."""
    made: list[ReversingReranker] = []

    def make(model_name: str) -> ReversingReranker:
        reranker = ReversingReranker()
        reranker.name = model_name
        made.append(reranker)
        return reranker

    monkeypatch.setattr(app_module, "CrossEncoderReranker", make)
    return made


# ---- 켜고 끄기 ----


def test_reranker_changes_prompt_order_and_citations(settings, llm):
    with open_client(settings, llm) as client:
        setup_indexed(client)
        plain = comparable(ask(client))
        base_order = prompt_articles(llm)
    assert len(base_order) == 3

    reranker = ReversingReranker()
    with open_reranked(settings, llm, reranker) as client:
        reranked = comparable(ask(client))
    assert prompt_articles(llm) == base_order[::-1]
    # LLM은 늘 [1]을 인용한다: 1번 자료가 바뀌었으니 인용과 원문도 바뀐다
    assert reranked["citations"][0]["article"] == base_order[-1]
    assert plain["citations"][0]["article"] == base_order[0]
    assert reranked["passages"][0]["chunk_id"] == reranked["citations"][0]["chunk_id"]
    assert reranked["citations"] != plain["citations"]
    ((question, passages),) = reranker.calls
    assert question == "보호 한도?" and len(passages) == 3


def test_reranker_none_is_the_same_path_and_response(settings, llm, monkeypatch):
    def must_not_wrap(*args, **kwargs):
        raise AssertionError("reranker is off: nothing may be wrapped or loaded")

    assert settings.reranker == "none"
    with open_client(settings, llm) as client:
        setup_indexed(client)
        monkeypatch.setattr(app_module, "RerankRetriever", must_not_wrap)
        monkeypatch.setattr(app_module, "CrossEncoderReranker", must_not_wrap)
        default = comparable(ask(client))
        default_prompt = llm.last_user
        retriever = client.app.state.service._retriever("bank")
        assert type(retriever) is app_module._WorkspaceRetriever
    explicit = Settings(data_dir=settings.data_dir, reranker="none")
    with open_reranked(explicit, llm) as client:
        assert comparable(ask(client)) == default
        assert llm.last_user == default_prompt
    assert set(default) == {"answer", "citations", "refused", "passages", "model"}


def test_response_shape_is_the_same_with_a_reranker(settings, llm):
    with open_reranked(settings, llm, ReversingReranker()) as client:
        setup_indexed(client)
        body = ask(client).json()
        assert set(body) == {"answer", "citations", "refused", "passages", "model", "latency_ms"}
        # 화면·상태 표시는 이번 범위가 아니다: 조회 응답과 /health에 리랭커가 나타나지 않는다
        assert client.get("/health").json() == {"status": "ok"}
        assert "rerank" not in client.get("/workspaces/bank").text
        assert "rerank" not in client.get("/workspaces").text


# ---- 설정으로 켠 서버 ----


def test_setting_builds_one_lazy_reranker_for_all_workspaces(settings, llm, fake_cross_encoder):
    on = settings.model_copy(update={"reranker": "kure-nano"})
    with open_reranked(on, llm) as client:
        setup_indexed(client)
        assert client.post("/workspaces", json={**WS, "id": "card"}).status_code == 201
        assert upload(client, "law.txt", LAW_TXT, META, ws="card").status_code == 201
        assert client.post("/workspaces/card/index").status_code == 200
        # 색인까지는 리랭커가 필요 없다: 처음 질문할 때 만든다
        assert fake_cross_encoder == []
        assert ask(client).status_code == 200
        assert ask(client, ws="card").status_code == 200
        assert ask(client).status_code == 200
        (reranker,) = fake_cross_encoder  # 워크스페이스가 둘이어도 하나
        assert reranker.name == RERANKER_MODELS["kure-nano"] == "nlpai-lab/KURE-Reranker-nano"
        assert len(reranker.calls) == 3


def test_injected_reranker_wins_over_the_setting(settings, llm, fake_cross_encoder):
    on = settings.model_copy(update={"reranker": "bge-v2-m3"})
    injected = ReversingReranker()
    with open_reranked(on, llm, injected) as client:
        setup_indexed(client)
        assert ask(client).status_code == 200
    assert len(injected.calls) == 1 and fake_cross_encoder == []


@pytest.mark.parametrize(("candidates", "expected"), [(2, 5), (7, 7), (30, 30)])
def test_candidates_setting_is_what_the_base_retriever_is_asked_for(
    settings, llm, monkeypatch, candidates, expected
):
    asked: list[int] = []
    search = HybridRetriever.search

    def spy(self, query, k=5, access_levels=None):
        asked.append(k)
        return search(self, query, k=k, access_levels=access_levels)

    monkeypatch.setattr(HybridRetriever, "search", spy)
    on = settings.model_copy(update={"rerank_candidates": candidates})
    reranker = ReversingReranker()
    with open_reranked(on, llm, reranker) as client:
        setup_indexed(client)
        assert ask(client).status_code == 200
    # answer()는 k=5를 청한다. 후보 수가 그보다 작으면 base에서 k개를 받는다(RerankRetriever)
    assert asked == [expected]


def test_candidates_limit_what_the_reranker_sees(settings, llm, monkeypatch):
    # answer()의 k(5)보다 문서가 많아야 후보 수가 드러난다: k를 1로 줄여 본다
    answer = app_module.answer
    monkeypatch.setattr(app_module, "answer", lambda question, *args: answer(question, *args, k=1))
    on = settings.model_copy(update={"rerank_candidates": 2})
    reranker = ReversingReranker()
    with open_reranked(on, llm, reranker) as client:
        assert setup_indexed(client) == 3
        assert ask(client).status_code == 200
    ((_, passages),) = reranker.calls
    assert len(passages) == 2
    assert len(prompt_articles(llm)) == 1


def test_without_reranker_the_base_retriever_is_asked_for_k(settings, llm, monkeypatch):
    asked: list[int] = []
    search = HybridRetriever.search

    def spy(self, query, k=5, access_levels=None):
        asked.append(k)
        return search(self, query, k=k, access_levels=access_levels)

    monkeypatch.setattr(HybridRetriever, "search", spy)
    with open_client(settings, llm) as client:
        setup_indexed(client)
        assert ask(client).status_code == 200
    assert asked == [5]


# ---- 실패: 조용히 리랭커 없이 답하지 않는다 ----


def test_reranker_unavailable_is_503_with_cause_and_fix(settings, llm, caplog):
    with open_reranked(settings, llm, BrokenReranker()) as client:
        setup_indexed(client)
        with caplog.at_level(logging.WARNING, logger="axbuilder.api.app"):
            r = ask(client)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail.startswith("리랭커를 불러오지 못했습니다: ")
    assert "secret/reranker" in detail and "OSError" in detail
    assert 'pip install -e ".[ml]"' in detail and "AXB_RERANKER=none" in detail
    assert llm.calls == []  # 리랭커 없이 답을 만들지 않았다
    assert "secret/reranker" in caplog.text


def test_reranker_unavailable_from_the_setting_is_503(settings, llm, monkeypatch):
    class Unloadable(BrokenReranker):
        def __init__(self, model_name: str) -> None:
            super().__init__()
            self.name = model_name

    monkeypatch.setattr(app_module, "CrossEncoderReranker", Unloadable)
    on = settings.model_copy(update={"reranker": "kure-nano"})
    with open_reranked(on, llm) as client:
        setup_indexed(client)  # 색인은 리랭커 없이 된다
        assert ask(client).status_code == 503
        assert ask(client).status_code == 503  # 다음 질문도 그대로다(몰래 꺼지지 않는다)
        assert client.get("/health").status_code == 200
    assert llm.calls == []


def test_reranker_error_does_not_show_local_paths(settings, llm):
    class PathLeaking(ReversingReranker):
        def score(self, question: str, passages: list[str]) -> list[float]:
            raise RerankerUnavailableError(
                r"cannot load reranker model x/y: OSError: C:\Users\someone\.cache\hf\x is missing"
            )

    with open_reranked(settings, llm, PathLeaking()) as client:
        setup_indexed(client)
        r = ask(client)
    assert r.status_code == 503 and "someone" not in r.text and "<path>" in r.text


def test_v1_reranker_unavailable_is_503_with_the_fixed_message(settings, llm, caplog):
    with open_reranked(settings, llm, BrokenReranker()) as client:
        setup_indexed(client)
        key = make_key(client)["key"]
        chat = {"model": "bank", "messages": [{"role": "user", "content": "보호 한도?"}]}
        with caplog.at_level(logging.WARNING, logger="axbuilder.api.app"):
            responses = [
                client.post("/v1/ask", json={"question": "보호 한도?"}, headers=bearer(key)),
                client.post("/v1/chat/completions", json=chat, headers=bearer(key)),
                client.post(
                    "/v1/chat/completions", json={**chat, "stream": True}, headers=bearer(key)
                ),
            ]
    for r in responses:
        assert r.status_code == 503
        assert r.json() == {
            "error": {
                "message": V1_SEARCH_UNAVAILABLE,
                "type": "server_error",
                "code": "reranker_unavailable",
                "param": None,
            }
        }
        # 키로 부르는 쪽에는 모델 이름·원인·설치 방법을 주지 않는다
        assert not any(leak in r.text for leak in ("secret", "OSError", "pip", "AXB_"))
    assert "secret/reranker" in caplog.text  # 상세는 서버 로그에
    assert llm.calls == []


# ---- 권한 ----


def test_reranker_only_gets_passages_the_role_may_read(settings, llm):
    reranker = ReversingReranker()
    with open_reranked(settings, llm, reranker) as client:
        setup_indexed(client, *MIXED)
        public = ask(client, QUESTION, role="상담원")
        assert public.status_code == 200 and INTERNAL_MARK not in public.text
        ((_, public_passages),) = reranker.calls
        assert public_passages and all(INTERNAL_MARK not in text for text in public_passages)
        assert INTERNAL_MARK not in llm.last_user

        # 같은 질문을 internal 역할로: 리랭커가 내부 문서도 받는다(위 단언이 빈말이 아니다)
        assert ask(client, QUESTION, role="준법감시").status_code == 200
        assert any(INTERNAL_MARK in text for text in reranker.calls[-1][1])

        # 볼 수 있는 등급이 없는 역할: 검색도 리랭크도 하지 않는다
        calls = len(reranker.calls)
        assert ask(client, QUESTION, role="견학생").json()["refused"] is True
        assert len(reranker.calls) == calls


def test_reranker_only_gets_public_passages_for_a_public_key(settings, llm):
    reranker = ReversingReranker()
    with open_reranked(settings, llm, reranker) as client:
        setup_indexed(client, *MIXED)
        key = make_key(client, role="상담원")["key"]
        r = client.post(
            "/v1/ask?role=준법감시",
            json={"question": QUESTION, "role": "준법감시"},
            headers=bearer(key),
        )
    assert r.status_code == 200 and INTERNAL_MARK not in r.text
    ((_, passages),) = reranker.calls
    assert passages and all(INTERNAL_MARK not in text for text in passages)


# ---- 색인과의 관계 ----


def index_info(settings: Settings) -> bytes:
    return (settings.data_dir / "workspaces" / "bank" / "index" / "index.json").read_bytes()


def test_turning_the_reranker_on_or_off_needs_no_reindex(settings, llm, fake_cross_encoder):
    with open_client(settings, llm) as client:  # 리랭커 없이 색인
        setup_indexed(client)
    built = index_info(settings)
    assert b"rerank" not in built

    on = settings.model_copy(update={"reranker": "bge-v2-m3", "rerank_candidates": 50})
    with open_reranked(on, llm) as client:  # 켜고 다시 띄움: 같은 색인을 그대로 연다
        assert client.get("/workspaces/bank").json()["index"] == {"state": "ready", "chunks": 3}
        assert ask(client).status_code == 200
        assert len(fake_cross_encoder) == 1
        assert client.get("/workspaces/bank").json()["index"] == {"state": "ready", "chunks": 3}
    assert index_info(settings) == built

    with open_client(settings, llm) as client:  # 다시 끔
        assert client.get("/workspaces/bank").json()["index"] == {"state": "ready", "chunks": 3}
        assert ask(client).status_code == 200
    assert index_info(settings) == built


def test_index_built_with_a_reranker_records_nothing_about_it(settings, llm, fake_cross_encoder):
    on = settings.model_copy(update={"reranker": "kure-nano"})
    with open_reranked(on, llm) as client:
        setup_indexed(client)
    info = index_info(settings)
    assert b"rerank" not in info and b"KURE" not in info
    assert fake_cross_encoder == []  # 색인은 리랭커를 만들지도 않는다
    with open_client(settings, llm) as client:
        assert client.get("/workspaces/bank").json()["index"] == {"state": "ready", "chunks": 3}
        assert ask(client).status_code == 200
