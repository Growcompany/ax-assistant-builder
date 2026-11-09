# 한국어 RAG 스택 최신 확인 — 임베딩·리랭커·로컬 LLM·버전 (2026)

(2026-11-09 조사 · 조사 에이전트 작성 · 표기: [확인] 원문을 직접 열어 확인 / [검색] 검색 결과 요약만 확인 / [추정] 해석. 결정에 반영한 내용은 `00-summary.md` 6절과 M-1 계획의 '변경 기록' 참고)

## 요약

- **임베딩**: 2026년 10~11월에 공개 비교표가 나왔고, 기준은 MTEB(kor, v2) Retrieval의 9개 데이터셋 평균 nDCG@10입니다. 수치는 bge-m3 0.7509, KURE-v1 0.7616, dragonkue/snowflake-arctic-embed-l-v2.0-ko 0.7653, Qwen3-Embedding-0.6B 0.7140, 그리고 새로 나온 **nlpai-lab/KURE-v3 0.8027**입니다 [확인][10][12]. 표의 다른 회사 모델 행은 공식 MTEB 결과 저장소에서 가져온 값이라 서로 비교할 수 있습니다 [확인][12]. 다만 이 표를 만든 쪽이 KURE 팀입니다.
- **디스크와 성능을 함께 보면**: bge-m3와 같은 구조(XLM-R 계열 인코더, 1024차원)에 디스크도 같은 2.27GB이면서 한국어 성능이 더 높은 **snowflake-arctic-embed-l-v2.0-ko를 기본값으로 바꾸는 것**을 권합니다. KURE-v3는 상향 실험 후보, bge-m3는 비교용 베이스라인으로 두면 됩니다 [추정].
- **리랭커**: **KURE-Reranker-nano**(149M, 0.60GB, Apache-2.0)가 MTEB-ko-retrieval 평균 nDCG@10 0.8757로, bge-reranker-v2-m3(0.8518, 2.27GB)보다 높고 4배 작습니다 [확인][16][17][18]. 8GB 조건에서 가장 유리합니다.
- **로컬 LLM**: 2026년 Ollama 공식 라이브러리에 qwen3.5와 gemma4가 올라왔습니다 [확인][21]. EXAONE 3.5는 비상업(NC) 라이선스이고, 결과물 소유권이 LG에 있으며, 다른 모델 개선에 쓰는 것도 금지합니다 [확인][32]. 그래서 **기본값은 `qwen3.5:4b-q4_K_M`(3.3GB, Apache-2.0), 대안은 `qwen3.5:9b-q4_K_M`(6.6GB)** 을 권합니다. exaone3.5:7.8b는 한국어 비교군으로만 쓰는 것이 좋습니다 [추정].
- **설치 주의**: 2026-11-09 기준 PyTorch 2.15.0의 Windows cp313 CUDA 휠은 **cu132 인덱스에만** 있습니다 [확인][58]. 드라이버는 R580 이상이 필요합니다 [확인][62].
- **함정 4가지** (모두 [확인]):
  - Ollama는 VRAM 24GiB 미만이면 기본 컨텍스트가 **4k**입니다 [43].
  - Qdrant 로컬 모드는 **프로세스 하나만** 열 수 있고(.lock), **2만 포인트를 넘으면 경고**를 띄웁니다 [51].
  - Qdrant RRF의 기본 k는 **2**입니다 [53].
  - Kiwi는 VV·VA에 **-R/-I 접미사**를 붙입니다 (예: VV-I) [48].

---

## 임베딩

### 비교표 (MTEB(kor, v2) Retrieval, nDCG@10)

