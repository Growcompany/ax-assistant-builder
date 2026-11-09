"""Dense(임베딩) 검색: 임베더 + Qdrant 로컬 모드 색인."""

import hashlib
import math
import uuid
from pathlib import Path
from typing import Protocol

from qdrant_client import QdrantClient, models

from axbuilder.models import Chunk, ScoredChunk

MAX_BATCH_SIZE = 32  # sentence-transformers 기본값
# 은행 코퍼스 945청크 측정(RTX 3070 Ti 8GB): 8천·1.6만·3.2만 모두 약 15~17초, 최대 2.4~3.2GiB
MAX_BATCH_CHARS = 16_000


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

    def build(self, chunks: list[Chunk]) -> None:
        """색인을 새로 만든다 (이전 색인은 버린다)."""
        # 임베딩이 실패해도 기존 색인이 남도록 컬렉션을 지우기 전에 계산한다
        vectors = self.embedder.embed([c.text for c in chunks])
        if self._client.collection_exists(self.collection):
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
        # 점수 하한은 두지 않는다: BM25가 비는 질문("은?")에도 가장 가까운 청크를 돌려준다
        points = self._client.query_points(
            self.collection,
            query=self.embedder.embed_query(query),
            query_filter=query_filter,
            limit=k,
            with_payload=True,
        ).points
        return [ScoredChunk(chunk=Chunk(**p.payload), score=p.score) for p in points]

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
