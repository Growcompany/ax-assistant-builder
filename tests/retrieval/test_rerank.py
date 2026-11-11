"""리랭커: 가짜 Reranker로 RerankRetriever를, 가짜 CrossEncoder로 CrossEncoderReranker를 본다."""

import importlib
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import axbuilder
from axbuilder.models import Chunk, ScoredChunk
from axbuilder.retrieval.hybrid import Retriever
from axbuilder.retrieval.rerank import (
    RERANKER_MODELS,
    CrossEncoderReranker,
    Reranker,
    RerankerUnavailableError,
    RerankRetriever,
)


def chunk(index: int, text: str, access_level: str = "public") -> Chunk:
    return Chunk(
        chunk_id=f"doc::{index:04d}",
        doc_id="doc",
        workspace_id="bank",
        title="테스트 문서",
        text=text,
        access_level=access_level,
    )


class FakeBase:
    """정해 둔 순위를 돌려주는 검색기. 받은 인자를 기록하고 권한 등급으로 거른다."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, int, list[str] | None]] = []

    def search(
        self, query: str, k: int = 5, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]:
        self.calls.append((query, k, access_levels))
        allowed = [
            c for c in self.chunks if access_levels is None or c.access_level in access_levels
        ]
        return [ScoredChunk(chunk=c, score=1.0 / (i + 1)) for i, c in enumerate(allowed[:k])]


class FakeReranker:
    """본문 → 점수 표로 채점한다(없는 본문은 0점). 받은 질문과 본문을 기록한다."""

    name = "fake"

    def __init__(self, scores: dict[str, float] | None = None) -> None:
        self.scores = scores or {}
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, question: str, passages: list[str]) -> list[float]:
        self.calls.append((question, list(passages)))
        return [self.scores.get(p, 0.0) for p in passages]


def texts(results: list[ScoredChunk]) -> list[str]:
    return [r.chunk.text for r in results]


a, b, c, d = (chunk(i, t) for i, t in enumerate("abcd"))


# --- 계획의 테스트 (명세) ---


def test_order_follows_reranker_scores():
    reranker = FakeReranker({"a": 0.1, "b": 0.9, "c": 0.5})
    results = RerankRetriever(FakeBase([a, b, c]), reranker).search("q", k=3)
    assert texts(results) == ["b", "c", "a"]
    assert [r.score for r in results] == [0.9, 0.5, 0.1]  # base 점수가 아니라 리랭커 점수


def test_ties_keep_base_order():
    reranker = FakeReranker({"a": 0.5, "b": 0.5, "c": 0.9, "d": 0.5})
    results = RerankRetriever(FakeBase([a, b, c, d]), reranker).search("q", k=4)
    assert texts(results) == ["c", "a", "b", "d"]


def test_k_cuts_after_reranking():
    # 앞 k개는 base의 앞 k개가 아니라 후보 전체를 다시 줄 세운 뒤의 앞 k개다
    reranker = FakeReranker({"d": 1.0})
    results = RerankRetriever(FakeBase([a, b, c, d]), reranker, candidates=4).search("q", k=1)
    assert texts(results) == ["d"]


def test_k_larger_than_candidates_asks_base_for_k():
    base = FakeBase([a, b, c, d])
    results = RerankRetriever(base, FakeReranker(), candidates=2).search("q", k=3)
    assert base.calls == [("q", 3, None)]
    assert len(results) == 3


def test_candidates_larger_than_k_asks_base_for_candidates():
    base = FakeBase([a, b, c, d])
    reranker = FakeReranker()
    RerankRetriever(base, reranker, candidates=3).search("q", k=1)
    assert base.calls == [("q", 3, None)]
    assert reranker.calls == [("q", ["a", "b", "c"])]


def test_no_candidates_returns_empty_without_calling_reranker():
    reranker = FakeReranker()
    assert RerankRetriever(FakeBase([]), reranker).search("q", k=5) == []
    assert reranker.calls == []


def test_single_candidate():
    results = RerankRetriever(FakeBase([a]), FakeReranker({"a": -3.5})).search("q", k=5)
    assert texts(results) == ["a"] and results[0].score == -3.5


def test_access_levels_go_to_base_and_reranker_sees_only_allowed_chunks():
    # Review Focus 3: 리랭커는 권한 필터를 우회하지 않는다
    secret = chunk(9, "내부 전용", access_level="internal")
    base = FakeBase([secret, a, b])
    reranker = FakeReranker({"내부 전용": 99.0})
    results = RerankRetriever(base, reranker).search("q", k=5, access_levels=["public"])
    assert base.calls == [("q", 30, ["public"])]
    assert reranker.calls == [("q", ["a", "b"])]
    assert "내부 전용" not in texts(results)
    # 볼 수 있는 등급이 없으면 base가 빈 결과를 주고(fail closed) 리랭커는 불리지 않는다
    assert RerankRetriever(base, reranker).search("q", k=5, access_levels=[]) == []
    assert len(reranker.calls) == 1


def test_wrong_number_of_scores_raises():
    class ShortReranker(FakeReranker):
        def score(self, question: str, passages: list[str]) -> list[float]:
            return [1.0]

    with pytest.raises(ValueError, match="1 scores for 2 passages"):
        RerankRetriever(FakeBase([a, b]), ShortReranker()).search("q", k=2)


def test_cross_encoder_reranker_is_created_without_loading_a_model():
    reranker = CrossEncoderReranker("no-such-org/no-such-model")
    assert reranker.name == "no-such-org/no-such-model"
    assert (reranker.max_length, reranker.batch_size, reranker.truncated) == (512, 16, 0)


# --- 계획 외 추가 테스트 ---


def test_protocols():
    reranker: Reranker = FakeReranker()
    retriever: Retriever = RerankRetriever(FakeBase([a]), reranker)
    assert texts(retriever.search("q", k=1)) == ["a"]
    assert texts(retriever.search("q")) == ["a"]  # Retriever 프로토콜처럼 k를 생략할 수 있다


def test_non_positive_k_returns_empty_without_searching():
    base = FakeBase([a])
    assert RerankRetriever(base, FakeReranker()).search("q", k=0) == []
    assert base.calls == []


def test_non_positive_candidates_rejected():
    with pytest.raises(ValueError, match="candidates"):
        RerankRetriever(FakeBase([a]), FakeReranker(), candidates=0)


def test_nan_score_raises():
    # NaN은 정렬 결과를 입력 순서에 따라 바꾸므로 조용히 넘기지 않는다
    with pytest.raises(ValueError, match="non-finite"):
        RerankRetriever(FakeBase([a, b]), FakeReranker({"a": float("nan")})).search("q", k=2)


def test_model_table_has_both_candidates():
    assert RERANKER_MODELS == {
        "kure-nano": "nlpai-lab/KURE-Reranker-nano",
        "bge-v2-m3": "BAAI/bge-reranker-v2-m3",
    }


def test_modules_import_without_sentence_transformers():
    # 기본 설치(ml extra 없음)에서도 리랭커 모듈과 평가 실행기는 import돼야 한다
    code = (
        "import sys\n"
        "import axbuilder.retrieval.rerank, axbuilder.eval.run\n"
        "assert 'sentence_transformers' not in sys.modules\n"
        "assert 'torch' not in sys.modules\n"
    )
    src = str(Path(axbuilder.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": src}
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_missing_sentence_transformers_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import하면 ImportError
    importlib.import_module("axbuilder.retrieval.rerank")  # 모듈 import는 된다
    reranker = CrossEncoderReranker("nlpai-lab/KURE-Reranker-nano")
    with pytest.raises(RerankerUnavailableError, match=r'pip install -e "\.\[ml\]"'):
        reranker.score("q", ["a"])


class FakeTokenizer:
    """글자 하나를 토큰 하나로 센다. 쌍은 [CLS] 질문 [SEP] 본문 [SEP] = 글자 수 + 3."""

    def __call__(self, questions, passages, **kwargs):
        assert kwargs.get("truncation") is False  # 자르기 전의 길이를 재야 한다
        return {
            "input_ids": [
                [0] * (len(q) + len(p) + 3) for q, p in zip(questions, passages, strict=True)
            ]
        }


class FakeCrossEncoder:
    """sentence_transformers.CrossEncoder 자리에 넣는 가짜: 본문 길이를 점수로 준다."""

    created: list[dict] = []
    output = "single"

    def __init__(self, model_name: str, **kwargs) -> None:
        if "missing" in model_name:
            raise OSError(f"{model_name} is not a valid model identifier")
        type(self).created.append({"model_name": model_name, **kwargs})
        self.tokenizer = FakeTokenizer()
        self.max_seq_length = kwargs.get("max_length")
        self.predict_calls: list[dict] = []

    def predict(self, pairs, **kwargs):
        self.predict_calls.append({"pairs": list(pairs), **kwargs})
        lengths = [float(len(p)) for _, p in pairs]
        if type(self).output == "single":
            return lengths
        if type(self).output == "column":
            return [[x] for x in lengths]
        if type(self).output == "two":
            return [[1.0, x] for x in lengths]  # [무관 로짓, 관련 로짓]
        return [[0.0, x, 1.0] for x in lengths]


@pytest.fixture
def fake_st(monkeypatch):
    FakeCrossEncoder.created = []
    FakeCrossEncoder.output = "single"
    module = types.ModuleType("sentence_transformers")
    module.CrossEncoder = FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return FakeCrossEncoder


def test_model_is_loaded_on_first_score_and_reused(fake_st):
    reranker = CrossEncoderReranker("org/model", max_length=64, batch_size=4)
    assert fake_st.created == []
    assert reranker.score("q", ["aaa", "a", "aa"]) == [3.0, 1.0, 2.0]
    assert reranker.score("q", ["a"]) == [1.0]
    assert len(fake_st.created) == 1
    assert fake_st.created[0]["model_name"] == "org/model"
    assert fake_st.created[0]["max_length"] == 64
    call = reranker._model.predict_calls[0]
    assert call["pairs"] == [("q", "aaa"), ("q", "a"), ("q", "aa")]
    assert call["batch_size"] == 4


def test_empty_passages_do_not_load_the_model(fake_st):
    assert CrossEncoderReranker("org/model").score("q", []) == []
    assert fake_st.created == []


def test_load_failure_names_the_model(fake_st):
    reranker = CrossEncoderReranker("org/missing-model")
    with pytest.raises(RerankerUnavailableError, match="org/missing-model") as exc:
        reranker.score("q", ["a"])
    assert "not a valid model identifier" in str(exc.value)  # 원인도 함께 보인다


def test_truncated_counts_pairs_longer_than_max_length(fake_st):
    # Review Focus 4: 질문 2자 + 본문 + 특수 토큰 3 > max_length 10 이면 잘린다 → 본문 6자부터
    reranker = CrossEncoderReranker("org/model", max_length=10)
    reranker.score("qq", ["a" * 5, "a" * 6, "a" * 50])
    assert (reranker.truncated, reranker.scored) == (2, 3)
    reranker.score("qq", ["a" * 6, "a"])
    assert (reranker.truncated, reranker.scored) == (3, 5)  # 누적


def test_truncation_limit_follows_the_loaded_model_when_it_is_shorter(fake_st):
    # 모델이 받는 길이가 max_length보다 짧아 더 짧게 잘리는 경우
    reranker = CrossEncoderReranker("org/model", max_length=10)
    reranker.score("qq", ["a"])
    reranker._model.max_seq_length = 6
    reranker.score("qq", ["a", "aa"])  # 6토큰, 7토큰
    assert reranker.truncated == 1


@pytest.mark.parametrize("output", ["column", "two"])
def test_two_dimensional_outputs_become_one_score_per_passage(fake_st, output):
    fake_st.output = output
    scores = CrossEncoderReranker("org/model").score("q", ["aaa", "a"])
    # 클래스 2개면 (관련 로짓 - 무관 로짓): softmax의 관련 확률과 순서가 같다
    assert scores == ([3.0, 1.0] if output == "column" else [2.0, 0.0])


def test_three_class_output_is_rejected(fake_st):
    fake_st.output = "three"
    with pytest.raises(ValueError, match="3 values per passage"):
        CrossEncoderReranker("org/model").score("q", ["a"])


@pytest.mark.slow
def test_real_kure_nano_ranks_the_relevant_passage_first():
    reranker = CrossEncoderReranker(RERANKER_MODELS["kure-nano"])
    passages = [
        "전자금융거래에서 접근매체는 본인이 직접 관리해야 한다.",
        "예금자 보호 한도는 금융회사별로 1인당 1억원이다.",
        "오늘 서울의 날씨는 맑고 기온은 20도이다.",
    ]
    scores = reranker.score("예금자 보호 한도는 얼마인가요?", passages)
    assert len(scores) == 3
    assert max(range(3), key=scores.__getitem__) == 1
    assert scores[1] > scores[2]  # 같은 금융 주제인 0번과 2번의 순서는 단언하지 않는다
    assert reranker.truncated == 0 and reranker.scored == 3