| 모델 | 파라미터 | 디스크(가중치) | 차원 | 최대 길이 | 라이선스 | 평균 | LawIRKo | 비고 |
|---|---|---|---|---|---|---|---|---|
| BAAI/bge-m3 | 568M | pytorch_model.bin 2.27GB. 저장소에 **onnx/model.onnx_data 2.27GB가 따로 있음** [확인][6] | 1024 [확인][5] | 8192 [확인][5] | MIT [확인][6] | 0.7509 | 0.7174 | 마지막 수정 2024-07 [확인][6] |
| nlpai-lab/KURE-v1 | 568M | 2.27GB safetensors [확인][2] | 1024 | 8192 | MIT | 0.7616 | 0.7426 | bge-m3 기반 [확인][1] |
| **dragonkue/snowflake-arctic-embed-l-v2.0-ko** | 568M | 2.27GB [확인][4] | 1024 | 8192. 학습은 최대 1300 토큰이라 긴 문서에서는 성능이 떨어질 수 있음 [확인][3] | Apache-2.0 [확인][4] | **0.7653** | 0.7735 | 쿼리에 `query: ` 또는 `prompt_name="query"` [확인][3] |
| dragonkue/BGE-m3-ko | 568M | – | 1024 | – | 미확인 | 0.7547 | 0.7322 | |
| Qwen/Qwen3-Embedding-0.6B | 0.6B | **1.19GB** [확인][8] | 32~1024 (MRL) | 32K | Apache-2.0 | 0.7140 | 0.7526 | `Instruct: …\nQuery:` 형식, left padding, last-token pooling, transformers 4.51 이상 [확인][7] |
| Qwen3-Embedding-4B / 8B | 4.0B / 7.6B | – | – | – | – | 0.7737 / 0.7826 | 0.7769 / 0.8171 | LLM과 함께 8GB에 올리기 어려움 [추정] |
| **nlpai-lab/KURE-v3** | 1.1B (Ministral-3-3B 기반) | 2.28GB BF16 [확인][11] | 2048 (MRL 1024/512/256) | 8192 | Apache-2.0 (+OpenMDW-1.1 파일) [확인][11] | **0.8027** | **0.8158** | 2026-10-15 생성, 11-01 수정 [확인][11]. 1024차원으로 줄여도 점수의 약 99% 유지 [확인][10] |
| nlpai-lab/KURE-v2 | 154M | **0.30GB** [확인][13] | 토큰당 128차원 (late-interaction) | 문서 8192 / 쿼리 64 | Apache-2.0 | 0.8160 | 0.7550 | PyLate 형식. 단일 벡터가 아니라 멀티벡터 저장이 필요 [확인][12][13] |
| nlpai-lab/KURE-Sparse-nano | 0.1B | – | sparse (어휘 36,282개) | 8192 | Apache-2.0 | 0.7716 | 0.7226 | `SparseEncoder(..., trust_remote_code=True)` [확인][14] |
| google/embeddinggemma-300m | 0.3B | – | – | – | – | 0.7254 | 0.6850 | [확인][10] |
| nlpai-lab/KoE5 | 560M | – | – | – | – | 0.7337 | 0.7756 | [확인][10] |

### 해석

- **벤치마크 사이의 일관성**:
  - 2025년 dragonkue 카드(7개 데이터셋, 평균 nDCG@10)에서도 순위가 같습니다. snowflake-ko 0.7404 > BGE-m3-ko 0.7300 > KURE-v1 0.7277 > bge-m3 0.7242 [확인][3].
  - 예전 KURE-v1 카드의 자체 벤치마크에서는 KURE-v1 0.6947이 bge-m3 0.6872보다 약간 앞섭니다 [확인][1].
  - 정리하면 세 모델의 차이는 1~2점 정도로 작고, 데이터셋에 따라 순위가 바뀝니다 [추정].
- **법령 검색(LawIRKo)**: 이 프로젝트처럼 법령·감독규정 문서에는 LawIRKo가 가장 가까울 것으로 봅니다 [추정]. 여기서 KURE-v3(0.8158)와 snowflake-ko(0.7735)가 bge-m3(0.7174)보다 크게 높습니다 [확인][10].
- **디스크 2GB 이하 대안**:
  - ① Qwen3-Embedding-0.6B (1.19GB). 다만 한국어 점수가 bge-m3보다 낮습니다.
  - ② KURE-v2 (0.30GB, 0.8160). Qdrant 멀티벡터와 ST 6의 MultiVectorEncoder가 필요합니다 [확인][55].
  - ③ KURE-Sparse-nano. Kiwi BM25를 학습된 sparse 검색으로 바꿀 때의 실험 후보입니다.
  - ④ F32 체크포인트를 fp16으로 다시 저장하면 1.1GB 정도로 줄어듭니다 [추정].
