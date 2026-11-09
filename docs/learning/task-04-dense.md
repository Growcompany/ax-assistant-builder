# Task 4 — Dense 임베딩 색인

## 이 작업이 하는 일

- `src/axbuilder/retrieval/dense.py` 하나에 네 가지가 들어 있다.
  - `Embedder` 프로토콜: `dim`, `embed(texts)`(문서용), `embed_query(text)`(질문용).
  - `SentenceTransformerEmbedder`: 실제 모델(기본 `dragonkue/snowflake-arctic-embed-l-v2.0-ko`, 1024차원, Apache-2.0)을 GPU에서 돌린다.
  - `HashEmbedder`: 테스트용 가짜 임베더. 모델이 필요 없다.
  - `DenseIndex`: 청크를 임베딩해 Qdrant(로컬 모드)에 넣고, 질문과 가장 가까운 청크를 찾는다. `search`는 BM25와 모양이 같다. 그래서 Task 5 하이브리드가 둘을 똑같이 다룬다.

```
build(chunks):  청크 text ─embed()──────▶ 벡터 ─┐
                청크 필드 전부 ─────────▶ payload ─┴▶ Qdrant 점(point), id = uuid5(chunk_id)
search(q):      q ─embed_query()─▶ 벡터 ─▶ 코사인 상위 k (+ access_level payload 필터) ─▶ ScoredChunk
```

## 핵심 개념

**임베딩과 코사인 유사도**
임베딩 모델은 문장을 고정 길이 숫자 벡터(여기선 1024개)로 바꾼다. 뜻이 비슷한 문장일수록 벡터의 방향이 비슷해지도록 학습돼 있다. 두 벡터가 얼마나 같은 방향인지는 코사인 유사도로 잰다.

```
cos(q, d) = (q · d) / (|q| · |d|)      1 = 같은 방향, 0 = 무관, -1 = 반대
```

벡터를 미리 길이 1로 정규화(`normalize_embeddings=True`)해 두면 분모가 1이 된다. 그러면 코사인은 내적(q · d)과 같다. Qdrant 컬렉션은 `Distance.COSINE`으로 만들었으므로 `ScoredChunk.score`가 곧 코사인 유사도다. `test_score_is_cosine_similarity`가 이 값을 직접 계산해 비교한다.
BM25는 같은 글자가 있어야 점수가 난다. dense는 다른 단어로 같은 뜻을 물어도 찾는다. 예를 들어 "예금 보험 보장 금액"으로 물으면 "예금자 보호 한도"가 든 청크가 1위로 나온다(slow 테스트). 대신 dense는 정확한 조문 번호나 숫자에 약하다. 그래서 Task 5에서 BM25와 합친다.

**질문/문서 비대칭 프롬프트**
snowflake-arctic-embed 계열은 학습할 때 질문 앞에만 `"query: "`를 붙였다. 문서에는 아무것도 붙이지 않았다. 질문("~는 얼마인가요?")과 답이 든 문서(조문)는 생김새가 다르다. 그래서 "이건 질문이다"라는 표시를 붙여 서로 다른 공간 위치로 보내도록 학습한 것이다. 모델 설정 파일(`config_sentence_transformers.json`)의 `prompts`에 `{"query": "query: "}`가 들어 있다. 그래서 `embed_query`만 `prompt_name="query"`로 인코딩한다.
- 질문을 `embed()`로 넣으면 접두사가 빠진다. 오류는 나지 않지만 검색 품질이 조용히 떨어진다. 그래서 계획은 `search`가 **반드시 `embed_query`** 를 쓰라고 하고, `test_search_uses_embed_query`가 스파이 임베더로 이를 확인한다.
- 실제로 재 보면 차이가 작지만 있다. 은행 코퍼스 945청크로 "고령 고객 상담 시 주의할 점"을 검색했다. 접두사가 있으면 1위가 `guide-elderly-customers::0004`(0.611)이고, 없으면 `::0003`(0.569)이다. 나머지 두 질문은 1위가 같고 점수만 0.01쯤 높다.
- `"query"` 프롬프트가 없는 모델(bge-m3 등)에 `prompt_name="query"`를 넘기면 오류가 난다. 그래서 프롬프트가 있을 때만 넘긴다(`test_st_embedder_without_query_prompt`).

