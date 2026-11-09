"""BM25와 dense 결과를 RRF(Reciprocal Rank Fusion)로 합치는 하이브리드 검색."""

import math
from typing import Protocol

from axbuilder.models import Chunk, ScoredChunk
from axbuilder.retrieval.bm25 import BM25Index
from axbuilder.retrieval.dense import DenseIndex

# Cormack et al.(2009)의 값. Qdrant 내장 RRF는 순위를 0부터 세고(1/(k + 순위 - 1)) 기본값이
# 2라서, Qdrant로 같은 점수를 내려면 Rrf(k=61)을 줘야 한다
RRF_K = 60


class Retriever(Protocol):
    """BM25Index, DenseIndex, HybridRetriever가 모두 따르는 검색 모양."""

    def search(
        self, query: str, k: int = 5, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]: ...


def rrf_fuse(
    rankings: list[list[ScoredChunk]], k: int = RRF_K, top_n: int = 10
) -> list[ScoredChunk]:
    """여러 순위 목록을 점수 = Σ 1/(k + 순위)로 합친다. 순위는 1부터, 원래 점수는 보지 않는다."""
    # k가 음수면 0으로 나누거나(k = -1, 1위) 몫이 음수가 될 수 있다
    if not (math.isfinite(k) and k >= 0):
        raise ValueError(f"k must be >= 0, got {k}")
    if top_n <= 0:
        return []

    terms: dict[str, list[float]] = {}  # chunk_id -> 목록마다 받은 1/(k + 순위)
    first: dict[str, Chunk] = {}  # 처음 나온 청크를 그대로 돌려준다
    for ranking in rankings:
        seen: set[str] = set()
        for rank, item in enumerate(ranking, start=1):
            chunk_id = item.chunk.chunk_id
            if chunk_id in seen:  # 한 목록 안의 중복은 높은 순위 한 번만 센다
                continue
            seen.add(chunk_id)
            first.setdefault(chunk_id, item.chunk)
            terms.setdefault(chunk_id, []).append(1 / (k + rank))

    # 목록이 2개면 +도 순서와 무관하다(교환법칙). 3개 이상이면 더하는 순서에 따라 마지막 자리가
    # 달라질 수 있어서, 정확히 반올림한 합을 내는 fsum으로 같은 순위 조합이면 늘 정확히 동점이
    # 되게 한다. sorted는 안정 정렬이라 동점은 처음 나온 순서(앞 목록 = BM25 먼저)로 고정된다
    fused = sorted(
        (ScoredChunk(chunk=first[cid], score=math.fsum(ts)) for cid, ts in terms.items()),
        key=lambda s: -s.score,
    )
    return fused[:top_n]


class HybridRetriever:
    def __init__(self, bm25: BM25Index, dense: DenseIndex, candidates: int = 30) -> None:
        if candidates <= 0:
            raise ValueError(f"candidates must be positive, got {candidates}")
        self.bm25 = bm25
        self.dense = dense
        self.candidates = candidates

    def search(
        self, query: str, k: int = 5, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]:
        if k <= 0:
            return []
        # 각 검색기에서 k보다 넉넉히 받아야 한쪽에서만 중위권인 청크도 합산 기회를 얻는다.
        # k가 candidates보다 크면 k개씩 받는다(결과 수가 candidates에 묶이지 않게)
        n = max(self.candidates, k)
        # 권한 필터는 두 검색기 안에서 top-n 전에 건다. 합친 뒤 거르면 결과가 k보다 모자란다
        rankings = [
            self.bm25.search(query, k=n, access_levels=access_levels),
            self.dense.search(query, k=n, access_levels=access_levels),
        ]
        return rrf_fuse(rankings, k=RRF_K, top_n=k)