- **bge-m3 다운로드 주의**: 저장소 전체를 받으면 onnx까지 포함해 약 4.5GB가 됩니다 [확인][6]. 필요한 파일만 받는 방식(allow_patterns)을 쓰십시오 [추정].

## 리랭커

MTEB-ko-retrieval 9개 데이터셋 평균 nDCG@10입니다. PPS(초당 처리 쌍 수)는 RTX A6000에서 bf16으로 잰 값입니다 [확인][15][16].

| 모델 | 파라미터 | 디스크 | 라이선스 | 평균 nDCG@10 | LawIRKo | PPS |
|---|---|---|---|---|---|---|
| **nlpai-lab/KURE-Reranker-nano** (skt/A.X-Encoder-base 기반) | 149M | **0.60GB** [확인][17] | Apache-2.0 | **0.8757** | 0.8368 | 482 |
| nlpai-lab/KURE-Reranker-base (Qwen3-1.7B 기반) | 1.7B | 약 3.4GB [추정] | Apache-2.0 | 0.8908 | 0.8768 | 56 |
| BAAI/bge-reranker-v2-m3 | 568M | 2.27GB [확인][18] | Apache-2.0 | 0.8518 | 0.7906 | 413 |
| Qwen/Qwen3-Reranker-0.6B | 596M | 1.19GB [확인][20] | Apache-2.0 | 0.8528 | 0.7865 | 101 |
| dragonkue/bge-reranker-v2-m3-ko | 568M | 약 2.27GB [추정] | Apache-2.0 [확인][19] | 0.8199 | 0.6721 | 409 |
| Qwen3-Reranker-4B / 8B | 4.0B / 8.2B | – | – | 0.8916 / 0.8990 | – | 24 / 15 |

- **dragonkue-ko는 근거가 엇갈립니다**:
  - 자체 평가(AutoRAG 금융 데이터 하나)에서는 top-1 F1이 0.9123으로 원본 bge-reranker-v2-m3(0.8772)보다 높습니다 [확인][19].
  - 반면 KURE 팀 평가에서는 원본보다 낮고, 특히 LawIRKo와 MrTidy에서 약합니다 [확인][16].
  - 도메인에 따라 결과가 달라지므로 자체 평가셋으로 판정해야 합니다 [추정].
- **필요 버전**:
  - KURE-Reranker-nano: sentence-transformers 5.1.0 이상, transformers 4.55.4 이상 [확인][16].
  - KURE-Reranker-base: sentence-transformers 5.6.1 이상, 예제는 `model_kwargs={"dtype": torch.bfloat16}` [확인][15].
- **확인 못 한 부분**: 리랭크 벤치마크에서 1차 검색기가 무엇이었는지는 카드에 적혀 있지 않습니다.

## 로컬 LLM

### Ollama 공식 태그 (ollama.com에서 직접 확인)

| 태그 (고정 권장) | 다운로드 | 컨텍스트 | 라이선스 | 8GB 적합성 |
|---|---|---|---|---|
| `exaone3.5:7.8b`. `7.8b-instruct-q4_K_M`과 같은 해시 c7c4e3d1ca22 [확인][22] | 4.8GB | 32K | EXAONE 1.1-NC [확인][31] | 들어감. 2024-12 모델 |
| `exaone-deep:7.8b` | 4.8GB | 32K | NC | 추론 특화 [확인][24] |
| EXAONE 4.0 | **공식 태그 없음**. 커뮤니티 `ingu627/exaone4.0`(1.2b, 32b)만 있음 [확인][23] | 64K (1.2B) | 1.2-NC [확인][33] | 1.2B만 해당 |
| `qwen3:8b` (= q4_K_M) | 5.2GB | 40K | Apache-2.0 [추정] | 들어감 [확인][26] |
| `qwen3:4b-instruct-2507-q4_K_M` | 2.5GB | – | 〃 | [확인][26] |
| **`qwen3.5:4b-q4_K_M`** | **3.3GB** | 256K | Apache-2.0 [확인][36] | 여유 있음 [확인][27] |
| **`qwen3.5:9b-q4_K_M`** | **6.6GB** | 256K | Apache-2.0 [확인][35] | 빠듯함 [확인][27] |
| `gemma4:e4b-it-q4_K_M` / `e4b-it-qat` / `e2b-it-q4_K_M` / `12b-it-q4_K_M` | 6.6 / 6.1 / 4.6 / 8.0GB | 128K / 256K (12b) | Apache-2.0 [확인][37] | 12b는 초과 [확인][28] |
| `gemma3:4b` / `gemma3:12b` | 3.4 / 8.2GB | 128K | Gemma Terms of Use [확인][29] | 12b는 초과 |
| kanana | **공식 태그 없음**. 커뮤니티 `coolsoon/kanana-1.5-8b` 등만 있음 [확인][25] | 32K | Apache-2.0 [확인][38] | – |