**벡터DB 컬렉션과 payload 필터**
- 컬렉션은 "같은 차원·같은 거리 함수의 벡터 묶음"이다. 워크스페이스마다 하나를 쓰고, 이름은 생성자의 `collection`이다.
- 점(point) 하나 = id + 벡터 + payload(JSON). `Chunk` 필드 전부를 payload에 넣어 두었다. 그래서 검색 결과에서 바로 `Chunk(**payload)`를 복원한다.
- Qdrant 점 id는 정수나 UUID만 된다. `"law-1::0003"` 같은 chunk_id는 쓸 수 없다. 그래서 `uuid5(NAMESPACE_URL, chunk_id)`로 **항상 같은 UUID**를 만든다.
- 접근 권한은 `access_level` payload에 `MatchAny` 필터를 건다. 필터는 **유사도 상위 k를 고르기 전에** 적용된다. 권한 밖 청크가 1위여도 k개가 공개 청크로 채워진다(`test_access_filter_applies_before_top_k`). 규칙은 BM25와 같다. `access_levels=None`은 필터를 걸지 않고, `[]`는 빈 결과를 낸다(fail closed).

**왜 테스트에서는 가짜 임베더를 쓰는가**
실제 모델은 2.2GB 다운로드, GPU, 로딩 3~8초가 필요하다. CI(GitHub Actions)에는 GPU도 torch도 없다(`.[dev]`만 설치). 그래서 단위 테스트는 `HashEmbedder`로 돈다.
- 글자 바이그램("예금" → "예금", "금자", …)을 해시해 64칸 중 하나에 1씩 더하고 길이 1로 정규화한다. 같은 글자 조합이 많을수록 코사인이 높다. 결과가 **결정적**이어서 "어느 청크가 1위인가"를 단정할 수 있다.
- 파이썬 `hash()`는 실행할 때마다 값이 바뀐다(`PYTHONHASHSEED`). 그래서 `hashlib.blake2b`를 쓴다. 다른 프로세스에서 같은 벡터가 나오는지 테스트한다.
- 띄어쓰기는 지우고 바이그램을 만든다. 그래서 "예금자보호"와 "예금자 보호"가 같은 벡터가 된다.
- 가짜 임베더는 **배관을 검증하는 도구**다. 무엇을 검증하는지: 임베딩 → 저장 → 필터 → 정렬 → 복원이 맞는지, 질문에 `embed_query`를 쓰는지. 뜻 검색 품질은 검증하지 못한다. 그건 `@pytest.mark.slow` 실제 모델 테스트와 Task 9 평가셋이 맡는다.
- `SentenceTransformerEmbedder`도 단위 테스트한다. 모델 없이 `sys.modules`에 가짜 `sentence_transformers`를 끼워 넣고, `prompt_name`·`normalize_embeddings`·배치 나누기를 확인한다.

**Qdrant 로컬 모드는 한 번에 한 클라이언트만**
`QdrantClient(path=폴더)`는 서버 없이 파이썬 안에서 동작한다(내부 저장은 SQLite). 그 대신 폴더에 잠금 파일을 잡는다.
- 같은 폴더를 다른 클라이언트가 열면 `RuntimeError: Storage folder ... is already accessed by another instance of Qdrant client`가 난다. 다른 프로세스뿐 아니라 **같은 프로세스 안에서 두 번째로 열어도** 같은 오류다(직접 확인).
- 그래서 API 서버(Task 10)를 여러 워커로 띄우거나, 서버가 켜진 채로 평가 스크립트가 같은 폴더를 열면 실패한다. 이 문제의 답은 둘 중 하나다. 프로세스 하나가 `DenseIndex`를 들고 공유하거나, Qdrant 서버(도커)로 옮긴다.
- 같은 폴더를 다시 열 수 있도록 `DenseIndex.close()`를 두었다(계획 외 추가). `test_persists_to_folder`는 다음 순서로 확인한다. 저장 → 닫기 → 새로 열기 → `build` 없이 검색.
- `":memory:"`는 클라이언트마다 따로 생기는 임시 저장소다. 테스트가 서로 섞이지 않는다.

