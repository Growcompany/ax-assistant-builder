"""긴 조항 나누기 (M-2 Task 25): chunk_by_article(doc, max_chars).

max_chars가 None이면 지금과 바이트까지 같다. 값이 있으면 그보다 긴 본문 조항을 항(①) → 호(1.) →
목(가.) → 줄 → 글자 수 순서로 나누고, 이웃 조각을 max_chars 안에서 욕심내어 묶는다. 둘째 조각부터
조항 머리("제11조(대리인 거래)")를 붙인다. article·page는 원래 조항 그대로다. 부칙은 나누지 않는다.
"""

import hashlib
import re
from pathlib import Path

import pytest

from axbuilder.corpus import MANIFEST_NAME, load_sources, source_file
from axbuilder.eval.run import load_corpus_chunks
from axbuilder.ingest.chunking import MIN_MAX_CHARS, chunk_by_article, chunk_document
from axbuilder.models import Chunk, DocMeta, Document, Page
from axbuilder.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES = REPO_ROOT / "corpus" / "bank" / "sources.yaml"

meta = DocMeta(doc_id="law-1", title="가계대출약관", source="tests", doc_type="terms")


def law(*pages: str, paged: bool = False, access_level: str = "public") -> Document:
    return Document(
        meta=meta.model_copy(update={"access_level": access_level}),
        workspace_id="bank",
        pages=[Page(number=i, text=text) for i, text in enumerate(pages, start=1)],
        paged=paged,
    )


def syllables(start: int, n: int) -> str:
    """서로 다른 한글 n자 (자른 자리를 글자로 확인할 수 있다)."""
    return "".join(chr(0xAC00 + start + i) for i in range(n))


P1 = "① " + "가" * 120  # 122자
P2 = "② " + "나" * 120
P3 = "③ " + "다" * 120
ARTICLE_11 = (
    f"제11조(대리인 거래) {P1}\n{P2}\n{P3}"  # 머리 12자 + 1 + 122 + 1 + 122 + 1 + 122 = 381자
)
TWO_ARTICLES = law(f"{ARTICLE_11}\n제12조(기록) 라")

ITEMS = [f"{i}. " + syllables(i * 100, 90) for i in range(1, 5)]  # 호마다 93자
P_INTRO = "① 다음 각 호의 경우에는 할 수 있다."  # 22자
ARTICLE_3 = "제3조(정의) " + P_INTRO + "\n" + "\n".join(ITEMS) + "\n② 끝."


def non_space(text: str) -> str:
    return re.sub(r"\s", "", text)


def split_back(pieces: list[Chunk]) -> str:
    """나눈 조각에서 둘째 조각부터 붙인 머리 줄을 떼고 이어 붙인다."""
    first, *rest = pieces
    return first.text + "".join(piece.text.split("\n", 1)[1] for piece in rest)


def align(whole: list[Chunk], split: list[Chunk]) -> list[list[Chunk]]:
    """나누기 전 청크마다 그 청크에서 나온 조각들 (조각은 원래 자리에 차례로 나온다)."""
    groups: list[list[Chunk]] = []
    pieces = iter(split)
    for original in whole:
        target = non_space(original.text)
        group = [next(pieces)]
        while len(non_space(split_back(group))) < len(target):
            group.append(next(pieces))
        assert {piece.article for piece in group} == {original.article}, original.chunk_id
        groups.append(group)
    assert next(pieces, None) is None
    return groups


# ---- None이면 지금과 같다 ----


@pytest.mark.parametrize(
    "document",
    [
        TWO_ARTICLES,
        law(ARTICLE_3),
        law("머리말\n제1조(목적) 가\n제2조(정의) 나\n부칙\n제1조(시행일) 다\n제2조 생략"),
    ],
)
def test_max_chars_none_is_the_current_result(document):
    assert chunk_by_article(document, max_chars=None) == chunk_by_article(document)
    assert chunk_document(document, max_chars=None) == chunk_document(document)
    assert [c.text for c in chunk_by_article(document)] == [
        c.text for c in chunk_by_article(document, max_chars=20_000)
    ]


def test_short_articles_are_not_split():
    assert chunk_by_article(TWO_ARTICLES, max_chars=381) == chunk_by_article(TWO_ARTICLES)


# ---- 나누는 자리와 묶기 ----


def test_long_article_is_split_at_paragraph_marks_with_the_header_repeated():
    chunks = chunk_by_article(TWO_ARTICLES, max_chars=300)
    assert [c.text for c in chunks] == [
        f"제11조(대리인 거래) {P1}\n{P2}",  # ①과 ②는 함께 들어간다(258자)
        f"제11조(대리인 거래)\n{P3}",
        "제12조(기록) 라",
    ]
    assert [c.article for c in chunks] == ["제11조", "제11조", "제12조"]
    assert [c.chunk_id for c in chunks] == ["law-1::0000", "law-1::0001", "law-1::0002"]


