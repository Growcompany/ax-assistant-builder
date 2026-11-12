"""골든셋 읽기(load_golden)·검증 규칙(validate_golden), 저장소 골든셋과 실제 청크 라벨 대조."""

import json
from collections import Counter
from pathlib import Path

import pytest

from axbuilder.corpus import MANIFEST_NAME, load_sources, source_file
from axbuilder.eval.dataset import TAGS, Evidence, GoldItem, load_golden, validate_golden
from axbuilder.eval.metrics import matches
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


def test_tags_are_optional_and_default_to_empty(tmp_path):
    # v0 문항(태그 없음)과 v1 문항(태그 있음)이 한 파일에 섞여도 읽힌다
    path = write_lines(tmp_path, [line(), line(id="bank-002", tags=["article_ref"])])
    items = load_golden(path)
    assert items[0].tags == [] and items[1].tags == ["article_ref"]


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


@pytest.mark.parametrize(
    ("content", "message"), [("", r"golden\.jsonl: no items"), ("\n", r"golden\.jsonl:1: blank")]
)
def test_load_golden_rejects_empty_file(tmp_path, content, message):
    # 0문항으로 평가가 "성공"하면 잘린 파일을 알아채지 못한다
    path = tmp_path / "golden.jsonl"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_golden(path)


# ---- validate_golden: 계획의 규칙 4가지 ----


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


# ---- validate_golden: 원문 없이 CI에서 잡을 수 있는 라벨·문구 실수 ----


@pytest.mark.parametrize("article", ["제7조", "제30조의3", "부칙 제2조", "부칙 제1조의2", "부칙"])
def test_validate_accepts_article_labels_the_chunker_produces(article):
    item = gold(evidence=[{"doc_id": "banking-act", "article": article}])
    assert validate_golden([item], KNOWN) == []


@pytest.mark.parametrize(
    "article", ["7조", "제 7조", "제7조 ", "제7조제1항", "제7조(목적)", "부칙제2조", "부칙 7조", ""]
)
def test_validate_reports_malformed_article_label(article):
    # 형식이 틀린 라벨은 어떤 청크의 article과도 같지 않아 그 근거가 조용히 0점이 된다
    item = gold(evidence=[{"doc_id": "banking-act", "article": article}])
    assert validate_golden([item], KNOWN) == [f"bank-001: malformed article label {article!r}"]


def test_validate_reports_duplicate_evidence():
    ev = {"doc_id": "banking-act", "article": "제30조의3"}
    item = gold(evidence=[ev, {"doc_id": "prod-fixed-deposit"}, ev], qtype="multi_doc")
    assert validate_golden([item], KNOWN) == ["bank-001: duplicate evidence banking-act 제30조의3"]


def test_tag_vocabulary_is_the_plan_list():
    assert TAGS == (
        "article_ref",
        "product_name",
        "negative_answer",
        "paraphrase",
        "internal_only",
    )


def test_validate_accepts_known_tags():
    assert validate_golden([gold(tags=list(TAGS))], KNOWN) == []


def test_validate_reports_unknown_tag():
    # 오타 태그("article-ref")는 태그별 표에서 조용히 빠지므로 알린다
    item = gold(tags=["article_ref", "article-ref"])
    assert validate_golden([item], KNOWN) == ["bank-001: unknown tag 'article-ref'"]


def test_validate_reports_blank_question_and_answer():
    assert validate_golden([gold(question=" ", answer="")], KNOWN) == [
        "bank-001: empty question",
        "bank-001: empty answer",
    ]


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


# ---- 실제 청크와 라벨 대조 ----
# 저장소에 있는 가상 문서 6개(path 항목)는 항상(CI 포함) 대조한다.
# 내려받는 원문 7개(url 항목)는 raw/manifest.json이 있을 때만 대조하고,
# 그때는 13개 모두 있어야 한다.
# 채점은 평가 실행기와 같은 metrics.matches()로 한다(두 규칙이 갈라지지 않게).


def chunk_corpus(raw_dir: Path) -> dict[str, list[Chunk]]:
    """doc_id → 실제 청크. url 문서는 raw_dir에 manifest가 있을 때만(그때는 빠짐없이) 나눈다."""
    downloaded = (raw_dir / MANIFEST_NAME).is_file()
    chunks: dict[str, list[Chunk]] = {}
    for source in load_sources(SOURCES):
        if source.url is not None and not downloaded:
            continue  # 원문을 받지 않은 환경(CI): python scripts/download_corpus.py bank
        path = source_file(source, raw_dir, REPO_ROOT)
        assert path.is_file(), f"{source.doc_id}: {path} missing; re-run download_corpus.py bank"
        chunks[source.doc_id] = chunk_document(load_document(path, source.doc_meta(), "bank"))
    return chunks


@pytest.fixture(scope="module")
def real_chunks() -> dict[str, list[Chunk]]:
    data_dir = Settings.from_env().data_dir
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir
    return chunk_corpus(data_dir / "workspaces" / "bank" / "raw")


def checkable_evidence(real_chunks: dict[str, list[Chunk]]) -> list[tuple[GoldItem, Evidence]]:
    pairs = [
        (item, ev)
        for item in load_golden(GOLDEN)
        for ev in item.evidence
        if ev.doc_id in real_chunks
    ]
    assert pairs, "no golden evidence points to an available document"
    return pairs


def test_without_downloaded_corpus_repo_documents_are_still_checked(tmp_path):
    # manifest가 없는 빈 raw 폴더 = CI. 저장소의 가상 문서 6개는 그래도 대조 대상이어야 한다
    chunks = chunk_corpus(tmp_path)
    assert set(chunks) == {s.doc_id for s in load_sources(SOURCES) if s.path is not None}
    assert any(ev.article for _, ev in checkable_evidence(chunks))


def test_every_golden_evidence_matches_a_real_chunk(real_chunks):
    missed = [
        f"{item.id}: {ev.doc_id} {ev.article or '(문서 단위)'}"
        for item, ev in checkable_evidence(real_chunks)
        if not any(matches(chunk, ev) for chunk in real_chunks[ev.doc_id])
    ]
    assert missed == []


def test_golden_article_labels_match_exactly_one_chunk(real_chunks):
    # 같은 라벨이 청크 2개에 붙으면(부칙을 못 알아봐 본문 제7조와 부칙 제7조가 겹치는 경우)
    # 엉뚱한 청크도 정답으로 센다
    ambiguous = {
        f"{item.id}: {ev.doc_id} {ev.article}": n
        for item, ev in checkable_evidence(real_chunks)
        if ev.article is not None
        and (n := sum(matches(chunk, ev) for chunk in real_chunks[ev.doc_id])) != 1
    }
    assert ambiguous == {}
