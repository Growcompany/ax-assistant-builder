"""긴 조항 나누기 (M-2 Task 25): chunk_by_article(doc, max_chars).

max_chars가 None이면 지금과 바이트까지 같다. 값이 있으면 그보다 긴 본문 조항을 항(①) → 호(1.) →
목(가.) → 줄 → 글자 수 순서로 나누고, 이웃 조각을 max_chars 안에서 욕심내어 묶는다. 둘째 조각부터
조항 머리("제11조(대리인 거래)")를 붙인다. article·page는 원래 조항 그대로다. 부칙은 나누지 않는다.
"""

import hashlib
import random
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from axbuilder.corpus import MANIFEST_NAME, load_sources, source_file
from axbuilder.eval.run import load_corpus_chunks
from axbuilder.ingest.chunking import (
    ADDENDA,
    ARTICLE_HEAD,
    MIN_MAX_CHARS,
    chunk_by_article,
    chunk_document,
)
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


def piece_prefix(original: Chunk) -> str:
    """둘째 조각부터 붙는 머리 줄. 머리를 알아볼 수 없는 조항(ARTICLE_HEAD가 맞지 않음)은 머리 없이
    나뉜다."""
    found = ARTICLE_HEAD.match(original.text)
    return found.group() + "\n" if found else ""


def restored(original: Chunk, pieces: list[Chunk]) -> str:
    """조각들을 다시 이은 본문. 둘째 조각부터는 원래 조항의 머리 줄로 시작해야 하고, 그것을 뗀다."""
    prefix = piece_prefix(original)
    first, *rest = pieces
    for piece in rest:
        assert piece.text.startswith(prefix), (original.chunk_id, piece.text[:40])
    return first.text + "".join(piece.text[len(prefix) :] for piece in rest)


def align(whole: list[Chunk], split: list[Chunk]) -> list[list[Chunk]]:
    """나누기 전 청크마다 그 청크에서 나온 조각들 (조각은 원래 자리에 차례로 나온다)."""
    groups: list[list[Chunk]] = []
    pieces = iter(split)
    for original in whole:
        target = len(non_space(original.text))
        prefix = len(non_space(piece_prefix(original)))
        group = [next(pieces)]
        found = len(non_space(group[0].text))
        while found < target:
            group.append(next(pieces))
            found += len(non_space(group[-1].text)) - prefix
        assert {piece.article for piece in group} == {original.article}, original.chunk_id
        groups.append(group)
    assert next(pieces, None) is None
    return groups


def finishes(work: Callable[[], Any], seconds: float = 20.0) -> Any:
    """work()를 스레드에서 돌려 seconds 안에 끝나는지 본다(Windows에는 signal.alarm이 없다).

    끝나지 않으면 실패한다. 그 스레드는 daemon이라 테스트 프로세스가 끝날 때 함께 끝난다.
    """
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = work()
        except BaseException as e:  # 스레드 안의 예외를 테스트로 넘긴다
            outcome["error"] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"did not finish in {seconds}s (endless loop?)"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def check_split(document: Document, max_chars: int) -> list[Chunk]:
    """나누기의 성질: 끝난다, 본문 조각은 모두 max_chars 이하, 공백 말고는 글자가 빠지거나 더해지지
    않는다, 라벨·위치는 원래 조항 그대로, 나누지 않는 청크(머리말·부칙·짧은 조항)는 그대로다."""
    whole = chunk_by_article(document)
    split = chunk_by_article(document, max_chars=max_chars)
    for original, pieces in zip(whole, align(whole, split), strict=True):
        assert non_space(restored(original, pieces)) == non_space(original.text)
        if original.article is None or original.article.startswith(ADDENDA):
            assert [p.text for p in pieces] == [original.text]
        else:
            assert all(len(p.text) <= max_chars for p in pieces), original.text[:40]
            if len(original.text) <= max_chars:
                assert [p.text for p in pieces] == [original.text]
        for p in pieces:
            assert (p.page, p.access_level) == (original.page, original.access_level)
    return split


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


