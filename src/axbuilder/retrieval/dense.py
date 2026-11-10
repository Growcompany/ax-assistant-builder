"""Dense(임베딩) 검색: 임베더 + Qdrant 로컬 모드 색인."""

import hashlib
import math
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from qdrant_client import QdrantClient, models

from axbuilder.models import Chunk, ScoredChunk

MAX_BATCH_SIZE = 32  # sentence-transformers 기본값
# 은행 코퍼스 945청크 측정(RTX 3070 Ti 8GB): 8천·1.6만·3.2만 모두 약 15~17초, 최대 2.4~3.2GiB
MAX_BATCH_CHARS = 16_000

# 점수 차가 이 값 이하면 동점으로 본다. Qdrant 로컬 모드(numpy 내적)는 비트까지 같은 벡터에도
# 저장 위치에 따라 마지막 자리가 다른 점수를 준다: 같은 세션 안에서 약 5e-16, 색인한 세션과 다시
# 연 세션 사이에서 약 4e-8을 쟀다(dim 1024, PR #19 리뷰). 그보다 충분히 크고, 뜻이 다른 청크의
# 점수 차(보통 1e-3 이상)보다는 충분히 작은 값이다
TIE_TOLERANCE = 1e-6


def _tie_groups(scores: list[float]) -> list[int]:
    """내림차순 점수마다 동점 묶음 번호(0부터)를 붙인다.

    묶음은 그 묶음의 가장 높은 점수(기준)에서 TIE_TOLERANCE 안에 있는 점들이다. 기준에서 오차
    밖으로 벗어난 첫 점이 다음 묶음의 기준이 된다. 이웃한 점끼리의 차로 묶으면 a≈b, b≈c가
    이어져(연쇄) 묶음이 끝없이 길어지고, 그 안에서는 점수 순서를 잃는다. 기준에 묶으면 한 묶음의
    폭이 TIE_TOLERANCE를 넘지 않는다.

    보장하지 않는 것: 점수가 "기준 - TIE_TOLERANCE" 경계에서 흔들림 폭(약 4e-8) 안에 있는 점은
    세션에 따라 묶음이 달라질 수 있다. 같은 벡터의 점수 차(위의 흔들림)는 경계에서 멀어 늘 한
    묶음이다.
    """
    groups: list[int] = []
    anchor = math.inf
    group = -1
    for score in scores:
        if anchor - score > TIE_TOLERANCE:
            anchor = score
            group += 1
        groups.append(group)
    return groups


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """문서(청크)용 임베딩."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """질문용 임베딩. 질문 접두사가 필요한 모델은 여기서 붙인다."""
        ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, device: str = "cuda") -> None:
        # ml extra(torch)가 없는 CI에서도 이 모듈은 import돼야 하므로 여기서 import한다
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name  # API가 색인 정보에 남겨 재시작 후 같은 모델인지 비교한다
        self._model = SentenceTransformer(model_name, device=device)
        # 설치본(6.2.0)의 정식 이름은 get_embedding_dimension이고, 옛 이름
        # get_sentence_embedding_dimension은 FutureWarning을 낸다. 옛 이름만 있는 버전도 받는다
        get_dim = getattr(self._model, "get_embedding_dimension", None)
        if get_dim is None:
            get_dim = self._model.get_sentence_embedding_dimension
        dim = get_dim()
        if dim is None:
            raise ValueError(f"cannot determine embedding dimension of {model_name}")
        self.dim = dim
        # snowflake-arctic-embed는 질문에만 "query: " 접두사를 붙여 학습됐다(비대칭).
        # 프롬프트가 없는 모델에 prompt_name을 넘기면 오류라서 있을 때만 쓴다
        self._query_prompt = "query" if "query" in self._model.prompts else None

    def embed(self, texts: list[str]) -> list[list[float]]:
        # 긴 조항(최대 약 7천 토큰) 여러 개를 한 배치에 넣으면 8GB GPU 메모리를 넘겨
        # 공유 메모리로 넘치고 수십 배 느려진다(학습 노트). 긴 글부터 정렬해
        # (배치 크기 × 배치에서 가장 긴 글자 수)가 MAX_BATCH_CHARS를 넘지 않게 나눈다
        order = sorted(range(len(texts)), key=lambda i: -len(texts[i]))
        result: list[list[float]] = [[] for _ in texts]
        start = 0
        while start < len(order):
            longest = max(len(texts[order[start]]), 1)
            size = max(1, min(MAX_BATCH_SIZE, MAX_BATCH_CHARS // longest))
            batch = order[start : start + size]
            vectors = self._model.encode(
                [texts[i] for i in batch],
                batch_size=size,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            for i, vector in zip(batch, vectors, strict=True):
                result[i] = vector.tolist()
            start += size
        return result

    def embed_query(self, text: str) -> list[float]:
        vectors = self._model.encode(
            [text],
            prompt_name=self._query_prompt,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vectors[0].tolist()


class HashEmbedder:
    """테스트용 임베더: 글자 바이그램을 해시해 dim차원에 누적하고 L2 정규화한다.

    같은 글자 조합이 많을수록 코사인 유사도가 높다. 모델·GPU·네트워크가 필요 없다.
    """

    def __init__(self, dim: int = 64) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        # 띄어쓰기를 지워 "예금자보호"와 "예금자 보호"가 같은 바이그램을 갖게 한다
        compact = "".join(text.split())
        grams = [compact[i : i + 2] for i in range(len(compact) - 1)] or (
            [compact] if compact else []
        )
        vec = [0.0] * self.dim
        for gram in grams:
            # 파이썬 hash()는 프로세스마다 달라(PYTHONHASHSEED) 결과가 재현되지 않는다
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            vec[int.from_bytes(digest, "big") % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class DenseIndex:
    def __init__(self, embedder: Embedder, location: str | Path, collection: str) -> None:
        self.embedder = embedder
        self.collection = collection
        if str(location) == ":memory:":
            self._client = QdrantClient(location=":memory:")
        else:
            # 로컬 모드 폴더는 잠금 파일 때문에 한 번에 클라이언트 하나만 열 수 있다(학습 노트)
            self._client = QdrantClient(path=str(location))

    def build(self, chunks: list[Chunk], before_replace: Callable[[], None] | None = None) -> None:
        """색인을 새로 만든다 (이전 색인은 버린다).

        before_replace는 임베딩이 끝나고 저장된 점을 바꾸기 직전에 한 번 부른다. 임베딩이
        실패하면 부르지 않는다. API는 여기서 "색인 완성" 표시(index.json)를 지운다: 그 전에
        실패하면 이전 색인이 그대로 쓸 수 있고, 그 뒤에 실패하면 덜 만들어진 색인으로 보인다.
        """
        # 임베딩이 실패해도 기존 색인이 남도록 컬렉션을 지우기 전에 계산한다
        vectors = self.embedder.embed([c.text for c in chunks])
        if before_replace is not None:
            before_replace()
        if self._client.collection_exists(self.collection):
            # Windows의 로컬 모드(qdrant-client 1.20)는 delete_collection이 컬렉션의 SQLite 파일을
            # 닫지 않은 채 지우려다 조용히 실패하고, 같은 이름으로 다시 만들면 그 파일의 옛 점을
            # 다시 읽는다. 점을 먼저 모두 지워 두면 남은 파일에도 옛 점이 없다
            self._client.delete(
                self.collection,
                points_selector=models.FilterSelector(filter=models.Filter()),
            )
            self._client.delete_collection(self.collection)
        self._client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(
                size=self.embedder.dim, distance=models.Distance.COSINE
            ),
        )
        if not chunks:
            return
        self._client.upsert(
            self.collection,
            points=[
                models.PointStruct(
                    # Qdrant 점 id는 정수나 UUID만 된다. chunk_id에서 항상 같은 UUID를 만든다
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                    vector=vector,
                    payload=chunk.model_dump(mode="json"),
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ],
        )

    def search(
        self, query: str, k: int = 10, access_levels: list[str] | None = None
    ) -> list[ScoredChunk]:
        if k <= 0 or not self._client.collection_exists(self.collection):
            return []
        # BM25와 같은 규칙: None은 필터 없음, []는 볼 수 있는 등급이 없으므로 빈 결과(fail closed)
        if access_levels is not None and not access_levels:
            return []
        query_filter = (
            None
            if access_levels is None
            else models.Filter(
                must=[
                    models.FieldCondition(
                        key="access_level", match=models.MatchAny(any=list(access_levels))
                    )
                ]
            )
        )
        vector = self.embedder.embed_query(query)

        def fetch(limit: int, score_threshold: float | None = None) -> list[ScoredChunk]:
            # 점수 하한은 평소에는 두지 않는다: BM25가 비는 질문("은?")에도 가장 가까운 청크를 준다
            points = self._client.query_points(
                self.collection,
                query=vector,
                query_filter=query_filter,
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
            ).points
            hits = [ScoredChunk(chunk=Chunk(**p.payload), score=p.score) for p in points]
            hits.sort(key=lambda hit: -hit.score)
            return hits

        # 동점 묶음 안에서는 chunk_id 오름차순으로 정한다(_tie_groups). 본문이 같은 청크("제2조
        # 생략" 등)는 벡터가 같은데, Qdrant 로컬 모드가 돌려주는 순서는 색인한 세션과 다시 연
        # 세션에서 다르다. k에서 잘리는 묶음은 구성원 전부를 봐야 누가 남을지 정할 수 있다:
        # k+1개를 받아 보고, k번째와 k+1번째가 같은 묶음이면 그 묶음의 끝까지(점수 하한으로) 한 번
        # 더 받는다. 질의는 많아야 두 번이다
        hits = fetch(k + 1)
        if len(hits) > k:
            groups = _tie_groups([hit.score for hit in hits])
            if groups[k] == groups[k - 1]:
                anchor = hits[groups.index(groups[k])].score
                total = self._client.count(
                    self.collection, count_filter=query_filter, exact=True
                ).count
                # 하한은 묶음 폭의 두 배로 넉넉히: 점수는 질의마다 마지막 자리가 흔들린다.
                # 묶음은 아래에서 다시 나누므로 더 받아 온 점은 결과에 영향이 없다
                hits = fetch(total, score_threshold=anchor - 2 * TIE_TOLERANCE)
        groups = _tie_groups([hit.score for hit in hits])
        ranked = sorted(zip(groups, hits, strict=True), key=lambda g: (g[0], g[1].chunk.chunk_id))
        return [hit for _, hit in ranked[:k]]

    def chunks(self) -> list[Chunk]:
        """색인에 들어 있는 청크 전부(순서는 정하지 않음). 컬렉션이 없으면 [].

        폴더에 저장한 색인을 다시 열 때, 메모리에만 있는 BM25를 같은 청크로 다시 만드는 데 쓴다.
        """
        if not self._client.collection_exists(self.collection):
            return []
        result: list[Chunk] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                self.collection, limit=256, offset=offset, with_payload=True, with_vectors=False
            )
            result.extend(Chunk(**p.payload) for p in points)
            if offset is None:
                return result

    def close(self) -> None:
        """로컬 모드 폴더 잠금을 푼다. 같은 폴더를 다시 열기 전에 호출한다."""
        self._client.close()