- `qwen3.5:4b`, `qwen3.5:9b` 같은 기본 태그는 크기가 범위(예: 3.3~4.0GB)로 표시되고, 해시도 `-q4_K_M` 태그와 다릅니다 [확인][27]. 재현성을 위해 `-q4_K_M` 태그로 고정하십시오.

### 한국어 품질 근거

공개된 한국어 RAG 전용 동일 조건 비교는 찾지 못했습니다 [검색][40]. 확인한 수치는 다음과 같습니다.

| 모델 | 근거 수치 | 비고 |
|---|---|---|
| EXAONE 3.5 7.8B | KoMT-Bench 7.96, LogicKor 9.08 | 자체 보고, 2024 [확인][31] |
| Kanana-1.5-8B | KMMLU 48.28, KoMT-Bench 7.63 | 2025-05 [확인][38] |
| EXAONE 4.0 1.2B (비추론 모드) | KMMLU-Pro 37.5, Ko-LongBench 69.8 | [확인][33] |
| Qwen3.5-9B | MMMLU 81.2, MMLU-ProX(29개 언어 평균) 76.3 | 한국어 단독 수치 아님 [확인][35] |
| Gemma 4 E4B | MMMLU 76.6 | [확인][37] |

결론적으로 최종 선택은 자체 은행 평가셋으로 결정해야 합니다 [추정].

### EXAONE 라이선스 핵심 (3.5, 1.1-NC) [확인][32]

- 상업 목적 사용 금지. 직간접 수익이 생기는 제품이나 서비스도 포함됩니다.
- 연구 목적으로만 허용됩니다.
- **결과물(Output)의 소유권은 LG에 있습니다.**
- **모델이나 결과물을 다른 모델 개발·개선에 쓰는 것이 금지됩니다.** 따라서 EXAONE으로 합성 평가셋이나 학습 데이터를 만들면 안 됩니다.
- 발표할 때 출처 표기가 필요합니다.
- EXAONE 4.0(1.2-NC)은 결과물 소유권 주장을 없앴고, 교육 목적 사용을 허용합니다 [확인][33].
- 이 프로젝트를 "기업용 플랫폼"으로 내세운다면 NC 모델을 기본값으로 두는 것은 리스크입니다 [추정].

### VRAM 근거

- `qwen3.5:9b` Q4_K_M을 RTX 3070 8GB, 4K 컨텍스트에서 실측한 결과: 생성 59.4 tok/s, 최대 VRAM 6.8GB (2026-07-26 측정) [확인][41].
- 계산기 사이트는 약 9.4GB가 필요하다고 더 비관적으로 추정합니다 [검색][42].
- 따라서 9b를 쓰면 임베더와 리랭커를 GPU에 같이 올리기 어렵습니다. 쿼리 시점에는 CPU에서 돌리거나 순서대로 로드해야 합니다 [추정].

### 추천

- **기본: `qwen3.5:4b-q4_K_M`**. LLM, 임베더(fp16 약 1.1GB), nano 리랭커를 8GB에 동시에 올릴 수 있습니다 [추정]. Apache-2.0이고 2026-02 출시입니다 [확인][35].
- **대안: `qwen3.5:9b-q4_K_M`**. 품질 모드로 쓰되, 임베딩과 리랭크는 CPU에서 돌립니다.
- **호출 시 필수 설정**:
  - `think: false` [확인][44]. Qwen3.5는 기본이 thinking 모드이고 `/think`·`/nothink` 스위치를 지원하지 않습니다 [확인][35].
  - `options.num_ctx` 8192 이상. 기본 4k에서는 청크가 잘립니다 [확인][43] (num_ctx 옵션명 자체는 [추정]).
  - non-thinking 권장 샘플링: temperature 0.7, top_p 0.8, top_k 20 [확인][35].