# ---- 어떤 입력에도 끝난다 (PR #24 리뷰 major: 덩어리 한도가 0 이하이면 끝나지 않았다) ----
# 머리 뒤 공백이나 머리 자체가 길어 "max_chars - 머리 - 구분 공백"이 0 이하가 되면 글자 수로 자르는
# 반복이 앞으로 나가지 못했다. 업로드 검사(_check_parses)가 파싱 자리를 쥔 채 멈추는 경로였다.

EDGE_CASES = {
    # 리뷰에서 재현한 세 입력 (192칸은 예전에도 끝났다: 한도 1)
    "192 spaces after the head": (200, "제1조(목적)" + " " * 192 + "가" * 400),
    "193 spaces after the head": (200, "제1조(목적)" + " " * 193 + "가" * 400),
    "900 newlines after the head": (800, "제1조(목적)" + "\n" * 900 + "가" * 400),
    "250 spaces before the title": (200, "제1조" + " " * 250 + "(목적) " + "가" * 400),
    # 공백·줄바꿈·제어 문자로만 된 긴 본문
    "whitespace-only body": (
        200,
        "제1조(목적)" + " \t\n\r\u3000\u00a0\u2009\x1c\x0b\x0c" * 300 + "끝",
    ),
    "control characters": (200, "제1조(목적) " + "\x00\x01\x7f" * 300),
    "blank lines between paragraphs": (200, "제1조(목적)\n" + ("① 가\n" + "\n" * 300) * 5),
    # 머리가 아주 길 때
    "500-char title": (200, "제1조(" + "가" * 500 + ") " + "나" * 400),
    "80-char title at the lower bound": (
        200,
        "제1조(" + "가" * 80 + ") ① " + "나" * 300 + "\n② " + "다" * 300,
    ),
    "300-digit article number": (200, "제" + "1" * 300 + "조(목적) " + "가" * 400),
    "300-digit branch number": (200, "제1조의" + "2" * 300 + "(목적) " + "가" * 400),
    # 경계가 없는 긴 본문 (글자 수로만 자른다)
    "one long word": (200, "제1조(목적) " + "가" * 5000),
    "long article of short words": (800, "제1조(목적) " + ("가" * 199 + " ") * 2000),
}


@pytest.mark.parametrize(("max_chars", "article"), EDGE_CASES.values(), ids=EDGE_CASES.keys())
def test_split_finishes_and_keeps_its_promises_on_edge_inputs(max_chars, article):
    document = law(article + "\n제99조(끝) 끝")
    split = finishes(lambda: check_split(document, max_chars))
    assert split[-1].text == "제99조(끝) 끝"


def test_long_whitespace_after_the_head_becomes_one_separator():
    # 첫 조각은 머리 + 구분 문자 하나 + 본문이다. 원문의 공백이 여러 칸이어도 한 칸으로 줄인다
    spaces = law("제1조(목적)" + " " * 193 + "가" * 400 + "\n제2조(정의) 나")
    chunks = finishes(lambda: chunk_by_article(spaces, 200))
    assert chunks[0].text == "제1조(목적) " + "가" * 192
    assert chunks[1].text == "제1조(목적)\n" + "가" * 192
    newlines = law("제1조(목적)" + "\n" * 900 + "가" * 400 + "\n제2조(정의) 나")
    assert finishes(lambda: chunk_by_article(newlines, 800))[0].text == "제1조(목적)\n" + "가" * 400


def test_article_without_a_recognisable_head_is_split_without_one():
    # 조 번호가 너무 길면 머리로 보지 않는다(정상 조항이 아니다). 붙일 머리 없이 나눈다
    number = "1" * 300
    document = law(f"제{number}조(목적) " + "가" * 400 + "\n제2조(정의) 나")
    chunks = finishes(lambda: chunk_by_article(document, 200))
    pieces = [c for c in chunks if c.article == f"제{number}조"]
    assert len(pieces) == 4 and all(len(c.text) <= 200 for c in pieces)
    assert not pieces[1].text.startswith("제")