## 왜 이렇게 했나 (대안과 비교)

| 결정 | 대안 | 고른 이유 |
|---|---|---|
| Qdrant 로컬 모드 | FAISS, Chroma, numpy 행렬 곱 | payload 필터를 벡터 검색 안에서 처리하고(권한), 나중에 코드 변경 없이 서버 모드로 바꿀 수 있다. FAISS는 필터·메타데이터를 따로 관리해야 한다 |
| `build`가 컬렉션을 지우고 새로 만든다 | 같은 id로 upsert만 | BM25의 `build`와 같은 뜻("이전 색인은 버린다")이다. upsert만 하면 삭제된 문서의 청크가 남는다. 임베딩은 지우기 **전에** 계산해서, 임베딩이 실패해도 기존 색인이 남는다 |
| 점수 하한 없음 | 코사인 0.3 미만 버리기 | "은?"처럼 BM25가 비는 질문에도 dense는 결과를 내야 한다(Task 5 `test_hybrid_works_when_bm25_empty`). 하한값은 모델마다 달라 평가 없이 정할 수 없다 |
| `sentence_transformers`를 생성자 안에서 import | 모듈 맨 위 import | CI는 torch 없이 돈다. 맨 위에 두면 `HashEmbedder`만 쓰는 테스트도 import 단계에서 실패한다. 서브프로세스 테스트로 `torch`가 import되지 않는지 확인한다 |
| 글자 예산으로 배치 나누기 (`MAX_BATCH_CHARS=16_000`) | 기본 `batch_size=32` 그대로 | 아래 측정 참고. 긴 조항이 여러 개 한 배치에 들어가면 8GB GPU가 넘친다 |
| 기본 `device="cuda"` (계획) | CPU로 자동 전환 | 계획의 시그니처를 따랐다. GPU가 없는 PC에서는 `device="cpu"`를 명시해야 한다. 조용히 CPU로 바뀌면 수십 배 느려진 걸 모르고 지나칠 수 있다 |

**측정: 긴 조항과 GPU 메모리** (RTX 3070 Ti 8GB, torch 2.15.0+cu132)
은행 코퍼스는 945청크다. 대부분 짧지만(중앙값 99토큰) 일부 조항은 매우 길다(최대 6,916토큰, 1,024토큰 초과 19개). 이 모델은 최대 8,192토큰까지 받는다.

| 방식 | 대상 | 시간 | GPU 최대 |
|---|---|---|---|
| `batch_size=1` | 가장 긴 32청크 | 4.8초 | 2.41GiB |
| `batch_size=4` | 가장 긴 32청크 | 8.2초 | 4.25GiB |
| `batch_size=8` | 가장 긴 32청크 | **242초** | 6.38GiB |
| 기본 `batch_size=32` | 무작위 100청크 + 전체 945청크 | 10분 넘게 안 끝나 중단 | nvidia-smi 7,965/8,192MiB |
| 글자 예산 1.6만 (채택) | 전체 945청크 | **15.4초** | 2.60GiB |

메모리가 한계에 가까워지면 Windows 드라이버가 시스템 RAM(공유 메모리)으로 넘긴다. 이때 오류 없이 수십 배 느려진다. sentence-transformers는 길이순으로 정렬해 배치를 만든다. 그래서 가장 긴 청크들이 한 배치에 몰린다. 지금은 `배치 크기 × 배치에서 가장 긴 글자 수 ≤ 16,000`이 되도록 직접 나눈다. 짧은 청크는 32개씩, 6천 자 청크는 2개씩, 1.1만 자 청크는 1개씩이다. 근본 해결은 긴 조항을 항 단위로 더 쪼개는 것이다(분할 실험, M-2).