- Ollama 최신 버전은 v0.40.15 (11-03)입니다 [확인][45].

## 라이브러리 버전·설치

| 패키지 | 최신 버전 (날짜) | 주의점 |
|---|---|---|
| kiwipiepy | **0.24.1** (2026-10-20) [확인][46] | 아래 별도 정리 |
| qdrant-client | **1.20.0** (2026-10-22), Python 3.10 이상 [확인][49] | 아래 별도 정리 |
| sentence-transformers | **6.2.0** (2026-10-21) [확인][54] | 아래 별도 정리 |
| transformers | 5.22.2 (2026-11-06), Python 3.11 이상 [확인][56] | – |
| torch | **2.15.0** (2026-10-28), Python 3.11 이상, cp313 Windows 휠 있음 [확인][57] | 아래 별도 정리 |

**kiwipiepy**
- Windows 휠은 `cp39-abi3`와 `cp314`만 있습니다. 3.13에서는 abi3 휠이 설치됩니다 [추정].
- 품사 태그 체계는 바뀌지 않았습니다. NNG·NNP·NNB·NR·NP·VV·VA·VX·XR·SL·SH·SN이 그대로입니다 [확인][47][48].
- **VV·VA·VX·XSA는 -R/-I 접미사가 붙습니다** (0.12.0부터, 예: `듣/VV-I`) [확인][48]. 그래서 `tag.split("-")[0] in KEEP`처럼 비교해야 합니다.
- 0.23.1에서 문장 맨 앞 "1." 같은 글머리가 SN이 아니라 SB로 태깅되도록 수정됐습니다 [확인][47].
- 0.22.0부터 CoNg가 기본 모델이고, 0.22.1부터 knlm·sbg는 기본 제공되지 않습니다 [확인][47].
- 0.24.0에서 `Sentence`가 namedtuple에서 dataclass로 바뀌었습니다. 튜플처럼 쓰면 FutureWarning이 납니다 [확인][47].

**qdrant-client**
- 사용법: `QdrantClient(path="data/qdrant")` 또는 `QdrantClient(":memory:")` [확인][50]. README 예제는 `query_points`만 씁니다 [확인][49].
- 로컬 모드는 `.lock`에 portalocker 배타 잠금을 겁니다. 다른 인스턴스가 열면 "already accessed by another instance" 오류가 납니다. 그래서 **uvicorn workers=1**로 실행해야 합니다 [확인][51].
- 컬렉션이 20,000 포인트를 넘으면 경고가 나옵니다 [확인][51].
- 로컬 모드에도 RRF와 DBSF 결합이 구현돼 있습니다 [확인][52].
- 서버 문서 기준 `Rrf(k=…)`는 v1.16, 가중 RRF는 v1.17부터이고, **기본 k=2**입니다 [확인][53]. 직접 구현할 때는 k=60을 명시하십시오 [추정].

**sentence-transformers 6.x**
- transformers 5.0 이상 6.0 미만, huggingface-hub 1.3 이상, torch 2.2 이상이 필요합니다 [확인][55].
- 기본 모듈 밖의 커스텀 클래스를 쓰려면 `trust_remote_code=True`가 필수입니다 [확인][55].
- CrossEncoder가 logit을 float32로 올려서 계산하도록 바뀌어, bf16 리랭커에서 동점이 생기던 문제가 고쳐졌습니다. CrossEncoder에는 Flash Attention을 권장하지 않습니다 [확인][55].
- MultiVectorEncoder가 새로 생겼습니다 (KURE-v2용) [확인][55].
- KURE-v3는 `encode_query`/`encode_document`를 쓰면 `query: `/`passage: `가 자동으로 붙습니다. `truncate_dim=1024`를 지정할 수 있습니다 [확인][10].

**torch (CUDA)**
- 2026-11-09 기준 인덱스 상황입니다.
  - 2.15.0: **cu132에만 있음**.
  - 2.14.1: cu126, cu130, cu132에 있음.
  - cu128: 2.11.0까지만 있음 [확인][58][59][60][61].
  - 공식 previous-versions 페이지 요약과는 일부 다릅니다 [73]. 실제 인덱스를 기준으로 판단했습니다.
