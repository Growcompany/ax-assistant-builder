# RAG 포트폴리오 트렌드 · 기술스택 (2026-11-09 조사)

[확인] 1차 출처 · [2차] 블로그/벤더 · [판단] 추론

## 0. 핵심
- 차별화는 기능 개수가 아니라 **같은 질문셋으로 검색 방식을 비교하고 수치로 보여주는 것**.
- 국내 지원이면 **한국어 처리 근거**(형태소 BM25, 한국어 임베딩 비교, HWPX·표 파싱)가 강한 차별점. [판단]

## 1. "PDF 챗봇"을 넘는 차별화 요소

| 요소 | 근거 |
|---|---|
| 평가 (골든셋 + RAGAS 등) | LangChain 설문(1,340명): 프로덕션 장애요인 1위 품질 32%, 지연 20% [확인] https://www.langchain.com/state-of-agent-engineering |
| 하이브리드 (BM25+벡터) · 리랭킹 | 키워드 → 벡터 → 필터 → 리랭크 → 하이브리드를 *같은 질문*으로 비교, 실패 사례 공개 [확인] https://datatalksclub.github.io/podwiki/wiki/search-and-rag-project-checklist/ |
| 청킹 비교 실험 | 청킹 선택이 정확도에 유의미한 영향 [확인] https://research.trychroma.com/evaluating-chunking |
| Agentic RAG / LangGraph | 국내 공고에 "LangGraph 재시도·에러처리, 평가 프레임워크, 관찰성" 명시 [확인] |
| 관측성 | 89%가 관측성 보유 [확인]. Langfuse는 MIT 코어 셀프호스팅 가능 [2차] |
| 캐싱 · 비용 | 프롬프트 캐싱(제공자) vs 시맨틱 캐싱(앱) 구분, 시맨틱 캐시는 오래된 답 위험 [2차] |
| 배포 · CI | 배포된 프로젝트가 로컬 프로젝트보다 높게 평가 [확인], PR마다 평가 게이트 [확인] |

## 2. 한국어 RAG 특화

- **임베딩** [확인] https://github.com/OnAnd0n/ko-embedding-leaderboard (2026-07, NDCG 평균)
  - pplx-embed-v1-4b 82.79 › dragonkue/snowflake-arctic-embed-l-v2.0-ko 82.14 › PIXIE-Rune 81.57 › Qwen3-Embedding-4B 81.37 › KURE-v1 80.76 › bge-m3 79.30
  - 상용 API는 직접 비교 필요 → 그 비교 자체가 좋은 실험 소재
- **리랭커**: dragonkue/bge-reranker-v2-m3-ko (모델카드 자체 보고 F1 0.912) [2차]
- **Kiwi BM25**: 공백 토큰화로는 복합어 검색 실패 → 색인·질의 모두 형태소 토큰화, 도메인 용어는 사용자 사전, **점수 합산 말고 RRF로 결합** [확인] https://wikidocs.net/231609
  - Qdrant는 dense+sparse+RRF 네이티브 지원 [확인]. 한국어는 Kiwi 토큰으로 sparse 벡터 직접 생성 권장 [판단]
- **HWP/HWPX**: 2026-05-18부터 온나라시스템 hwp 첨부 제한, hwpx 전환 [확인, 언론]. python-hwpx(HWPX), pyhwp(HWP5, AGPL)
- **PDF 표**: OpenDataLoader PDF(한컴, Apache-2.0, bbox 제공 → 인용에 유리) [2차], Docling(기본 OCR에 한글 없음) [2차], Upstage Document Parse(API)
- **평가 데이터**: `allganize/RAG-Evaluation-Dataset-KO` — 300문항, **금융·공공·의료·법률·커머스**, 근거 유형(문단·표·이미지) 라벨, MIT [확인] https://huggingface.co/datasets/allganize/RAG-Evaluation-Dataset-KO
  - 파이프라인 자동 최적화: AutoRAG https://github.com/Marker-Inc-Korea/AutoRAG

## 3. 기술스택 (포트폴리오 관점)

| 영역 | 1순위 | 대안 |
|---|---|---|
| 오케스트레이션 | 검색 코어는 직접 구현 + 흐름은 LangGraph | LlamaIndex (수집·검색 빠름). 직접 구현이 면접 설명에 유리 [판단] |
| 벡터DB | Qdrant (dense+sparse+RRF 네이티브) | pgvector (Postgres 하나로), Chroma (sparse 미지원), Milvus (무거움), FAISS (라이브러리) |
| API | FastAPI | — |
| UI | Streamlit / Gradio (HF Spaces 무료 배포) | Next.js (일정 부담) |
| LLM | 제공자 추상화 + 상용 API 1개 + 로컬(Ollama) 1개 비교 | EXAONE 4.0 공식 GGUF 제공 (라이선스 확인 필요) |
| 관측성 | Langfuse (셀프호스팅) | LangSmith 무료 티어 |

## 4. GitHub README 구성 (스크리닝 약 8분 기준) [확인] https://myengineeringpath.dev/genai-engineer/portfolio-guide/

1. 결과 중심 1문장 헤드라인
2. 데모 링크 / GIF
3. 아키텍처 다이어그램
4. 기술 결정 3~5개와 이유
5. 결과 표 (정량 지표 2개 이상)
6. 원커맨드 셋업 (`docker compose up`)
7. 한계와 개선 방향 ("가장 과소평가된 섹션")
- 추가: 평가 실행마다 프롬프트·모델 버전·지연·비용 기록, 실패 사례 갤러리, `eval/` 폴더 + CI 배지