def test_paragraph_longer_than_max_chars_is_split_at_items_and_packed_with_neighbours():
    # ①(398자)은 호 경계에서 나누고, ① 4호와 ② 끝은 한 조각으로 묶는다
    chunks = chunk_by_article(law(ARTICLE_3, "제4조(기타) 마"), max_chars=250)
    assert [c.text for c in chunks] == [
        f"제3조(정의) {P_INTRO}\n{ITEMS[0]}\n{ITEMS[1]}",
        f"제3조(정의)\n{ITEMS[2]}\n{ITEMS[3]}\n② 끝.",
        "제4조(기타) 마",
    ]


def test_subitems_are_the_next_boundary_after_items():
    subitems = [f"{mark}. " + syllables(i * 100, 90) for i, mark in enumerate("가나다", 1)]
    article = "제5조(목) ① 다음 각 호와 같다.\n1. 다음 각 목의 것\n" + "\n".join(subitems)
    chunks = chunk_by_article(law(article, "제6조(기타) 바"), max_chars=220)
    assert [c.text for c in chunks[:2]] == [
        "제5조(목) ① 다음 각 호와 같다.\n1. 다음 각 목의 것\n" + subitems[0],
        "제5조(목)\n" + "\n".join(subitems[1:]),
    ]


def test_line_without_any_mark_is_cut_at_a_space():
    words = " ".join(f"{syllables(i * 4, 4)}" for i in range(100))  # 4글자 낱말 100개
    chunks = chunk_by_article(law(f"제5조(긴 조항) {words}", "제6조(기타) 바"), max_chars=200)
    pieces = [c for c in chunks if c.article == "제5조"]
    assert len(pieces) == 3
    assert all(len(c.text) <= 200 for c in pieces)
    assert all(c.text.startswith("제5조(긴 조항)") for c in pieces)
    # 낱말 가운데서 자르지 않는다
    words_back = [w for c in pieces for w in c.text.removeprefix("제5조(긴 조항)").split()]
    assert words_back == words.split()


def test_text_without_spaces_is_cut_by_characters():
    body = syllables(0, 500)
    chunks = chunk_by_article(law(f"제5조(긴 조항) {body}", "제6조(기타) 바"), max_chars=200)
    pieces = [c for c in chunks if c.article == "제5조"]
    # 머리 9자: 첫 조각 = 머리 + 공백 + 190자, 다음 조각 = 머리 + 줄바꿈 + 190자, 마지막 120자
    assert [len(c.text) for c in pieces] == [200, 200, 130]
    assert split_back(pieces) == f"제5조(긴 조항) {body}"


def test_header_on_its_own_line_stays_with_the_first_paragraph():
    # 공정위 표준약관(hwpx)은 "제20조(약관의 변경)" 줄 다음 줄에 ①이 온다.
    # 머리만 있는 조각을 만들지 않는다
    article = "제20조(약관의 변경)\n① " + syllables(0, 300) + "\n② " + syllables(400, 50)
    chunks = chunk_by_article(law(article, "제21조(기타) 사"), max_chars=200)
    pieces = [c for c in chunks if c.article == "제20조"]
    assert pieces[0].text.startswith("제20조(약관의 변경)\n① " + syllables(0, 10))
    assert all(c.text.strip() != "제20조(약관의 변경)" for c in pieces)
    assert all(len(c.text) <= 200 for c in pieces)
    assert all(c.text.startswith("제20조(약관의 변경)\n") for c in pieces)


def test_header_without_title_is_repeated_as_is():
    article = "제7조 ① " + "가" * 150 + "\n② " + "나" * 150
    chunks = chunk_by_article(law(article, "제8조(기타) 아"), max_chars=200)
    assert [c.text for c in chunks[:2]] == ["제7조 ① " + "가" * 150, "제7조\n② " + "나" * 150]


# ---- 나누지 않는 것 ----


def test_addenda_articles_are_not_split():
    # 부칙의 "다른 법률의 개정"은 길지만 나누지 않는다(잡음 청크가 늘어난다)
    document = law(
        "제1조(목적) 가\n부칙 <법률 제1호, 2020. 1. 1.>\n"
        "제1조(시행일) 이 법은 공포한 날부터 시행한다.\n"
        "제2조(다른 법률의 개정) ① " + "가" * 500 + "\n② " + "나" * 500
    )
    assert chunk_by_article(document, max_chars=200) == chunk_by_article(document)


def test_text_before_the_first_article_is_not_split():
    document = law("가" * 500 + "\n제1조(목적) 나\n제2조(정의) 다")
    assert chunk_by_article(document, max_chars=200) == chunk_by_article(document)


def test_fixed_length_documents_ignore_max_chars():
    product = Document(
        meta=meta.model_copy(update={"doc_id": "prod-1", "doc_type": "product"}),
        workspace_id="bank",
        pages=[Page(number=1, text=syllables(0, 2000))],
    )
    assert chunk_document(product, max_chars=200) == chunk_document(product)


# ---- 조각이 지키는 것 ----