- 드라이버 조건: CUDA 13.x는 R580 이상, 13.2 툴킷은 R595 브랜치입니다 [확인][62].
- 다운로드 크기: cu132 휠 2.11GB, cu126 휠 2.60GB [확인][58][59].

**설치 명령 (PowerShell)**

```powershell
py -3.13 -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
# 드라이버 580 이상 (nvidia-smi의 "CUDA Version"이 13.x 이상)
pip install --no-cache-dir torch==2.15.0 --index-url https://download.pytorch.org/whl/cu132
# 드라이버가 525~579이면:
# pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126
pip install --no-cache-dir "sentence-transformers==6.2.0" "transformers>=5.22,<6" "qdrant-client==1.20.0" "kiwipiepy==0.24.1"
python -c "import torch;print(torch.__version__,torch.version.cuda,torch.cuda.is_available())"
```

- torch를 먼저 설치하는 이유: 그래야 이후 패키지 설치 때 PyPI의 CPU 전용 torch로 덮어쓰이지 않습니다 [추정].
- `--no-cache-dir`을 붙이면 C드라이브 사용량을 아낄 수 있습니다 [추정].

## 평가

### 검색 평가 (직접 구현할 정의)

정답은 조문 ID 단위로 라벨링하고, 청크는 조문 ID로 매핑한 뒤 중복을 제거해서 계산합니다 [추정].

| 지표 | 정의 |
|---|---|
| Recall@k | 상위 k개에 든 정답 수 ÷ 전체 정답 수 |
| Hit@k | 상위 k개에 정답이 하나라도 있으면 1 |
| MRR@k | 첫 정답 순위의 역수 평균. k 밖이면 0 |
| nDCG@k | DCG@k ÷ IDCG@k. DCG@k = Σᵢ₌₁..ₖ (2^relᵢ − 1) / log₂(i+1) [추정, 표준 정의] |

- 직접 구현한 값은 ranx 0.3.21로 교차 검증할 수 있습니다. ranx는 Recall, MRR, NDCG, Hit Rate와 RRF를 지원합니다 [확인][67].
- 모델 비교의 외부 기준점으로는 mteb의 MTEB(kor, v2)를 쓸 수 있습니다. KURE 팀은 mteb 2.18.16을 썼습니다 [확인][12].

### RAGAS 정의 참고

- Context Precision@K = Σ(Precision@k × v_k) ÷ 상위 K개 중 관련 항목 수 [확인][65].
- LLM이 필요 없는 **ID 기반 변형**(`IDBasedContextPrecision`)이 있습니다. 이 프로젝트의 정답 ID 방식과 맞습니다 [확인][65].
- Faithfulness = 근거로 뒷받침되는 주장 수 ÷ 전체 주장 수 [확인][66].
- 버전 상황: RAGAS 0.4.3 (2026-01-13)이 마지막 릴리스입니다. 예전 API는 0.4에서 폐기 예정, 1.0에서 제거되므로 `ragas.metrics.collections`를 쓰십시오 [확인][63][66].

### 인용 정확도 (ALCE, EMNLP 2023)

- **인용 재현율**: 문장마다, 인용이 하나 이상 있고 인용한 문서들을 이어 붙인 내용이 그 문장을 뒷받침(entail)하면 1점입니다.
- **인용 정밀도**: 인용 하나가 단독으로는 문장을 뒷받침하지 못하고, 빼도 나머지 인용만으로 뒷받침이 유지되면 그 인용은 "무관"으로 봅니다.
- 원 논문은 NLI 판정에 TRUE(T5-11B)를 썼습니다 [확인][69][70].
- 이 프로젝트 적용안: 1단계로 "인용한 조문 ID가 정답 조문 ID 집합에 들어 있는지"를 결정적으로 검사하고, 2단계로 Claude가 뒷받침 여부를 판정합니다. 답이 없는 질문에 대한 거부 정확도도 따로 측정하십시오 [추정].

### LLM-judge 보정 (MLflow, 2026-10-29) [확인][68]

