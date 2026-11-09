"""골든셋 읽기(load_golden)·검증 규칙(validate_golden), 저장소 골든셋과 실제 청크 라벨 대조."""

import json
from collections import Counter
from pathlib import Path

import pytest

from axbuilder.corpus import MANIFEST_NAME, load_sources, source_file
from axbuilder.eval.dataset import Evidence, GoldItem, load_golden, validate_golden
from axbuilder.generation.answer import REFUSAL_TEXT
from axbuilder.ingest.chunking import chunk_document
from axbuilder.ingest.loaders import load_document
from axbuilder.models import Chunk
from axbuilder.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "corpus" / "bank" / "golden.jsonl"
SOURCES = REPO_ROOT / "corpus" / "bank" / "sources.yaml"
KNOWN = {"banking-act", "prod-fixed-deposit"}


def gold(**overrides) -> GoldItem:
    fields = {
        "id": "bank-001",
        "question": "은행법상 대출금리에 반영하면 안 되는 항목은?",
        "answer": "지급준비금, 예금보험료 등",
        "evidence": [{"doc_id": "banking-act", "article": "제30조의3"}],
        "qtype": "lookup",
    }
    return GoldItem(**{**fields, **overrides})


def line(**overrides) -> str:
    return json.dumps(gold(**overrides).model_dump(exclude_none=True), ensure_ascii=False)