def test_pieces_keep_page_and_document_fields_of_the_article():
    document = law(
        "제1조(목적) ① " + "가" * 150,
        "② " + "나" * 150 + "\n제2조(정의) 다",
        paged=True,
        access_level="internal",
    )
    chunks = chunk_by_article(document, max_chars=200)
    assert [(c.article, c.page) for c in chunks] == [("제1조", 1), ("제1조", 1), ("제2조", 2)]
    for c in chunks:
        assert (c.doc_id, c.workspace_id, c.title) == ("law-1", "bank", "가계대출약관")
        assert c.access_level == "internal"


def test_chunk_document_passes_max_chars_to_article_chunking():
    assert chunk_document(TWO_ARTICLES, max_chars=300) == chunk_by_article(
        TWO_ARTICLES, max_chars=300
    )


MIXED = law(
    "법 이름\n"
    + ARTICLE_11
    + "\n"
    + ARTICLE_3
    + "\n제20조(약관의 변경)\n① "
    + syllables(0, 700)
    + "\n제21조(짧은 조항) 짧다.\n제22조 ① "
    + " ".join(syllables(i * 3, 3) for i in range(200))
    + "\n부칙\n제1조(시행일) 공포한 날부터 시행한다."
)


@pytest.mark.parametrize("max_chars", [MIN_MAX_CHARS, 250, 300, 333, 500, 1000])
def test_every_piece_fits_and_no_text_is_lost(max_chars):
    whole = chunk_by_article(MIXED)
    split = chunk_by_article(MIXED, max_chars=max_chars)
    assert all(len(c.text) <= max_chars for c in split if c.article and c.article[0] == "제")
    for original, pieces in zip(whole, align(whole, split), strict=True):
        assert non_space(split_back(pieces)) == non_space(original.text)
        assert pieces[0].text.startswith(original.text[:10])
    assert [c.chunk_id for c in split] == [f"law-1::{i:04d}" for i in range(len(split))]


@pytest.mark.parametrize("bad", [0, -1, MIN_MAX_CHARS - 1])
def test_too_small_max_chars_is_rejected(bad):
    with pytest.raises(ValueError, match="max_chars"):
        chunk_by_article(TWO_ARTICLES, max_chars=bad)
    with pytest.raises(ValueError, match="max_chars"):
        chunk_document(TWO_ARTICLES, max_chars=bad)


# ---- 실제 코퍼스 (원문을 받아 둔 PC에서만) ----
# 원문 13개로 만든 945청크가 분할을 넣기 전(커밋 c065799)의 결과와 바이트까지 같은지 본다.
# 원문을 다시 받아 지문이 바뀌면 기준값을 다시 재야 하므로 건너뛴다.
CORPUS_SHA256 = "e2627ec66d7d"  # eval.run의 corpus_sha256 (docs/experiments/2026-11-11-reranker.md)
CHUNKS_BEFORE_SPLIT = 945
CHUNKS_SHA256 = "3b13fc8d36e11aa0134c9a825d7eab794419da329e63a0341ba4347c1e040372"


def corpus_digest(raw_dir: Path) -> str:
    digest = hashlib.sha256()
    for source in load_sources(SOURCES):
        file_hash = hashlib.sha256(source_file(source, raw_dir, REPO_ROOT).read_bytes())
        digest.update(f"{source.doc_id}:{file_hash.hexdigest()}\n".encode())
    return digest.hexdigest()[:12]


@pytest.fixture(scope="module")
def raw_dir() -> Path:
    data_dir = Settings.from_env().data_dir
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir
    raw = data_dir / "workspaces" / "bank" / "raw"
    if not (raw / MANIFEST_NAME).is_file():
        pytest.skip("원문이 없다: python scripts/download_corpus.py bank")
    if corpus_digest(raw) != CORPUS_SHA256:
        pytest.skip("원문이 기준값을 잰 때와 다르다")
    return raw


def test_real_corpus_without_max_chars_is_byte_identical(raw_dir):
    chunks = load_corpus_chunks(SOURCES, raw_dir, REPO_ROOT, "bank")
    assert len(chunks) == CHUNKS_BEFORE_SPLIT
    dumped = "\n".join(c.model_dump_json() for c in chunks)
    assert hashlib.sha256(dumped.encode()).hexdigest() == CHUNKS_SHA256


def test_real_corpus_split_keeps_labels_and_text(raw_dir):
    whole = load_corpus_chunks(SOURCES, raw_dir, REPO_ROOT, "bank")
    split = load_corpus_chunks(SOURCES, raw_dir, REPO_ROOT, "bank", max_chars=800)
    assert len(split) > len(whole)
    for original, pieces in zip(whole, align(whole, split), strict=True):
        assert non_space(split_back(pieces)) == non_space(original.text)
        if original.article is None or original.article.startswith("부칙"):
            assert [p.text for p in pieces] == [original.text]  # 머리말·부칙·고정 길이는 그대로
        else:
            assert all(len(p.text) <= 800 for p in pieces), original.chunk_id
        for p in pieces:
            assert (p.doc_id, p.page, p.access_level) == (
                original.doc_id,
                original.page,
                original.access_level,
            )
