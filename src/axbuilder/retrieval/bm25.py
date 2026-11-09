"""라이브러리 없이 직접 구현한 BM25 (Okapi BM25, Lucene식 +1 IDF)."""

import math
from collections import Counter, defaultdict

from axbuilder.models import Chunk, ScoredChunk
from axbuilder.retrieval.tokenizer import tokenize


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        # NaN은 모든 비교가 False라서 "k1 < 0"만으로는 통과한다
        if not (math.isfinite(k1) and k1 >= 0) or not 0 <= b <= 1:
            raise ValueError(f"k1 must be >= 0 and b in [0, 1], got k1={k1}, b={b}")
        self.k1 = k1
        self.b = b
        self._chunks: list[Chunk] = []
        self._postings: dict[str, list[tuple[int, int]]] = {}  # term -> [(청크 번호, 빈도)]
        self._idf: dict[str, float] = {}
        self._length_norm: list[float] = []  # k1·(1 - b + b·|D|/avgdl), 청크마다 미리 계산

    def build(self, chunks: list[Chunk]) -> None:
        """색인을 새로 만든다 (이전 색인은 버린다)."""
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths: list[int] = []
        for i, chunk in enumerate(chunks):
            tokens = tokenize(chunk.text)
            lengths.append(len(tokens))
            for term, freq in Counter(tokens).items():
                postings[term].append((i, freq))

        n_docs = len(chunks)
        # 토큰이 하나도 없으면 postings가 비어 이 값은 쓰이지 않는다 (0으로 나누기만 피한다)
        avgdl = sum(lengths) / n_docs if any(lengths) else 1.0
        self._chunks = list(chunks)
        self._postings = dict(postings)
        self._idf = {
            term: math.log((n_docs - len(docs) + 0.5) / (len(docs) + 0.5) + 1)
            for term, docs in postings.items()
        }
        self._length_norm = [self.k1 * (1 - self.b + self.b * dl / avgdl) for dl in lengths]

    def search(
        self, query: str, k: int = 10, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]:
        if k <= 0:
            return []
        scores: dict[int, float] = defaultdict(float)
        # 질문에 두 번 나온 단어는 두 번 더한다 (rank_bm25·Lucene과 같은 동작)
        for term, query_freq in Counter(tokenize(query)).items():
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, freq in self._postings[term]:
                tf_part = freq * (self.k1 + 1) / (freq + self._length_norm[i])
                scores[i] += query_freq * idf * tf_part

        # 권한 밖 청크는 결과에서만 빠진다. IDF·avgdl 통계에는 전체 청크가 들어간다(학습 노트).
        # None은 필터 없음이므로 사용자 요청을 처리하는 쪽(Task 5·10)은 항상 등급을 넘겨야 한다
        allowed = None if access_levels is None else set(access_levels)
        ranked = sorted(
            (
                (score, i)
                for i, score in scores.items()
                if score > 0 and (allowed is None or self._chunks[i].access_level in allowed)
            ),
            key=lambda pair: (-pair[0], pair[1]),  # 동점이면 색인 순서로 고정
        )
        return [ScoredChunk(chunk=self._chunks[i], score=score) for score, i in ranked[:k]]