def write_lines(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---- 저장소 골든셋 ----


def test_repo_golden_set_is_valid():
    items = load_golden(GOLDEN)
    known = {s.doc_id for s in load_sources(SOURCES)}
    assert validate_golden(items, known) == []
    assert len(items) >= 50


def test_repo_golden_set_distribution():
    items = load_golden(GOLDEN)
    counts = Counter(item.qtype for item in items)
    assert counts == {"lookup": 30, "table": 8, "multi_doc": 6, "out_of_scope": 6}
    for item in items:
        if item.qtype == "out_of_scope":
            assert item.answer == REFUSAL_TEXT, item.id


# ---- load_golden ----


def test_load_golden_reads_one_item_per_line(tmp_path):
    path = write_lines(
        tmp_path,
        [
            line(),
            line(id="bank-002", evidence=[{"doc_id": "prod-fixed-deposit"}], qtype="table"),
        ],
    )
    items = load_golden(path)
    assert [item.id for item in items] == ["bank-001", "bank-002"]
    assert items[0].evidence == [Evidence(doc_id="banking-act", article="제30조의3")]
    # 조항이 없는 문서 단위 근거: article·page는 None
    assert items[1].evidence[0].article is None and items[1].evidence[0].page is None


def test_load_golden_keeps_unicode_line_separator_inside_a_line(tmp_path):
    # ensure_ascii=False인 JSON은 U+2028을 그대로 쓴다. 이 글자에서 줄을 나누면 안 된다
    path = write_lines(tmp_path, [line(question="첫 줄\u2028둘째 줄"), line(id="bank-002")])
    items = load_golden(path)
    assert len(items) == 2 and items[0].question == "첫 줄\u2028둘째 줄"


def test_load_golden_reports_line_number_of_bad_json(tmp_path):
    path = write_lines(tmp_path, [line(), '{"id": "bank-002", "question": '])
    with pytest.raises(ValueError, match=r"golden\.jsonl:2: invalid JSON"):
        load_golden(path)


def test_load_golden_reports_line_number_of_blank_line(tmp_path):
    path = write_lines(tmp_path, [line(), "   ", line(id="bank-003")])
    with pytest.raises(ValueError, match=r"golden\.jsonl:2: blank line"):
        load_golden(path)


def test_load_golden_reports_line_number_of_invalid_item(tmp_path):
    bad = line(id="bank-003").replace('"qtype": "lookup"', '"qtype": "trivia"')
    path = write_lines(tmp_path, [line(), line(id="bank-002"), bad])
    with pytest.raises(ValueError, match=r"(?s)golden\.jsonl:3: .*qtype"):
        load_golden(path)


def test_load_golden_rejects_misspelled_field(tmp_path):
    # "artcle" 오타가 조용히 무시되면 문서 단위 근거가 되어 아무 청크나 정답으로 센다
    path = write_lines(tmp_path, [line().replace('"article"', '"artcle"')])
    with pytest.raises(ValueError, match=r"(?s)golden\.jsonl:1: .*artcle"):
        load_golden(path)


def test_load_golden_rejects_non_object_line(tmp_path):
    path = write_lines(tmp_path, [line(), '["bank-002"]'])
    with pytest.raises(ValueError, match=r"golden\.jsonl:2: "):
        load_golden(path)


# ---- validate_golden: 규칙 4가지 ----


def test_validate_accepts_valid_items():
    items = [
        gold(),
        gold(id="bank-002", evidence=[{"doc_id": "prod-fixed-deposit"}], qtype="table"),
        gold(id="bank-003", answer=REFUSAL_TEXT, evidence=[], qtype="out_of_scope"),
    ]
    assert validate_golden(items, KNOWN) == []


def test_validate_reports_duplicate_id():
    problems = validate_golden([gold(), gold(question="다른 질문")], KNOWN)
    assert problems == ["bank-001: duplicate id"]


def test_validate_reports_unknown_doc_id():
    item = gold(evidence=[{"doc_id": "banking-act"}, {"doc_id": "no-such-doc"}], qtype="multi_doc")
    assert validate_golden([item], KNOWN) == ["bank-001: unknown doc_id 'no-such-doc'"]


def test_validate_reports_out_of_scope_with_evidence():
    item = gold(qtype="out_of_scope")
    assert validate_golden([item], KNOWN) == ["bank-001: out_of_scope item must have no evidence"]


@pytest.mark.parametrize(
    "qtype", ["lookup", "table", "multi_doc", "needs_human", "no_access", "outdated"]
)
def test_validate_reports_missing_evidence(qtype):
    item = gold(evidence=[], qtype=qtype)
    assert validate_golden([item], KNOWN) == [f"bank-001: {qtype} item needs evidence"]


def test_validate_reports_every_problem_not_just_the_first():
    items = [
        gold(),
        gold(evidence=[{"doc_id": "no-such-doc"}]),
        gold(id="bank-003", qtype="out_of_scope"),
        gold(id="bank-004", evidence=[]),
    ]
    assert validate_golden(items, KNOWN) == [
        "bank-001: duplicate id",
        "bank-001: unknown doc_id 'no-such-doc'",
        "bank-003: out_of_scope item must have no evidence",
        "bank-004: lookup item needs evidence",
    ]


# ---- 실제 청크와 라벨 대조 (내려받은 원문이 있을 때만: CI에서는 skip) ----


def hits(chunk: Chunk, ev: Evidence) -> bool:
    """Task 9 matches()와 같은 규칙: doc_id가 같고, article·page가 있으면 그것도 같다."""
    return (
        chunk.doc_id == ev.doc_id
        and (ev.article is None or chunk.article == ev.article)
        and (ev.page is None or chunk.page == ev.page)
    )


@pytest.fixture(scope="module")
def real_chunks() -> dict[str, list[Chunk]]:
    data_dir = Settings.from_env().data_dir
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir
    raw_dir = data_dir / "workspaces" / "bank" / "raw"
    if not (raw_dir / MANIFEST_NAME).is_file():
        pytest.skip(f"no downloaded corpus in {raw_dir} (python scripts/download_corpus.py bank)")
    chunks: dict[str, list[Chunk]] = {}
    for source in load_sources(SOURCES):
        path = source_file(source, raw_dir, REPO_ROOT)
        assert path.is_file(), f"{source.doc_id}: {path} missing; re-run download_corpus.py bank"
        chunks[source.doc_id] = chunk_document(load_document(path, source.doc_meta(), "bank"))
    return chunks


def test_every_golden_evidence_matches_a_real_chunk(real_chunks):
    missed = [
        f"{item.id}: {ev.doc_id} {ev.article or '(문서 단위)'}"
        for item in load_golden(GOLDEN)
        for ev in item.evidence
        if not any(hits(chunk, ev) for chunk in real_chunks[ev.doc_id])
    ]
    assert missed == []


def test_golden_article_labels_match_exactly_one_chunk(real_chunks):
    # 같은 라벨이 청크 2개에 붙으면(부칙을 못 알아봐 본문 제7조와 부칙 제7조가 겹치는 경우)
    # 엉뚱한 청크도 정답으로 센다
    ambiguous = {
        f"{item.id}: {ev.doc_id} {ev.article}": n
        for item in load_golden(GOLDEN)
        for ev in item.evidence
        if ev.article is not None
        and (n := sum(hits(chunk, ev) for chunk in real_chunks[ev.doc_id])) != 1
    }
    assert ambiguous == {}
