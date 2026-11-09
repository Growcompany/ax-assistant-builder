# 채용 담당자가 보는 RAG 포트폴리오와 AI 도구 활용 기록 방법 (2026)

(2026-11-09 조사 · 조사 에이전트 작성 · 표기: [확인] 원문을 직접 열어 확인 / [검색] 검색 결과 요약만 확인 / [추정] 해석. 결정에 반영한 내용은 `00-summary.md` 6절과 M-1 계획의 '변경 기록' 참고)

## 요약

- **평가가 가장 잘 드러나는 신호입니다.** 확인한 좋은 RAG 저장소와 채용 쪽 글이 공통으로 요구하는 것은 네 가지입니다. 평가셋과 지표 정의, 결과 표, 실패·한계 공개, 그리고 각 설계 결정의 한 줄 이유입니다. [확인] https://www.techinterview.org/post/3233475399/ai-portfolio-built-with-ai-2026-interviews/ · https://github.com/UrBaneee/rag-framework
- **잘 알려진 오픈소스 RAG 저장소에도 평가 수치가 없는 경우가 많습니다.** kotaemon과 AutoRAG README가 그렇습니다. 그래서 개인 포트폴리오가 평가 표, 지연 비용, 효과가 없었던 결과까지 보여주면 오히려 눈에 띕니다. [확인] https://github.com/Cinnamon/kotaemon · https://github.com/Marker-Inc-Korea/AutoRAG · [추정]
- **2026년 국내 공채는 결과물보다 AI 협업 과정을 평가하는 쪽으로 바뀌었습니다.**
  - CJ올리브네트웍스는 코딩테스트를 전면 개편해 바이브 코딩 테스트로 바꿨습니다. [확인] https://cjnews.cj.net/?p=80060
  - 크래프톤 Cofa-Probe는 소스코드, AI 대화, 프롬프트, 도구 설정, 검증 실행 기록을 모아 평가합니다. [확인] https://www.dailian.co.kr/news/view/1684386
  - 그렙이 공급하는 시중은행 평가는 프롬프트 기록을 분석합니다. [확인] https://ditoday.com/?p=154678