# 무작위 입력: 실제 지침 문서의 낱말 사이에 공백·줄바꿈·항·호·목 기호, 긴 머리와 긴 공백을 아무
# 데나 넣는다. 시드를 고정해 같은 입력으로 다시 돌릴 수 있다
WORDS = (
    (REPO_ROOT / "corpus" / "bank" / "internal" / "guide-counseling.md")
    .read_text(encoding="utf-8")
    .split()
)
MARKS = ["\n① ", "\n② ", "①", "\n㉑", "\n㊿ ", "\n1. ", "\n2의2. ", "1. ", "\n가. ", "가. ", "\n"]
SPACES = [" ", "\t", "\n", "\r", "\u3000", "\u00a0", "\u2009", "\x1c", "\x0b"]
ODD = ["\x00", "\x7f", "(", ")", "（", "）", "【", "】", "제3조", "부칙", "<개정 2020. 1. 1.>"]
TITLES = ["", "(목적)", "(대리인 거래)", "（정의）", "【목적】", "(금융 (특례) 보호)"]


def random_document(rng: random.Random) -> Document:
    parts = [rng.choice(WORDS)] if rng.random() < 0.3 else []  # 첫 조항 앞 머리말
    for _ in range(rng.randint(1, 4)):
        number = str(rng.randint(1, 99)) if rng.random() < 0.9 else "1" * rng.randint(5, 120)
        spaces = " " * rng.choice([0, 0, 0, 1, 2, 3, 4, 40, 250])
        title = rng.choice([*TITLES, "(" + "가" * rng.randint(1, 120) + ")"])
        body: list[str] = []
        for _ in range(rng.randint(1, 30)):
            roll = rng.random()
            if roll < 0.4:
                body.append(rng.choice(WORDS))
            elif roll < 0.6:
                body.append(rng.choice(MARKS))
            elif roll < 0.85:
                body.append(rng.choice(SPACES) * rng.choice([1, 1, 2, 7, 60, 400]))
            elif roll < 0.95:
                body.append("가" * rng.randint(1, 900))
            else:
                body.append(rng.choice(ODD))
        gap = rng.choice(["", " ", "\n", "  ", "\n\n", " " * 300])
        parts.append(f"제{number}조{spaces}{title}{gap}" + "".join(body))
        if rng.random() < 0.15:
            parts.append("부칙")  # 그 뒤 조항은 부칙 조항이라 나누지 않는다
    return law("\n".join(parts))


def test_random_inputs_finish_fit_and_lose_no_text():
    rng = random.Random(20261112)

    def run_all() -> int:
        split_articles = 0
        for _ in range(2000):
            document = random_document(rng)
            max_chars = rng.choice([MIN_MAX_CHARS, 201, 230, 333, 500, 800])
            split = check_split(document, max_chars)
            split_articles += len(split) - len(chunk_by_article(document))
        return split_articles

    added = finishes(run_all, seconds=120)
    assert added > 1000  # 실제로 많이 나뉘었다(성질이 빈 입력으로만 지켜진 것이 아니다)


# ---- 실제 코퍼스 (원문을 받아 둔 PC에서만) ----
# 원문 13개로 만든 945청크가 분할을 넣기 전(커밋 c065799)의 결과와 바이트까지 같은지 본다.
# 원문을 다시 받아 지문이 바뀌면 기준값을 다시 재야 하므로 건너뛴다.
CORPUS_SHA256 = "e2627ec66d7d"  # eval.run의 corpus_sha256 (docs/experiments/2026-11-11-reranker.md)
CHUNKS_BEFORE_SPLIT = 945
CHUNKS_SHA256 = "3b13fc8d36e11aa0134c9a825d7eab794419da329e63a0341ba4347c1e040372"
# 800자로 나눈 1,079청크 (커밋 628e405, 실험 기록 2026-11-12-article-split.md의 조건). 나누는 규칙을
# 고쳐도 이 결과가 바뀌면 실험 기록의 수치가 낡으므로 지문으로 고정한다
SPLIT_800_CHUNKS = 1079
SPLIT_800_SHA256 = "2b9626610b30311d17eeab052dc276d3da1a2523ac2d9cc1470c9efb375ef594"


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
    assert len(split) == SPLIT_800_CHUNKS
    dumped = "\n".join(c.model_dump_json() for c in split)
    assert hashlib.sha256(dumped.encode()).hexdigest() == SPLIT_800_SHA256
    for original, pieces in zip(whole, align(whole, split), strict=True):
        assert non_space(restored(original, pieces)) == non_space(original.text)
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
