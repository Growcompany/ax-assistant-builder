# RAG Portfolio Project

> AI/LLM 엔지니어 직무 지원용 RAG(Retrieval-Augmented Generation) 프로젝트.
> 주제와 아키텍처는 채용공고 조사 결과를 바탕으로 확정 예정 — [docs/research](docs/research/) 참고.

## 개발 환경 세팅

```bash
python -m venv .venv
# Windows (Git Bash): source .venv/Scripts/activate
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # API 키 등 채우기
```

## 자주 쓰는 명령

```bash
pytest -q             # 테스트
ruff check .          # 린트
ruff format .         # 포맷
```

## 폴더 구조

```
src/rag_app/      # 애플리케이션 코드
tests/            # 테스트
data/raw/         # 원본 문서 (git 제외)
data/processed/   # 전처리 결과 (git 제외)
docs/             # 설계 문서, 조사 자료, 실험 기록
.github/workflows # CI (lint + test)
```
