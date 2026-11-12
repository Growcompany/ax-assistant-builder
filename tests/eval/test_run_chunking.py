"""평가 CLI의 --max-chunk-chars (M-2 Task 25): 긴 조항을 나눠 색인하고 보고서에 남긴다.

옵션이 없으면 보고서와 파일 이름이 예전과 같다(tests/eval/test_run.py가 지킨다).
"""

from pathlib import Path

import pytest
from test_run import (
    CONFIG_KEYS_WITHOUT_RERANKER,
    FakeReranker,
    cli_args,
    use_reranker,
    write_corpus,
)

from axbuilder.eval.run import EvalReport, main
from axbuilder.ingest.chunking import MIN_MAX_CHARS

# 제2조(정답 근거)가 300자를 넘는다: --max-chunk-chars 200이면 ①·②가 따로 → 청크 3개
LONG_GUIDE = (
    "제1조(목적) 이 지침은 상담 응대 원칙을 정한다.\n"
    "제2조(콜백) ① 콜백 요청은 4시간 안에 처리한다. " + "가" * 150 + "\n② " + "나" * 150 + "\n"
)


def write_long_corpus(root: Path) -> Path:
    corpus = write_corpus(root, with_url_doc=False)
    (corpus / "internal" / "guide.md").write_text(LONG_GUIDE, encoding="utf-8")
    return corpus


def run(tmp_path: Path, monkeypatch, name: str, *extra: str) -> tuple[Path, EvalReport]:
    corpus = tmp_path / "corpus" / "bank"
    if not corpus.exists():
        corpus = write_long_corpus(tmp_path)
    monkeypatch.setenv("AXB_DATA_DIR", str(tmp_path / "data"))
    reports = tmp_path / name
    assert main(cli_args(corpus, reports, *extra)) == 0
    (saved,) = (reports / "bank").glob("*.json")
    return saved, EvalReport.model_validate_json(saved.read_text(encoding="utf-8"))


def test_max_chunk_chars_splits_the_corpus_and_is_recorded(tmp_path, monkeypatch):
    saved, report = run(tmp_path, monkeypatch, "split", "--max-chunk-chars", "200")
    assert saved.name.endswith("-bm25-m200.json")
    assert list(report.config) == [*CONFIG_KEYS_WITHOUT_RERANKER, "chunking"]
    assert report.config["chunking"] == {"max_chars": 200}
    assert report.config["n_chunks"] == 3
    # 나눈 조각도 조항 라벨이 그대로라 근거 "제2조"에 맞는다
    assert report.recall_at_k == 1.0
    assert {r["article"] for r in report.items[0]["retrieved"]} <= {"제1조", "제2조"}


def test_without_the_option_nothing_is_split_or_recorded(tmp_path, monkeypatch):
    # 서버 설정(AXB_MAX_CHUNK_CHARS)은 보지 않는다: 평가 조건은 명령줄에서만 정한다
    monkeypatch.setenv("AXB_MAX_CHUNK_CHARS", "200")
    saved, report = run(tmp_path, monkeypatch, "whole")
    assert saved.name.endswith("-bm25.json") and "-m" not in saved.name.rsplit("-bm25", 1)[1]
    assert list(report.config) == CONFIG_KEYS_WITHOUT_RERANKER
    assert report.config["n_chunks"] == 2


def test_max_chunk_chars_with_reranker_goes_after_the_reranker_label(tmp_path, monkeypatch):
    use_reranker(monkeypatch, FakeReranker())
    extra = ("--reranker", "kure-nano", "--max-chunk-chars", "250")
    saved, report = run(tmp_path, monkeypatch, "both", *extra)
    assert saved.name.endswith("-bm25+kure-nano-c30-m250.json")
    assert list(report.config)[-5:] == [
        "reranker",
        "candidates",
        "rerank_truncated",
        "rerank_pairs",
        "chunking",
    ]
    assert report.config["chunking"] == {"max_chars": 250}


@pytest.mark.parametrize("bad", ["0", str(MIN_MAX_CHARS - 1), "many"])
def test_bad_max_chunk_chars_is_a_usage_error(tmp_path, monkeypatch, capsys, bad):
    corpus = write_long_corpus(tmp_path)
    monkeypatch.setenv("AXB_DATA_DIR", str(tmp_path / "data"))
    reports = tmp_path / "reports"
    with pytest.raises(SystemExit) as exited:
        main(cli_args(corpus, reports, "--max-chunk-chars", bad))
    assert exited.value.code == 2
    assert "--max-chunk-chars" in capsys.readouterr().err
    assert not reports.exists()