1. 사람이 라벨링한 골드셋과 홀드아웃을 만듭니다.
2. 사람끼리의 일치도부터 잽니다.
3. Cohen's κ가 약 0.61보다 낮으면 루브릭을 고칩니다. 혼동행렬도 같이 봅니다.
4. 위치 편향과 장황함(verbosity) 편향을 테스트합니다.
5. 민감도와 특이도로 점수를 통계적으로 보정합니다.
6. 결과는 95% 신뢰구간(±1.96·SE)과 함께, judge 모델·프롬프트 버전·골드셋 크기를 명시해 보고합니다.
7. judge, 프롬프트, 과제, 대상 앱이 바뀌면 다시 보정합니다.

- 생성 모델과 다른 계열의 judge를 쓰라는 권고도 있습니다 [검색][71].
- 법률 RAG 평가에서 κ류 지표의 한계를 지적한 논문도 있습니다 [검색][72].

## 이 프로젝트 권고값(표)

| 항목 | 권고 기본값 | 대안 / 베이스라인 | 근거 |
|---|---|---|---|
| Dense 임베딩 | `dragonkue/snowflake-arctic-embed-l-v2.0-ko` (1024차원, `prompt_name="query"`) | 베이스라인 `BAAI/bge-m3`, 상향 실험 `nlpai-lab/KURE-v3` (`truncate_dim=1024`) | [3][4][10] |
| Lexical | Kiwi BM25 유지. 태그 `NNG NNP NNB NR NP VV VA XR SL SN SH`를 접두사로 비교 | 실험 `KURE-Sparse-nano` | [14][48] |
| 결합 | RRF k=60을 직접 구현 [추정] | Qdrant를 쓰면 `Rrf(k=61)`을 명시 (기본값 2, 순위를 0부터 세서 `1/(k+순위-1)` — 2026-11 qdrant-client 1.20.0 로컬 모드로 확인) | [53] |
| 리랭커 | `nlpai-lab/KURE-Reranker-nano` (0.60GB, CPU도 가능 [추정]) | `BAAI/bge-reranker-v2-m3` | [16][17][18] |
| 로컬 LLM | `qwen3.5:4b-q4_K_M` + `think:false`, `num_ctx:8192` | `qwen3.5:9b-q4_K_M` (임베딩·리랭크는 CPU). 비교군 `exaone3.5:7.8b` (NC) | [27][32][41][43][44] |
| 클라우드 LLM / judge | `claude-opus-5-5` (생성 모델과 계열이 다르므로 judge로 적합) | – | [68][71] |
| 벡터 DB | `QdrantClient(path=...)`, 단일 프로세스, 2만 포인트 미만 | 커지면 Docker 서버 | [50][51] |
| 버전 고정 | kiwipiepy 0.24.1, qdrant-client 1.20.0, sentence-transformers 6.2.0, transformers ≥5.22,<6, torch 2.15.0+cu132, Ollama ≥0.40.15 | 드라이버가 580 미만이면 torch 2.14.1+cu126 | [45][46][49][54][56][58][59] |
| 평가 | Recall@5/10, MRR@10, nDCG@10, 인용 P/R (ID 일치 후 judge), Faithfulness, 거부 정확도, κ ≥ 0.61 | ranx로 교차 검증, ragas는 선택 | [63][65][67][68][70] |
| 디스크 예산 (C: 33GB) | torch 약 2.1GB 다운로드 (설치 후 수 GB [추정]) + 임베더 2.3 + 리랭커 0.6 + bge-m3 2.3 + qwen3.5:4b 3.3 (+9b 6.6) ≈ 15~20GB [추정] | 다른 드라이브가 있으면 `HF_HOME`, `OLLAMA_MODELS`를 옮기기 [추정] | [6][17][27][58] |

## 출처