**실제 모델 결과**
- 로딩: 새 프로세스에서 7.9초, 같은 프로세스에서 두 번째는 3.3초.
- 임베딩: 코퍼스 청크 100개(평균 334자)는 2.0초, 전체 945청크 색인은 15.5초. 장치는 `cuda:0`이다.
- 알려진 한계: 동점일 때 Qdrant 로컬 모드의 순서는 색인 순서가 아니다. 실제 임베딩에서 완전 동점은 드물지만 평가 재현성을 볼 때 기억해 둘 것.

## 코드 읽는 순서

1. `Embedder` 프로토콜 — 문서용·질문용 두 메서드가 왜 나뉘는지
2. `HashEmbedder._vector` — 바이그램 → 해시 → 누적 → 정규화
3. `SentenceTransformerEmbedder` — 지연 import, `_query_prompt`, `embed`의 글자 예산 배치
4. `DenseIndex.build` — 임베딩 → 컬렉션 재생성 → `uuid5` id + payload로 upsert
5. `DenseIndex.search` — `embed_query` → `MatchAny` 필터 → `query_points` → `Chunk(**payload)`
6. `tests/retrieval/test_dense.py` — `test_search_uses_embed_query`(스파이), `test_access_filter_applies_before_top_k`, `test_persists_to_folder`, `FakeSentenceTransformer`로 한 배치 테스트

## 면접 예상 질문과 답

**Q1. BM25가 있는데 왜 dense 검색이 필요한가요?**
BM25는 질문과 문서에 같은 단어가 있어야 찾습니다. 고객은 "예금 보험 보장 금액"이라고 묻고, 법령에는 "예금자 보호 한도"라고 적혀 있습니다. dense는 뜻이 가까운 문장을 벡터 공간에서 가깝게 두기 때문에 이런 표현 차이를 메웁니다. 반대로 조문 번호나 금액처럼 정확히 맞아야 하는 질문에는 BM25가 강해서, 다음 단계에서 RRF로 합칩니다.

**Q2. 질문과 문서를 왜 다른 함수로 임베딩하나요?**
이 모델은 질문에만 "query: " 접두사를 붙여 학습한 비대칭 모델입니다. 질문을 문서처럼 인코딩해도 오류는 나지 않지만 검색 품질이 조용히 떨어집니다. 그래서 인터페이스에서 `embed`와 `embed_query`를 나눴고, 검색이 반드시 `embed_query`를 쓰는지를 스파이 객체로 테스트합니다.

**Q3. 권한 필터를 검색 후에 걸면 안 되나요?**
상위 k개를 먼저 뽑고 거르면, 권한 밖 청크가 상위를 차지할 때 결과가 k개보다 적거나 0개가 됩니다. Qdrant payload 필터는 유사도 탐색 안에서 적용되어 권한 안에서의 상위 k를 줍니다. 등급 목록이 비어 있으면 아무것도 보여주지 않는 쪽(fail closed)으로 BM25와 규칙을 맞췄습니다.

**Q4. 테스트에서 진짜 모델 대신 해시 임베더를 쓰면 의미가 있나요?**
해시 임베더는 저장·필터·정렬·복원 같은 배관이 맞는지를 결정적으로 검증합니다. CI에 GPU와 torch 없이 몇 초 만에 돕니다. 의미 검색 품질은 검증하지 못합니다. 그래서 실제 모델 테스트를 `slow`로 따로 두었고, 품질은 50문항 평가셋에서 지표로 봅니다. 역할을 나눈 것입니다.

**Q5. 실제 모델을 붙였을 때 무슨 문제가 있었나요?**
전체 945청크를 기본 배치 32로 임베딩하자 10분이 넘어도 끝나지 않았습니다. 원인을 재 보니 길이 7천 토큰 가까운 조항들이 길이순 정렬 때문에 한 배치에 몰려 8GB GPU 메모리를 넘겼습니다. Windows는 이때 시스템 메모리로 넘겨서 오류 없이 수십 배 느려집니다. 가장 긴 32개를 배치 8로 하면 242초, 배치 1로 하면 4.8초였습니다. 그래서 "배치 크기 × 최장 길이"가 일정 예산을 넘지 않게 배치를 직접 나눴고, 전체가 15초로 줄었습니다. 근본 해결은 긴 조항을 더 잘게 나누는 것이라 분할 실험 항목으로 남겼습니다.
