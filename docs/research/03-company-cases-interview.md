# 국내 기업 LLM/RAG 실무 사례 · 면접 질문 (2026-11-09 조사)

[확인] 원문 확인 · [2차] 기사/요약 경유 · [추정] 해석

## 1. 실무 사례

| 사례 | 겪은 문제 → 해결 |
|---|---|
| 우아한형제들 디자인시스템 챗봇 (2026-05) [확인] | `Button` 질문에 `IconButton` 문서가 섞임 → 질문에서 컴포넌트명 추출 → **메타데이터 필터**. 출력 가드레일이 스트리밍 지연 → 입력 가드레일 + async |
| 우아한형제들 교육운영 RAG (2026-03) [확인] | MCP 방식은 VPC DB 접근·컨텍스트 초과로 실패 → VPC 내부 RAG 서버, **부모-자식 청킹**. 평가 3지표, "90% 쿼리 3초 이내" 목표 |
| 당근 KAMP (2025-11) [2차] | 멀티에이전트 CS. **사람 판정과 90% 이상 일치 확인 후 LLM-as-judge 전환**. 남은 과제는 비용 |
| 삼성SDS RAG 투자심사역 [확인] | PDF·Excel·PPT 혼재 → 마크다운 정규화, **하이브리드 + 리랭크**, 질의 재작성, 체크섬 버전관리, RBAC. 40h → 8h |
| **토스 SNAP (2026-10)** [확인] | 비용·지연 → 소형 모델 우선, confidence 낮으면 대형 LLM fallback. 6일간 입력 토큰 76억 절감 |
| **신한은행 금융지식 Q&A (LG CNS)** [2차] | 문서 10만+, 파싱 엔진, 하이브리드 검색, **개인정보 외부 유출 방지 탐지** |
| **KB국민은행 플랫폼 2.0 (2026-10)** [2차] | **입력 PII 마스킹**, LLM-judge(정확성·근거성·규정준수·환각) → 승인/재검토/차단 |
| **카카오뱅크 AI 검색** [2차] | 혁신금융서비스 지정, 금융보안원 평가, 고객 정보 외부 미저장 |
| **카사코리아 CS 챗봇** [확인] | 유사도 ≥0.85면 LLM 없이 FAQ 즉답. **기본 PII 룰이 영어 중심 → 주민번호·계좌 한국어 정규식 추가**. 표 인식·신구 문서 혼재 해결 |
| 당근페이 Text-to-SQL [확인] | 스키마를 하이브리드 + 리랭커로 검색, SQL self-reflection |

**금융권 규제 맥락** [확인, 삼성SDS 2026-11-06]
- 2026-04-20부터 보안 요건 갖춘 SaaS는 망분리 예외. 단 고유식별·개인신용정보 처리는 제외.
- 2026-06 개정 금융 AI 가이드라인: **Human-in-the-loop** 요구.
- RAG는 검색·생성 양 단계에서 **원본 문서 접근권한 유지** 필요.

## 2. 공통 실무 고민 Top 5
1. 검색 정확도 — 메타데이터 필터, 하이브리드 + 리랭커, 질의 재작성
2. 문서 파싱·버전 충돌 — PDF 표, 구버전/신버전 혼재
3. 권한·개인정보 — RBAC/ACL, 한국어 PII 탐지·마스킹, 폐쇄망
4. 평가 — 사람 평가 → LLM-judge, 근거성/환각 지표, 변경 시 회귀평가
5. 비용·지연 — FAQ 즉답 경로, 소형 모델 라우팅

## 3. 예상 면접 질문
1. 청킹 전략·크기는 어떻게 정했나?
2. 리랭킹이 검색 품질을 어떻게 높이나?
3. 순수 벡터 검색보다 하이브리드가 나은 이유는?
4. RAG 평가 방법 — faithfulness, context precision/recall 설명
5. 근거 문서가 있는데도 환각이 나면?
6. HNSW / IVF / Flat 인덱스 비교, recall-지연 트레이드오프
7. 유사도는 높은데 결과가 엉뚱하다면 원인은?
8. LLM-as-judge의 한계는?
9. 파인튜닝 vs RAG vs 프롬프트 엔지니어링, 언제 무엇을?
10. 에이전트 루프 종료 조건, 무한 루프 방지
11. 품질 유지하며 비용 줄이는 방법
12. 직접/간접 프롬프트 인젝션 방어
13. 프로덕션 LLM 모니터링 지표
14. 권한 없는 문서가 답변에 섞이지 않게 하려면? [추정]
15. 구버전 약관이 검색되면? [추정]
16. 망분리 환경이면 아키텍처를 어떻게 바꾸나? [추정]
17. 임계값(예: 0.85)은 어떤 근거로 정했나? [추정]

**학사 지원자에게 면접관이 보는 것** [2차·추정]
- 기술 나열보다 **왜 그 구조를 골랐는지**와 기대 효과
- 정량 성과(시간 단축, 정확도), 본인이 주도한 범위, LLM 한계 인정과 보완책(HITL)
- 학위가 없으면 임베딩·ANN·컨텍스트 한계 같은 **원리를 꼬리질문으로** 깊게 검증 → 공개된 실험 기록이 논문 실적을 대신함

## 출처
- https://techblog.woowahan.com/26319/ · https://techblog.woowahan.com/25900/
- https://toss.tech/article/snap-llm-cost-reduction
- https://byline.network/2025/11/25_danggn-2/ · https://byline.network/2025/05/12-404/ · https://byline.network/2026/10/1-1496/
- https://www.samsungsds.com/kr/case-study/axe-partners-ai-investment-due-diligence.html
- https://www.samsungsds.com/kr/insights/financial-ai-security-network-separation.html
- https://aws.amazon.com/ko/blogs/tech/daangnpay-text-to-sql-1 · https://aws.amazon.com/ko/blogs/tech/kasakorea-agentic-cs-chatbot
- https://techblog.lycorp.co.jp/en/rag-based-bot-for-streamlining-inquiry-responses
- https://github.com/amitshekhariitbhu/ai-engineering-interview-questions
- https://toss.im/career/job-detail?job_id=7646941003
