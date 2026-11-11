"""리랭커: 검색기가 고른 후보를 크로스 인코더로 다시 줄 세운다.

임베딩 검색은 질문과 청크를 따로 벡터로 만들어 비교하지만, 크로스 인코더는 (질문, 청크) 쌍을
함께 읽고 관련도를 낸다. 더 정확하지만 쌍마다 모델을 돌려야 해서 후보 수십 개에만 쓴다.
"""

import math
from typing import Any, Protocol

from axbuilder.models import ScoredChunk
from axbuilder.retrieval.hybrid import Retriever

# eval.run의 --reranker 선택지 → Hugging Face 모델 이름 (둘 다 Apache-2.0)
RERANKER_MODELS = {
    "kure-nano": "nlpai-lab/KURE-Reranker-nano",
    "bge-v2-m3": "BAAI/bge-reranker-v2-m3",
}


class RerankerUnavailableError(RuntimeError):
    """리랭커 모델을 불러올 수 없다(패키지 없음, 모델 없음, 메모리 부족 등)."""


class Reranker(Protocol):
    name: str

    def score(self, question: str, passages: list[str]) -> list[float]:
        """본문마다 관련도 점수. 길이는 len(passages), 클수록 관련이 높다."""
        ...


class CrossEncoderReranker:
    """sentence-transformers CrossEncoder. 모델은 처음 score()를 부를 때 불러온다."""

    def __init__(self, model_name: str, max_length: int = 512, batch_size: int = 16) -> None:
        if max_length <= 0:
            raise ValueError(f"max_length must be positive, got {max_length}")
        if batch_size <= 0:
            raise ValueError(f"batch_size must be positive, got {batch_size}")
        self.name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self.truncated = 0  # max_length를 넘어 뒤가 잘린 (질문, 본문) 쌍의 수(누적)
        self.scored = 0  # 채점한 쌍의 수(누적). truncated의 분모
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            # ml extra(torch)가 없는 기본 설치에서도 이 모듈은 import돼야 하므로 여기서 import한다
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise RerankerUnavailableError(
                f"reranker {self.name} needs sentence-transformers; "
                f'install it with: pip install -e ".[ml]" ({e})'
            ) from e
        try:
            # device를 주지 않으면 sentence-transformers가 GPU가 있을 때 GPU를 고른다
            self._model = CrossEncoder(self.name, max_length=self.max_length)
        except Exception as e:  # 모델 없음(OSError), 네트워크, GPU 메모리 부족 등 원인이 여럿이다
            raise RerankerUnavailableError(
                f"cannot load reranker model {self.name}: {type(e).__name__}: {e}"
            ) from e
        return self._model

    def _count_truncated(self, model: Any, question: str, passages: list[str]) -> int:
        # 모델이 받는 길이가 max_length보다 짧으면(위치 임베딩 한계) 실제로는 거기서 잘린다
        limit = min(self.max_length, getattr(model, "max_seq_length", None) or self.max_length)
        # 모델에 들어가는 모양(특수 토큰 포함한 질문+본문 쌍)을 자르지 않고 토큰화해 길이를 잰다
        encoded = model.tokenizer(
            [question] * len(passages),
            passages,
            add_special_tokens=True,
            truncation=False,
            verbose=False,  # "모델 최대 길이보다 길다" 경고는 여기서 세고 있으므로 끈다
        )
        return sum(len(ids) > limit for ids in encoded["input_ids"])

    def score(self, question: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        model = self._load()
        self.truncated += self._count_truncated(model, question, passages)
        self.scored += len(passages)
        raw = model.predict(
            [(question, passage) for passage in passages],
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        rows = raw.tolist() if hasattr(raw, "tolist") else list(raw)
        # 순서만 쓰므로 로짓·확률을 변환하지 않는다
        return [_relevance(row) for row in rows]


def _relevance(row: Any) -> float:
    """모델 출력 한 줄을 점수 하나로. 대부분의 리랭커는 값 하나(num_labels=1)를 낸다."""
    if not isinstance(row, list):
        return float(row)
    if len(row) == 1:
        return float(row[0])
    if len(row) == 2:
        # [무관, 관련] 2클래스 분류기: 차이는 softmax의 관련 확률과 순서가 같다
        return float(row[1]) - float(row[0])
    raise ValueError(f"reranker returned {len(row)} values per passage; expected 1 or 2")


class RerankRetriever:
    """base 검색기의 후보를 리랭커 점수로 다시 정렬한다. Retriever 프로토콜을 만족한다."""

    def __init__(self, base: Retriever, reranker: Reranker, candidates: int = 30) -> None:
        if candidates <= 0:
            raise ValueError(f"candidates must be positive, got {candidates}")
        self.base = base
        self.reranker = reranker
        self.candidates = candidates

    def search(
        self, question: str, k: int = 5, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]:
        if k <= 0:
            return []
        # 권한 필터는 base 안에서 걸린다. 리랭커는 이미 걸러진 후보의 본문만 받는다
        found = self.base.search(question, k=max(self.candidates, k), access_levels=access_levels)
        if not found:
            return []
        scores = self.reranker.score(question, [item.chunk.text for item in found])
        if len(scores) != len(found):
            raise ValueError(f"reranker returned {len(scores)} scores for {len(found)} passages")
        if not all(math.isfinite(score) for score in scores):
            raise ValueError(f"reranker {self.reranker.name} returned a non-finite score")
        # sorted는 안정 정렬이라 동점은 base 순위를 유지한다
        ranked = sorted(zip(found, scores, strict=True), key=lambda pair: -pair[1])
        return [ScoredChunk(chunk=item.chunk, score=score) for item, score in ranked[:k]]