- **저장소도 그 평가 형식에 맞춰 남기면 됩니다.** AI 사용 기록, ADR(의사결정 기록), 검증 증거(실행 명령과 출력), 학습 노트를 남기면 "AI로 만들었다"가 감점이 아니라 평가 항목에 대한 증거가 됩니다. [추정, 근거: 위 기사들 + https://code.claude.com/docs/en/best-practices]
- **은행은 AI 역량만 보지 않고 금융 업무 이해도를 함께 봅니다.** [확인] https://economist.co.kr/article/view/ecn202609220047
  - 그래서 면접 대비는 RAG 기술 질문(청킹, 하이브리드, 평가, 환각, 비용·지연)에 도메인 질문을 더해야 합니다. 도메인 질문은 조문 인용, 개정 시행일, 내규와 법령 충돌, 망분리와 로컬 LLM 같은 것입니다. [추정]
- **이 조사에서 쓴 자료의 품질에 대한 주의:** 검색에 나온 "2026 AI 포트폴리오 가이드"류 블로그(pages.dev 등)는 근거가 확인되지 않는 것이 많아 거의 쓰지 않았습니다. [추정]

---

## 좋은 포트폴리오 사례

### 핵심 사례 5개와 보조 자료 (접근일 2026-11-09)

| # | 사례 | 배울 점 | 약점 / 주의 | 태그 |
|---|---|---|---|---|
| 1 | **UrBaneee/rag-framework** (개인 RAG 저장소) | 섹션 순서가 "만든 이유 → 아키텍처 → 결과 요약 → 설치 → 평가 결과 → 알려진 한계"입니다. Mermaid로 ingest와 query 파이프라인을 그렸습니다(BM25+FAISS → RRF → 리랭커 → 인용 답변). 하이브리드는 Recall@5 +6.6pt를 얻는 대신 쿼리 지연이 12배라는 비용을 같이 적었습니다. LLM 메타데이터 보강은 +0.0pt였다는 null result(효과 없음)도 공개합니다. 30문항을 사람이 직접 라벨링한 평가셋을 쓰고, 정답이 순환적으로 정해진 평가셋은 따로 표시했습니다. 한계 섹션에는 "수치는 방향성으로 읽을 것", "구현했지만 벤치마크하지 않음"이라고 적었습니다. | 데모 GIF가 없습니다 | [확인] https://github.com/UrBaneee/rag-framework |
| 2 | **techinterview.org, "The AI Portfolio: What 'Built With AI' Means in 2026 Interviews"** (2026-05-06, 2026-07-12 갱신) | README 레시피를 제시합니다. 문제와 그 비용, AI가 맞는 도구인 이유(비AI 대안과 비교), 설계 결정마다 한 줄 이유, 대략적인 수치를 붙인 트레이드오프(토큰, 지연, 품질이 깨진 지점), 평가셋과 지표를 점수보다 먼저 설명, 실패 유형과 수정, 다음 단계. 손으로 고른 20개 예제(고른 이유 포함)로도 의미가 있다고 봅니다. 면접관은 지원자가 어렵다고 주장한 부분을 파고든다고 합니다. | 업계 블로그라 설문 같은 데이터는 없습니다 | [확인] https://www.techinterview.org/post/3233475399/ai-portfolio-built-with-ai-2026-interviews/ |
| 3 | **allganize/RAG-Evaluation-Dataset-KO** (국내, 2024) | 한국어 5개 도메인(금융 포함)에 도메인당 60문항, 총 300문항입니다. 결과 셀을 "0.85 (51/60)"처럼 분모까지 적었습니다. 자동 평가자 4개의 투표로 채점하고, 금융 도메인에서 사람과 약 8% 불일치한다는 수치를 공개합니다. LLM 채점의 신뢰도를 보고하는 방식으로 참고할 만합니다. | 2024년 자료이고 마지막 갱신은 2024-08-09입니다 | [확인] https://huggingface.co/datasets/allganize/RAG-Evaluation-Dataset-KO |
| 4 | **Anthropic, "Introducing Contextual Retrieval"** (2024-09-19) | 실험 보고 형식의 본보기입니다. 지표를 먼저 정의하고(1−recall@20, 베이스라인 실패율 5.7%), 단계별로 결과를 보여줍니다. Contextual Embeddings는 실패를 35% 줄였고, Contextual BM25를 더하면 49%, 리랭킹까지 더하면 67% 줄었습니다. "임베딩만 쓰지 않고 BM25를 섞는 이유"에 대한 근거로도 쓸 수 있습니다. | 영문 데이터 기준이고 2024년 자료입니다 | [확인] https://www.anthropic.com/news/contextual-retrieval |
| 5 | **Cinnamon/kotaemon** (대조 사례) | 소개를 최종 사용자용과 개발자용으로 나눴습니다. Docker 실행을 권장 경로로 두고, 로컬 모델 설정 섹션을 따로 만들었습니다. | 정적 PNG만 있고 애니메이션 데모가 없습니다. 아키텍처 다이어그램도 평가 섹션도 없습니다 | [확인] https://github.com/Cinnamon/kotaemon |

보조 자료:
- **Marker-Inc-Korea/AutoRAG** (국내, 대조 사례): 독자별 진입점, 증상→해결 트러블슈팅 표, 프라이버시 섹션이 좋습니다. 반면 README가 매우 길고 성능 주장을 뒷받침하는 평가 수치가 없습니다. 현재 README는 원래 Python AutoRAG를 `legacy/`로 옮긴 형태로 크게 바뀌었으니, 인용할 때는 접근일을 적는 것이 좋습니다. [확인] https://github.com/Marker-Inc-Korea/AutoRAG
- **위키독스 Kiwi 하이브리드 실측 글** (국내, 2026-08): 사람이 만든 12문항으로 Recall@10을 쟀습니다. 키워드 0.542, 의미 0.624, 하이브리드 0.837입니다. 평가셋을 바꾸면 결론이 뒤집힐 수 있다고 직접 밝혔고, 조사가 붙어 키워드 검색이 실패하는 문제를 Kiwi로 해결하는 사례도 있습니다. 이 프로젝트와 스택이 거의 같습니다. [검색] https://wikidocs.net/389782 (403으로 직접 열람은 실패)
- **반례 (면접 대비용):** 법률 데이터에서는 BM25를 더해도 문서 내부 정밀도와 재현율이 떨어질 수 있다는 연구가 있습니다. [검색] https://arxiv.org/pdf/2510.06999

### 사례에서 뽑은 README 필수 요소

| 요청 항목 | 사례에서 확인된 좋은 방식 | 근거 |
|---|---|---|
| 문제 정의 | 누가, 어떤 비용을 겪는지 적고, 왜 AI(RAG)여야 하는지 비AI 대안과 비교 | [확인] techinterview |
| 아키텍처 다이어그램 | Mermaid로 ingest와 query를 나눠 그리고, 모듈을 교체 가능한 인터페이스로 설명 | [확인] rag-framework |
| 평가 표 | 지표 정의 → 베이스라인 → 단계별 비교. 셀에 분모를 적고 지연 비용을 같이 표기 | [확인] Anthropic, allganize, rag-framework |
| 실패·한계 | null result, 벤치마크하지 않은 구성요소, 평가셋의 약점을 명시 | [확인] rag-framework |
| 데모 GIF | 확인한 사례 중 애니메이션 데모가 있는 곳은 없었습니다. 직접 만들면 차별점이 됩니다 | [확인] kotaemon, rag-framework · [추정] |
| 실행 방법 | 한 줄 실행(Docker 또는 단일 명령)과 `.env.example` 제공 | [확인] kotaemon, rag-framework |
| (국내용 추가) | README 맨 위에 한국어 3줄 요약 | [검색] https://wikidocs.net/367963 |

---

## 채용 검토 포인트와 면접 질문

### 한국 채용에서 포트폴리오를 볼 때 확인되는 포인트

- **은행은 대규모 공채에서 직무별 실무형 채용으로 옮겨가고 있습니다.** [확인] https://sateconomy.co.kr/news/view/1065571246306524 · https://magazine.hankyung.com/business/article/202609077767b
  - KB국민은행은 ICT를 IT와 AI·플랫폼 개발로 나눠 뽑고, 서류 → 코딩테스트 → 면접 순으로 진행합니다.
  - 하나은행은 AI·데이터 분석 경력 정규직군을 운영합니다.
  - 4대 은행의 2026년 상반기 신입 채용은 485명으로, 전년 595명보다 줄었습니다.
- **은행 관계자는 "금융 이해도와 디지털 활용 능력"을 함께 갖춘 인재 수요가 늘 것이라고 말했습니다.** 그래서 도메인 이해를 보여주는 것이 중요합니다. [확인] https://economist.co.kr/article/view/ecn202609220047
- **실제 금융 도메인 RAG 공고의 업무 문구가 이 프로젝트와 거의 겹칩니다.** 애자일소다(NH농협은행 AI 조직, 신입 이상) 공고의 주요 업무는 "문서 구조에 맞는 청킹 전략 설계", 임베딩·메타데이터·VectorDB 적재 파이프라인, "검색 품질 개선 실험과 튜닝"입니다. 자격요건에는 Qdrant 등 VectorDB 경험이 있습니다. [확인] https://www.wanted.co.kr/wd/374884 (마감)
- **국내 포트폴리오 조언에서 반복되는 기준**은 직무 연관성, 직접 구현한 부분이 분명한지, 실행 결과를 보여줄 수 있는지, 문제 해결 경험입니다. README는 길이보다 무엇을 직접 했는지가 드러나야 한다는 조언이 많습니다. [검색] https://wikidocs.net/367963 · https://comento.kr/job-questions/모든회사/모든직무/
- **해외 실무 기준**은 "load-bearing AI"입니다. 정규식이나 룩업 테이블로 바꿔도 결과가 같다면 AI 프로젝트로 인정하지 않는다는 뜻입니다. 비용·지연을 인식하고 있는지, 기본값을 의도적으로 골랐는지도 봅니다. [확인] https://www.techinterview.org/post/3233475399/ai-portfolio-built-with-ai-2026-interviews/

### 면접에서 자주 파고드는 RAG 질문과 이 저장소에서 준비할 증거

질문 주제 분류의 근거는 KalyanKS RAG 면접 질문 모음입니다. 청킹(Q8, Q12–14, Q31–37), 하이브리드(Q50–54), 리랭킹(Q61–72), 평가(Q42, Q73–105), 지연(Q4, Q30, Q68, Q71–72)을 다룹니다. [확인] https://github.com/KalyanKS-NLP/RAG-Interview-Questions-and-Answers-Hub
질문과 준비 항목을 묶은 표 자체는 위 자료들을 종합한 것입니다. [추정]

| 주제 | 예상 질문 | 이 저장소에서 보여줄 증거 |
|---|---|---|
| 청킹 | 왜 그 단위로 잘랐나? 크기와 오버랩은 어떻게 정했나? 표와 별표는 어떻게 처리했나? | 조·항·호 구조 단위 청킹과 고정 길이 청킹의 Recall@k 비교 표. 청크 크기를 바꿔가며 잰 결과. 표가 들어간 문서의 실패 사례. 비교 근거: rag-framework는 512토큰 부근에서 효과가 평탄해졌습니다 [확인] https://github.com/UrBaneee/rag-framework |
| 하이브리드 근거 | 임베딩만 쓰면 왜 안 되나? 왜 가중합이 아니라 RRF인가? k=60은 왜인가? | 조문 번호, 금리 같은 숫자, 상품명 질의에서 BM25가 이기는 사례. BM25 / dense / RRF 3열 비교 표. BM25가 식별자와 정확 일치에 강하다는 근거 [확인] https://www.anthropic.com/news/contextual-retrieval. RRF k=60이 흔한 기본값이라는 점 [검색] https://techsy.io/ko/blog/hybrid-search-bm25-vs-vector-rrf. 하이브리드가 지지 않는 경우도 기록 [검색] https://arxiv.org/pdf/2510.06999 |
| 한국어 BM25 | 왜 형태소 분석이 필요한가? 품사 필터는 어떻게 했나? | 공백 토큰화와 Kiwi 토큰화 비교. 조사 때문에 검색이 실패한 사례 [검색] https://wikidocs.net/389782 |
| 평가 방법 | 평가셋은 어떻게 만들었고, 몇 개이며, 누가 라벨링했나? 검색과 생성은 어떻게 나눠 평가했나? LLM 채점은 믿을 수 있나? | 질문 유형별 평가셋(JSONL), 정답 근거 청크 ID, Recall@k·MRR·nDCG. 생성 쪽은 정답성·근거 충실도·인용 정확도·거절. LLM 채점과 사람 채점의 일치율(allganize처럼 불일치율 공개) [확인] https://huggingface.co/datasets/allganize/RAG-Evaluation-Dataset-KO. faithfulness는 컨텍스트와의 일관성만 본다는 한계 [검색] https://docs.ragas.io/en/v0.1.21/references/metrics.html |
| 오류 분석 | 숫자 말고 실제로 무엇이 틀렸나? | 출력 20–50개를 30분씩 수동 리뷰한 기록 [확인] https://hamel.dev/blog/posts/evals-faq/whats-a-minimum-viable-evaluation-setup.html. 오류 분석이 평가에서 가장 중요한 활동이라는 입장 [검색] https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html. 실패 유형 분류는 Barnett et al. 2024의 7가지 실패 지점 참고 [확인 초록] https://arxiv.org/abs/2401.05856 |
| 환각 대응 | 근거가 없을 때는 어떻게 하나? 인용이 맞는지는 어떻게 검증하나? | 근거 부족 시 거절 정책과 "답 없음" 문항의 거절 정확도. 답변 문장과 인용 청크를 대조하는 검증. 검색 실패와 생성 실패를 나눠 진단한 사례 [검색] https://wikidocs.net/435416 |
| 비용·지연 | 질의당 비용과 p95 지연은? 리랭커를 넣으면 얼마나 느려지나? | 로컬 Ollama와 Claude API의 토큰 비용·지연 표. 구성요소별 지연 분해. 지연 배수를 표기하는 방식은 rag-framework 참고(하이브리드 12배, 리랭커 8.5배) [확인] https://github.com/UrBaneee/rag-framework |
| 은행 도메인 | 왜 로컬 LLM인가? 개인정보는? 개정 전후 조문은? 내규와 법령이 충돌하면? | 로컬과 클라우드 모드를 나눈 근거. 망분리 규제는 2026-04-20 SaaS 예외 시행 이후 단계적으로 완화되는 중 [검색] https://byline.network/2026/10/1026-6/ · https://www.ddaily.co.kr/page/view/2026071517443276094. AI 기본법 시행과 고영향 AI 논의 [검색] https://www.lawtimes.co.kr/news/212422. 시행일 메타데이터 필터. 문서 안에 숨은 지시문(프롬프트 인젝션) 대응 [추정] |
| 설계 선택 | 프레임워크를 꼭 써야 했나? 왜 Qdrant인가? | ADR마다 대안과 기각 이유. 작은 프로젝트는 API를 직접 호출하는 편이 더 명확할 때가 많다는 지적 [확인] techinterview |

---

## AI 도구 활용 기록 방법

### 2026년 공채의 "AI 활용 역량" 평가 흐름

| 기업 / 시기 | 방식 | 보는 것 | 태그 |
|---|---|---|---|
| CJ올리브네트웍스, 2026 하반기 (보도자료 2026-09-17) | 코딩테스트를 전면 개편해 전 직무에 AI 역량진단 테스트 도입. 개발 직무는 바이브 코딩 테스트 | AI와 협업해 실제 결과물을 구현하는 능력 | [확인] https://cjnews.cj.net/?p=80060 |
| 크래프톤 (2026-08-31 기사, 하반기 인턴) | Cofa-Probe가 소스코드, AI 대화, 프롬프트, 도구 설정, 검증 실행 기록을 수집 | Technique(실행성·구조·보안), Intent(문제 정의·우선순위·지시의 질), Cognition(근거 탐색·반복 개선·오류 복구·결과 검증) | [확인] https://www.dailian.co.kr/news/view/1684386 · https://news.tf.co.kr/read/ogmeta/2376557.htm |
| 넥슨 (2026-08-27, 게임 프로그래머 인턴) | 코딩테스트를 폐지하고 AI 면접과 AI 활용 역량평가 실시 | AI 이해도, 프롬프트 작성 과정, 활용 방식, 결과물 | [확인] https://www.twig24.com/news/industry/2026/08/27/20260827500045 |
| 엔씨소프트 | 코딩테스트는 유지하고 AI 리터러시 검증을 추가 | — | [확인] https://news.tf.co.kr/read/ogmeta/2376557.htm |
| 시중은행(이름 비공개) + 그렙 (2026-10-12) | 하반기 신입 행원 PT 면접을 AI 역량평가로 대체. 개발직은 코딩테스트를 유지하고 AI 평가를 추가 | 프롬프트 기록을 분석해 과정 전체를 평가 | [확인] https://ditoday.com/?p=154678 |
| HD현대, 기아 (2026-09-12 기사) | AI 활용 역량검사, AI 문제해결력 검증 | AI 결과물을 비판적으로 분석하고 업무에 맞게 재구성하는 능력 | [확인] https://www.newsis.com/view/NISX20260911_0003785699 |
| KT (2026-08-25) | 직무 상황을 제시하고 AI 활용 과정을 평가 | — | [확인] https://www.ddaily.co.kr/page/view/2026082511042750389 |
| 배경 | 대한상의 조사에서 인사담당자의 69.2%가 채용 시 AI 역량을 고려한다고 답함 | — | [확인] 기사 재인용, 원 조사는 미확인: https://www.dailian.co.kr/news/view/1684386 |

- **용어를 구분해야 합니다.** 은행 공고의 "AI역량검사"(게임형·성향형)는 위 "AI 활용 역량평가"와 다른 종류일 수 있습니다. [검색] https://wikidocs.net/blog/@openwiki/24381/ · [추정]
- **해외 기준(Anthropic, 2025-07-10 갱신):** 지원서 초안은 본인이 쓰고 Claude로 다듬는 것은 허용합니다. 하지 않은 경험을 만들어내는 것은 금지합니다. 별도 허용이 없으면 과제와 라이브 면접에서는 AI를 쓸 수 없고, AI 사용은 투명하게 밝히도록 요구합니다. 그러니 면접에서는 AI 없이 직접 설명할 수 있어야 합니다. [확인] https://www.anthropic.com/candidate-ai-guidance

### AI 협업 기록이 감점이 아니라 강점이 되는 이유

- **"거의 맞는" 출력이 개발자들의 가장 큰 불만입니다.** Stack Overflow 2025 조사에서 84%가 AI를 쓰거나 쓸 계획이지만, 46%는 정확도를 불신하고, 66%가 "거의 맞지만 틀린" 출력을 가장 큰 불만으로 꼽았습니다. 그래서 검증 기록이 차별점이 됩니다. [확인] https://survey.stackoverflow.co/2025/ai
- **체감과 실측은 다를 수 있습니다.** METR RCT(2025)에서 숙련 개발자는 AI를 쓸 때 19% 더 느렸지만 스스로는 20% 빨라졌다고 믿었습니다. 체감이 아니라 측정으로 말해야 한다는 근거입니다. [검색] https://www.infoworld.com/article/4020931/ai-coding-tools-can-slow-down-seasoned-developers-by-19.html
- **Claude Code 공식 문서도 같은 방향입니다.** 문서가 권하는 것은 세 가지입니다. 통과/실패를 반환하는 검사를 제공할 것, 실행한 명령과 출력이라는 증거를 요구할 것, 코드를 작성하지 않은 독립 리뷰어(`/code-review`, 읽기 전용 서브에이전트)를 쓸 것. 또 리뷰 지적을 전부 반영하면 과잉 추상화가 생긴다고 경고합니다. [확인] https://code.claude.com/docs/en/best-practices
- **오픈소스 쪽 관행도 있습니다.** Ghostty는 PR에 AI 사용을 고지하게 하고, 사람이 검증하는 것을 필수로 둡니다. [검색] https://www.mintlify.com/ghostty-org/ghostty/guides/contributing

### 남길 기록 5종 (크래프톤 Cofa-Probe 수집 항목에 맞춤) [추정, 근거: 위 표]

1. **`docs/ai-usage.md`** (AI 사용 고지와 분담표): 작업 | AI가 한 것 | 내가 결정·검증한 것 | 검증 증거 링크 | 거절하거나 수정한 AI 제안.
   - 맨 아래 "거절하거나 수정한 제안" 열이 가장 강한 증거가 됩니다. HD현대가 보는 "비판적 분석과 재구성"이 바로 이것입니다.
2. **`docs/adr/NNNN-*.md`** (ADR): Nygard(2011)식 Context / Decision / Consequences에 "고려한 대안"과 "근거 실험 링크"를 더합니다. ADR은 결정 하나와 그 근거·트레이드오프·결과를 기록하고, ADR을 모은 것이 decision log가 됩니다. [확인] https://adr.github.io/
3. **실험 일지 `eval/runs/`**: 날짜, 커밋 해시, 설정(청크 크기, k, 모델 태그), 지표, 한 줄 해석. 효과가 없었던 결과(null result)도 그대로 남깁니다. [확인] 방식 참고: https://github.com/UrBaneee/rag-framework
4. **검증 증거**: 테스트, `make eval`의 출력 로그, 독립 리뷰 결과를 PR이나 커밋에 붙입니다. 커밋 트레일러(`Co-Authored-By`)로 AI 기여를 표시해 두면 투명성 근거가 됩니다. [추정, 근거: https://code.claude.com/docs/en/best-practices]
5. **`docs/learning-notes/`**: 개념을 자기 말로 정리하고 손으로 계산한 예를 둡니다(BM25 점수, RRF 순위 합산, nDCG 계산). 면접에서 AI 없이 설명할 수 있다는 증거가 됩니다. [추정, 근거: https://www.anthropic.com/candidate-ai-guidance]

ADR 최소 템플릿 [추정]:
```markdown
# ADR-0003: Kiwi 형태소 BM25 + bge-m3 dense를 RRF로 결합
- Status: Accepted (2026-11-xx)
- Context: 조문번호·금리·상품명 질의에서 dense 단독 실패 (eval/runs/2026-11-xx.md)
- Options: dense 단독 / BM25 단독 / 가중합 / RRF(k=60)
- Decision: RRF — 점수 스케일 정규화 불필요
- Consequences: 지연 +x ms, Recall@5 +y pt, 법령 장문에선 이득 작음
- AI 관여: 대안 목록은 AI 제안, 선택·실험 설계·해석은 본인
```

### 직접 이해해야 하는 부분과 AI에 맡겨도 되는 부분 [추정]

- **반드시 직접 이해하고, 가능하면 직접 작성할 부분:**
  - 청킹 규칙
  - BM25 토큰화와 품사 필터
  - RRF 함수(10줄 안팎)
  - 평가 지표 계산
  - 평가셋 라벨링
  - 거절 정책과 프롬프트 계약(인용 형식)
  - 각 ADR의 결론
- **AI에 맡겨도 되는 부분:** FastAPI 보일러플레이트, 데모 UI, Docker·스크립트, 테스트 스캐폴딩, 문서 서식. 단, 리뷰와 실행 증거가 있어야 합니다.
- **감점 패턴:**
  - "GPT를 썼다"처럼 모호한 서술
  - 근거 없는 성능 주장
  - 데모만 화려하고 견고성이 없음
  - 직접 설명하지 못하는 코드 [확인] https://www.techinterview.org/post/3233475399/ai-portfolio-built-with-ai-2026-interviews/
  - 해보지 않은 경험을 서술하는 것 [확인] https://www.anthropic.com/candidate-ai-guidance

---

## 이 저장소 권고안

### README 섹션 목록 (위에서 아래 순서) [추정, 근거: 사례 1–5]

0. 제목, 한 줄 소개, **한국어 3줄 요약**, 배지
1. **데모 GIF** (15–20초): 질문 → 조문 인용 답변 → 출처 클릭 → 근거가 없는 질문은 거절 → 로컬/Claude 전환
2. **문제 정의**: 은행 직원이 법령, 감독규정, 약관, 내규를 오가며 찾는 비용. 왜 RAG인지(키워드 검색이나 파인튜닝과 비교)
3. **결과 요약 표**: BM25(Kiwi) / dense(bge-m3) / RRF (/ +리랭커) × Recall@5, MRR@10, nDCG@10, 답변 정답률, 인용 정확도, 거절 정확도, p50/p95 지연, 질의당 비용. 평가셋 크기를 분모로 함께 적고(예: 0.83 (50/60)), "방향성 지표"라고 고지
4. **아키텍처**: Mermaid로 ingest(파싱 → 조항 단위 청킹 → 메타데이터(문서유형·시행일·조문) → Kiwi BM25 / bge-m3 → Qdrant 로컬)와 query(BM25 ∥ dense → RRF → 컨텍스트 패킹 → Ollama EXAONE 또는 Claude → 인용·거절)를 나눠 그림. Builder 계층(워크스페이스 생성)도 표시
5. **핵심 설계 결정**: 표 한 줄마다 이유와 ADR 링크
6. **평가 방법**: 질문 유형 구성, 라벨링 절차, 지표 정의, LLM 채점과 사람 채점의 일치율
7. **실패 사례와 한계**: 실패 갤러리 링크, null result, 벤치마크하지 않은 부분
8. **빠른 실행**: Windows 11 기준. `uv sync` → `ollama pull <태그>` → `.env.example` 복사 → `make demo` / `make eval`. 최소 하드웨어(8GB VRAM) 명시
9. **데이터 출처, 라이선스, 가상 데이터 고지**: 가상 은행 이름과 가상 내규임을 명시하고, 실존 은행의 브랜드를 쓰지 않음
10. **보안, 개인정보, 규제 고려**: 로컬 모드는 망분리 가정, 클라우드로 보낼 때 마스킹, 문서 속 프롬프트 인젝션 대응
11. **How this was built (AI 활용)**: Claude Code의 사용 범위, 사람이 결정·검증한 것, `docs/ai-usage.md` 링크
12. **로드맵과 회고**: LangGraph 에이전트, 리랭커, 멀티 워크스페이스. "다시 한다면 바꿀 결정" 하나

### docs/ 구조 [추정]
```
README.md
CLAUDE.md                     # AI 협업 규칙(공개 = 협업 방식 증거)
docs/
  architecture.md
  adr/0001-chunking-by-article.md  0002-kiwi-bm25.md  0003-hybrid-rrf.md
      0004-bge-m3.md  0005-qdrant-local.md  0006-llm-provider-switch.md  0007-langgraph-scope.md
  eval/README.md              # 평가셋 설계·라벨링 가이드·지표 정의
  failures.md                 # 실패 갤러리(질문/기대/실제/원인/조치)
  ai-usage.md                 # AI 사용 고지·분담표·거절한 제안
  learning-notes/             # bm25.md rrf.md embeddings.md metrics.md ollama-quant.md ...
  interview-prep.md           # 예상 질문 + 내 답 + 증거 링크
  data-sources.md             # 출처·라이선스·가상 데이터 고지
  runbook-windows.md
eval/datasets/*.jsonl  eval/runs/YYYY-MM-DD-*.md
```

### 작업별로 직접 이해해야 할 핵심 개념 [추정; 하이브리드·RRF·지표 근거는 위 출처]

| 작업 | 반드시 이해할 개념 |
|---|---|
| 환경 구축 | 가상환경과 의존성 고정(lock), Windows 경로와 인코딩, Ollama 모델 태그와 양자화(Q4 등)가 VRAM 8GB에 미치는 영향 |
| 문서 수집·파싱 | 법령의 조·항·호와 별표 구조, PDF 표 추출의 한계, 시행일과 개정 이력 메타데이터 |
| 청킹 | 구조 기반 청킹과 고정 길이 청킹, 크기·오버랩 트레이드오프(작은 청크는 검색 지표를 올려도 답변을 해칠 수 있음), 부모 문서 검색 |
| 형태소 BM25 | TF, IDF, 문서 길이 정규화(k1, b), 교착어에서 조사 분리가 필요한 이유, 품사 필터와 사용자 사전 |
| Dense 임베딩 | bi-encoder와 코사인 유사도, 다국어 임베딩의 장단점, 최대 입력 길이와 청크 크기의 관계. bge-m3 사양은 모델 카드로 확인 필요 |
| 벡터 저장 | ANN(HNSW)의 정확도·속도 트레이드오프, 페이로드 필터(문서유형, 시행일), Qdrant 로컬 모드의 한계 |
| 하이브리드 | RRF = Σ 1/(k+rank). 점수 스케일과 무관하다는 점, k의 의미, 가중합과의 차이, 하이브리드가 이득이 아닌 경우 |
| 리랭킹 | cross-encoder와 bi-encoder, 후보 수(예: 20 → 5)와 지연 |
| 평가 | Recall@k, MRR, nDCG를 손으로 계산하기. 검색 평가와 생성 평가 분리. faithfulness의 한계. LLM 채점 편향과 사람 일치율. 작은 표본의 불확실성. 평가셋 누수 |
| 생성·환각 | 근거 기반 프롬프트 계약, 인용 강제와 검증, 거절 기준, 시점(개정) 충돌, 내규와 법령 충돌 처리 |
| LLM 전환 | 로컬과 클라우드의 비용·지연·품질·보안 트레이드오프, 토큰 비용 계산, 스트리밍, 타임아웃과 재시도 |
| API | FastAPI async, SSE 스트리밍, 입력 검증, 에러 모델 |
| 에이전트 | LangGraph 상태 그래프와 도구 호출. 언제 고정 파이프라인이 더 나은지, 에이전트 행동의 가드레일 |
| Builder 플랫폼 | 워크스페이스 격리(컬렉션과 권한), 문서 추가 시 증분 색인, 워크스페이스별 평가셋 |
| 보안·규제 | 프롬프트 인젝션(문서 속 지시문), PII 마스킹, 망분리 동향, AI 기본법 고영향 AI 개념 |

---

## 출처

[확인] = 직접 열어 확인한 것, [검색] = 검색 결과 요약만 본 것.

1. [확인] techinterview.org, "The AI Portfolio: What 'Built With AI' Means in 2026 Interviews" (2026-05-06, 2026-07-12 갱신) — https://www.techinterview.org/post/3233475399/ai-portfolio-built-with-ai-2026-interviews/
2. [확인] UrBaneee/rag-framework README — https://github.com/UrBaneee/rag-framework
3. [확인] allganize RAG-Evaluation-Dataset-KO (2024) — https://huggingface.co/datasets/allganize/RAG-Evaluation-Dataset-KO
4. [확인] Anthropic, Contextual Retrieval (2024-09-19) — https://www.anthropic.com/news/contextual-retrieval
5. [확인] Cinnamon/kotaemon — https://github.com/Cinnamon/kotaemon
6. [확인] Marker-Inc-Korea/AutoRAG — https://github.com/Marker-Inc-Korea/AutoRAG
7. [검색] 위키독스 Kiwi 하이브리드 실측 (2026-08) — https://wikidocs.net/389782
8. [검색] 법률 RAG 검색 연구 — https://arxiv.org/pdf/2510.06999
9. [확인] KalyanKS RAG 면접 질문 모음 — https://github.com/KalyanKS-NLP/RAG-Interview-Questions-and-Answers-Hub
10. [확인] Hamel Husain, 최소 평가 구성 (2025-06-29, 2026-05-04 수정) — https://hamel.dev/blog/posts/evals-faq/whats-a-minimum-viable-evaluation-setup.html
11. [검색] Hamel Husain, 오류 분석 (2025-06-27) — https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html
12. [확인 초록] Barnett et al., Seven Failure Points (2024-01) — https://arxiv.org/abs/2401.05856
13. [검색] Ragas 지표 문서 — https://docs.ragas.io/en/v0.1.21/references/metrics.html
14. [검색] RRF k=60 설명 — https://techsy.io/ko/blog/hybrid-search-bm25-vs-vector-rrf
15. [검색] 국내 포트폴리오 README 조언 — https://wikidocs.net/367963 · https://comento.kr/job-questions/모든회사/모든직무/
16. [검색] 검색 실패와 생성 실패 분리 진단 — https://wikidocs.net/435416
17. [확인] CJ뉴스룸, CJ올리브네트웍스 하반기 채용 (2026-09-17) — https://cjnews.cj.net/?p=80060
18. [확인] 데일리안, 크래프톤 Cofa-Probe (2026-08-31) — https://www.dailian.co.kr/news/view/1684386
19. [확인] 더팩트, 게임업계 AI 평가 (2026-10-15) — https://news.tf.co.kr/read/ogmeta/2376557.htm
20. [확인] 트위그, 넥슨 (2026-08-27) — https://www.twig24.com/news/industry/2026/08/27/20260827500045
21. [확인] 디지털인사이트, 그렙 AI 역량평가 시중은행 (2026-10-12) — https://ditoday.com/?p=154678
22. [확인] 뉴시스, HD현대·기아 등 (2026-09-12) — https://www.newsis.com/view/NISX20260911_0003785699
23. [확인] 디지털데일리, KT (2026-08-25) — https://www.ddaily.co.kr/page/view/2026082511042750389
24. [확인] 시사경제, 은행 채용 (2026-09-07) — https://sateconomy.co.kr/news/view/1065571246306524
25. [확인] 한경BUSINESS, 은행 AI 채용 (2026-09-07) — https://magazine.hankyung.com/business/article/202609077767b
26. [확인] 이코노미스트, 은행 채용과 금융 이해도 (2026-09-26) — https://economist.co.kr/article/view/ecn202609220047
27. [확인] 원티드, 애자일소다 RAG/Data Engineer (금융, 마감) — https://www.wanted.co.kr/wd/374884
28. [검색] AI역량검사 유형 정리 — https://wikidocs.net/blog/@openwiki/24381/
29. [확인] Anthropic 지원자 AI 가이드 (2025-07-10) — https://www.anthropic.com/candidate-ai-guidance
30. [확인] Claude Code Best practices — https://code.claude.com/docs/en/best-practices
31. [확인] Stack Overflow Developer Survey 2025, AI — https://survey.stackoverflow.co/2025/ai
32. [검색] METR 연구 보도 (2025) — https://www.infoworld.com/article/4020931/ai-coding-tools-can-slow-down-seasoned-developers-by-19.html
33. [검색] Ghostty AI 정책 — https://www.mintlify.com/ghostty-org/ghostty/guides/contributing
34. [확인] ADR — https://adr.github.io/
35. [검색] 금융권 망분리 — https://byline.network/2026/10/1026-6/ · https://www.ddaily.co.kr/page/view/2026071517443276094
36. [검색] AI 기본법 — https://www.lawtimes.co.kr/news/212422