[1] https://huggingface.co/nlpai-lab/KURE-v1
[2] https://huggingface.co/api/models/nlpai-lab/KURE-v1?blobs=true
[3] https://huggingface.co/dragonkue/snowflake-arctic-embed-l-v2.0-ko
[4] https://huggingface.co/api/models/dragonkue/snowflake-arctic-embed-l-v2.0-ko?blobs=true
[5] https://huggingface.co/BAAI/bge-m3
[6] https://huggingface.co/api/models/BAAI/bge-m3?blobs=true
[7] https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
[8] https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B?blobs=true
[9] https://huggingface.co/nlpai-lab
[10] https://huggingface.co/nlpai-lab/KURE-v3
[11] https://huggingface.co/api/models/nlpai-lab/KURE-v3?blobs=true
[12] https://huggingface.co/nlpai-lab/KURE-v2
[13] https://huggingface.co/api/models/nlpai-lab/KURE-v2?blobs=true
[14] https://huggingface.co/nlpai-lab/KURE-Sparse-nano
[15] https://huggingface.co/nlpai-lab/KURE-Reranker-base
[16] https://huggingface.co/nlpai-lab/KURE-Reranker-nano
[17] https://huggingface.co/api/models/nlpai-lab/KURE-Reranker-nano?blobs=true
[18] https://huggingface.co/api/models/BAAI/bge-reranker-v2-m3?blobs=true
[19] https://huggingface.co/dragonkue/bge-reranker-v2-m3-ko
[20] https://huggingface.co/api/models/Qwen/Qwen3-Reranker-0.6B?blobs=true
[21] https://ollama.com/library
[22] https://ollama.com/library/exaone3.5/tags
[23] https://ollama.com/search?q=exaone
[24] https://ollama.com/library/exaone-deep
[25] https://ollama.com/search?q=kanana
[26] https://ollama.com/library/qwen3/tags
[27] https://ollama.com/library/qwen3.5/tags
[28] https://ollama.com/library/gemma4/tags
[29] https://ollama.com/library/gemma3
[30] https://ollama.com/library/bge-m3
[31] https://huggingface.co/LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct
[32] https://huggingface.co/LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct/blob/main/LICENSE
[33] https://huggingface.co/LGAI-EXAONE/EXAONE-4.0-1.2B
[34] https://huggingface.co/LGAI-EXAONE
[35] https://huggingface.co/Qwen/Qwen3.5-9B
[36] https://huggingface.co/Qwen/Qwen3.5-4B
[37] https://huggingface.co/google/gemma-4-E4B-it
[38] https://huggingface.co/kakaocorp/kanana-1.5-8b-instruct-2505
[39] https://huggingface.co/kakaocorp
[40] https://benchlm.ai/leaderboards/korean-benchmarks
[41] https://bmdpat.com/receipts/rtx-3070/qwen-3-5-9b
[42] https://willitrunai.com/es/can-run/qwen-3.5-9b-on-rtx-3070-ti-8gb (검색 결과)
[43] https://docs.ollama.com/context-length
[44] https://docs.ollama.com/capabilities/thinking
[45] https://github.com/ollama/ollama/releases
[46] https://pypi.org/project/kiwipiepy/
[47] https://github.com/bab2min/kiwipiepy/releases
[48] https://github.com/bab2min/kiwipiepy
[49] https://pypi.org/project/qdrant-client/
[50] https://github.com/qdrant/qdrant-client
[51] https://raw.githubusercontent.com/qdrant/qdrant-client/master/qdrant_client/local/qdrant_local.py
[52] https://raw.githubusercontent.com/qdrant/qdrant-client/master/qdrant_client/local/local_collection.py
[53] https://qdrant.tech/documentation/concepts/hybrid-queries/
[54] https://pypi.org/project/sentence-transformers/
[55] https://github.com/huggingface/sentence-transformers/releases/tag/v6.0.0
[56] https://pypi.org/project/transformers/
[57] https://pypi.org/project/torch/
[58] https://download.pytorch.org/whl/cu132/torch/ (휠: https://download.pytorch.org/whl/cu132/torch-2.15.0%2Bcu132-cp313-cp313-win_amd64.whl)
[59] https://download.pytorch.org/whl/cu126/torch/
[60] https://download.pytorch.org/whl/cu130/torch/
[61] https://download.pytorch.org/whl/cu128/torch/
[62] https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html
[63] https://pypi.org/project/ragas/
[64] https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
[65] https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/
[66] https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/
[67] https://pypi.org/project/ranx/
[68] https://mlflow.org/articles/llm-judge-calibration/
[69] https://arxiv.org/abs/2305.14627
[70] https://ar5iv.labs.arxiv.org/html/2305.14627
[71] https://futureagi.com/blog/llm-as-judge-best-practices-2026 (검색 결과)
[72] https://arxiv.org/html/2509.12382v1 (검색 결과)
[73] https://pytorch.org/get-started/previous-versions/
