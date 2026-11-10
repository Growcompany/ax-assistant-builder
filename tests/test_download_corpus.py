"""코퍼스 출처 목록과 다운로드 (네트워크 없음: httpx.MockTransport로 응답을 흉내 낸다)."""

import hashlib
import importlib.util
import io
import json
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from axbuilder.corpus import (
    MANIFEST_NAME,
    CorpusSource,
    download_sources,
    load_sources,
    source_file,
)
from axbuilder.ingest.chunking import chunk_document
from axbuilder.ingest.law_html import law_html_to_text
from axbuilder.ingest.loaders import load_document
from axbuilder.models import Document, Page

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_HTML = (REPO_ROOT / "tests" / "fixtures" / "law_page.html").read_text(encoding="utf-8")
BANK_DOC_IDS = [
    "banking-act",
    "financial-consumer-protection-act",
    "electronic-financial-transactions-act",
    "depositor-protection-act",
    "efs-supervision-regulation",
    "ftc-loan-terms-household",
    "ftc-deposit-terms",
    "prod-fixed-deposit",
    "prod-free-savings",
    "prod-mortgage-loan",
    "guide-counseling",
    "guide-complaints",
    "guide-elderly-customers",
]


def src(**overrides) -> CorpusSource:
    fields = {
        "doc_id": "banking-act",
        "title": "은행법",
        "source": "국가법령정보센터(법제처)",
        "doc_type": "law",
        "url": "https://law.example/LSW/lsInfoR.do?lsiSeq=1",
        "format": "html",
        "license": "저작권법 제7조 비보호 저작물",
    }
    return CorpusSource(**{**fields, **overrides})


# ---- 법령 HTML → 조문 줄 텍스트 ----


def test_law_html_to_text_keeps_article_lines():
    lines = law_html_to_text(FIXTURE_HTML).split("\n")
    assert lines == [
        "은행법",
        "[시행 2026. 7. 1.] [법률 제21296호, 2025. 12. 30., 일부개정]",
        "제1장 총칙 <개정 2010. 5. 17.>",
        "제1조(목적) 이 법은 은행의 건전한 운영을 도모하고 예금자를 보호함을 목적으로 한다.",
        "[전문개정 2010. 5. 17.]",
        "제2조(정의) ① 이 법에서 사용하는 용어의 뜻은 다음과 같다.",
        "1. “은행업”이란 예금을 받아 조달한 자금을 대출하는 것을 업(業)으로 하는 것을 말한다.",
        "8. “동일인”이란 본인 및 그와 대통령령으로 정하는 특수관계에 있는 자를 말한다.",
        "② 자기자본의 구체적 범위는 제16조의2에 따른다.",
        "제2조의2(금리인하 요구) 은행과 신용공여 계약을 체결한 자는 은행에 금리인하를 "
        "요구할 수 있다.",
        "부칙 <법률 제5540호, 1998. 5. 25.>",
        "이 법은 공포한 날부터 시행한다.",
        "부칙 <법률 제21296호, 2025. 12. 30.>",
        "제1조(시행일) 이 법은 공포 후 6개월이 경과한 날부터 시행한다.",
        "제2조 (대출금리 산정에 관한 적용례) 제30조의3의 개정규정은 이 법 시행 이후 대출 계약을 "
        "체결하는 경우부터 적용한다.",
    ]


def test_law_html_without_title_keeps_all_text():
    assert law_html_to_text("<p>제1조(목적) 가</p><p>제2조(정의) 나</p>") == (
        "제1조(목적) 가\n제2조(정의) 나"
    )


def test_converted_law_text_splits_into_articles_and_addenda():
    meta = src().doc_meta()
    doc = Document(
        meta=meta, workspace_id="bank", pages=[Page(number=1, text=law_html_to_text(FIXTURE_HTML))]
    )
    chunks = chunk_document(doc)
    assert [c.article for c in chunks] == [
        None,
        "제1조",
        "제2조",
        "제2조의2",
        "부칙",
        "부칙 제1조",
        "부칙 제2조",
    ]
    assert chunks[0].text.startswith("은행법\n[시행 2026. 7. 1.]")


# ---- sources.yaml ----


def test_repo_sources_yaml_is_valid():
    sources = load_sources(REPO_ROOT / "corpus" / "bank" / "sources.yaml")
    assert [s.doc_id for s in sources] == BANK_DOC_IDS  # 13개, 계획에 고정한 순서
    for s in sources:
        assert (s.url is None) != (s.path is None)
        if s.path is not None:
            path = source_file(s, raw_dir=Path("unused"), repo_root=REPO_ROOT)
            assert path.is_file()
            first_line = path.read_text(encoding="utf-8").split("\n", 1)[0]
            assert first_line == "본 문서는 포트폴리오용 가상 문서입니다"
    internal = {s.doc_id for s in sources if s.access_level == "internal"}
    assert internal == {"guide-counseling", "guide-complaints", "guide-elderly-customers"}


def test_repo_internal_guides_chunk_with_preamble_and_addenda():
    for s in load_sources(REPO_ROOT / "corpus" / "bank" / "sources.yaml"):
        if s.doc_type != "internal":
            continue
        path = source_file(s, raw_dir=Path("unused"), repo_root=REPO_ROOT)
        chunks = chunk_document(load_document(path, s.doc_meta(), "bank"))
        assert chunks[0].article is None
        assert chunks[0].text.startswith("본 문서는 포트폴리오용 가상 문서입니다")
        assert "대외비" in chunks[0].text
        articles = [c.article for c in chunks[1:]]
        assert len(articles) == len(set(articles)), (
            s.doc_id
        )  # 본문 조항과 부칙 조항이 겹치지 않는다
        assert "부칙 제1조" in articles
        assert all(c.access_level == "internal" for c in chunks)


def test_source_rejects_unknown_field():
    with pytest.raises(ValueError):
        src(licence="오타 필드")


@pytest.mark.parametrize(
    "where",
    [
        {"url": None, "path": None},
        {"url": "https://law.example/a", "path": "corpus/bank/a.md", "format": "md"},
    ],
)
def test_source_needs_exactly_one_of_url_or_path(where):
    with pytest.raises(ValueError):
        src(**where)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../secret.md",
        "/etc/passwd.md",
        "C:/Windows/x.md",
        "corpus\\bank\\x.md",
        "corpus/../../x.md",
    ],
)
def test_source_path_must_stay_inside_repo(bad_path):
    with pytest.raises(ValueError):
        src(url=None, path=bad_path, format="md")


def test_source_path_suffix_must_match_format():
    with pytest.raises(ValueError):
        src(url=None, path="corpus/bank/a.pdf", format="md")


@pytest.mark.parametrize("bad_url", ["http://law.example/a", "file:///etc/passwd", "ftp://x/y"])
def test_source_url_must_be_https(bad_url):
    with pytest.raises(ValueError):
        src(url=bad_url)


@pytest.mark.parametrize("bad_id", ["../x", "Banking-Act", "a/b", "-x", ""])
def test_source_doc_id_is_safe_file_name(bad_id):
    with pytest.raises(ValueError):
        src(doc_id=bad_id)


def test_load_sources_rejects_duplicate_doc_id(tmp_path):
    entry = src().model_dump(mode="json")
    path = tmp_path / "sources.yaml"
    path.write_text(json.dumps([entry, entry], ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="banking-act"):
        load_sources(path)


def test_load_sources_needs_top_level_list(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text("documents: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_sources(path)


# ---- 다운로드 ----


def hwpx_bytes() -> bytes:
    """부를 때마다 같은 바이트. 항목 시각을 고정한다(이름만 주면 writestr가 지금 시각을 찍는다)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, content in [
            ("mimetype", "application/hwp+zip"),
            ("Contents/section0.xml", "<sec/>"),
        ]:
            zf.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), content)
    return out.getvalue()


def test_hwpx_bytes_helper_does_not_depend_on_the_clock(monkeypatch):
    # 드물게 실패하던 원인: writestr는 ZIP 항목에 지금 시각(2초 단위)을 찍는다. 응답을 만들 때와
    # 저장된 파일과 비교할 때 사이에 2초 경계를 넘으면 바이트가 달라졌다
    clock = iter(range(1_800_000_000, 1_900_000_000, 2))
    monkeypatch.setattr(time, "time", lambda: next(clock))
    assert hwpx_bytes() == hwpx_bytes()


def mock_client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler))


def bank_sources() -> list[CorpusSource]:
    return [
        src(),
        src(
            doc_id="ftc-deposit-terms",
            doc_type="terms",
            url="https://ftc.example/download?no=1",
            format="hwpx",
        ),
        src(doc_id="broken-law", url="https://law.example/broken"),
        src(
            doc_id="guide-counseling",
            doc_type="internal",
            access_level="internal",
            url=None,
            path="corpus/bank/internal/guide-counseling.md",
            format="md",
        ),
    ]


def test_download_saves_files_and_manifest_and_continues_after_failure(tmp_path):
    raw = tmp_path / "raw"
    client = mock_client(
        {
            "https://law.example/LSW/lsInfoR.do?lsiSeq=1": httpx.Response(200, text=FIXTURE_HTML),
            "https://ftc.example/download?no=1": httpx.Response(200, content=hwpx_bytes()),
            "https://law.example/broken": httpx.Response(500),
        }
    )
    result = download_sources(bank_sources(), raw, client, repo_root=REPO_ROOT)

    assert [e.doc_id for e in result.downloaded] == ["banking-act", "ftc-deposit-terms"]
    assert list(result.failed) == ["broken-law"] and "500" in result.failed["broken-law"]
    assert result.in_repo == ["guide-counseling"]

    law_text = (raw / "banking-act.txt").read_text(encoding="utf-8")
    assert law_text == law_html_to_text(FIXTURE_HTML)
    assert (raw / "ftc-deposit-terms.hwpx").read_bytes() == hwpx_bytes()
    assert not (raw / "broken-law.txt").exists()
    assert sorted(p.name for p in raw.iterdir()) == [
        "banking-act.txt",
        "ftc-deposit-terms.hwpx",
        MANIFEST_NAME,
    ]

    manifest = json.loads((raw / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert [m["doc_id"] for m in manifest] == ["banking-act", "ftc-deposit-terms"]
    law_entry = manifest[0]
    assert law_entry["url"] == "https://law.example/LSW/lsInfoR.do?lsiSeq=1"
    assert law_entry["format"] == "html" and law_entry["file"] == "banking-act.txt"
    assert law_entry["sha256"] == hashlib.sha256((raw / "banking-act.txt").read_bytes()).hexdigest()
    assert law_entry["retrieved_at"].endswith("Z") or "+00:00" in law_entry["retrieved_at"]


def test_saved_law_text_loads_as_article_chunks(tmp_path):
    raw = tmp_path / "raw"
    client = mock_client(
        {"https://law.example/LSW/lsInfoR.do?lsiSeq=1": httpx.Response(200, text=FIXTURE_HTML)}
    )
    source = src()
    download_sources([source], raw, client, repo_root=REPO_ROOT)
    path = source_file(source, raw, REPO_ROOT)
    chunks = chunk_document(load_document(path, source.doc_meta(), "bank"))
    assert "제2조의2" in [c.article for c in chunks]


def test_html_without_articles_is_a_failure(tmp_path):
    # law.go.kr은 없는 lsiSeq에도 200과 안내 페이지를 돌려줄 수 있다 → 조문이 없으면 실패로 본다
    raw = tmp_path / "raw"
    client = mock_client(
        {
            "https://law.example/LSW/lsInfoR.do?lsiSeq=1": httpx.Response(
                200, text="<html><body><h2>요청하신 페이지를 찾을 수 없습니다</h2></body></html>"
            )
        }
    )
    result = download_sources([src()], raw, client, repo_root=REPO_ROOT)
    assert list(result.failed) == ["banking-act"]
    assert not (raw / "banking-act.txt").exists()


def test_hwpx_that_is_not_a_zip_is_a_failure(tmp_path):
    source = src(doc_id="ftc-deposit-terms", url="https://ftc.example/x", format="hwpx")
    client = mock_client({"https://ftc.example/x": httpx.Response(200, text="<html>로그인</html>")})
    result = download_sources([source], tmp_path / "raw", client, repo_root=REPO_ROOT)
    assert list(result.failed) == ["ftc-deposit-terms"]


def test_missing_repo_file_is_a_failure(tmp_path):
    source = src(doc_id="guide-x", url=None, path="corpus/bank/internal/missing.md", format="md")
    result = download_sources([source], tmp_path / "raw", mock_client({}), repo_root=REPO_ROOT)
    assert list(result.failed) == ["guide-x"]


def test_failed_retry_keeps_previous_file_and_manifest_entry(tmp_path):
    raw = tmp_path / "raw"
    url = "https://law.example/LSW/lsInfoR.do?lsiSeq=1"
    download_sources(
        [src()], raw, mock_client({url: httpx.Response(200, text=FIXTURE_HTML)}), REPO_ROOT
    )
    before = (raw / MANIFEST_NAME).read_text(encoding="utf-8")

    result = download_sources([src()], raw, mock_client({url: httpx.Response(503)}), REPO_ROOT)
    assert list(result.failed) == ["banking-act"]
    assert (raw / "banking-act.txt").exists()  # 받아 둔 파일을 지우지 않는다
    assert (raw / MANIFEST_NAME).read_text(encoding="utf-8") == before


def test_source_file_for_url_and_path_sources(tmp_path):
    raw = tmp_path / "raw"
    assert source_file(src(), raw, REPO_ROOT) == raw / "banking-act.txt"
    assert source_file(src(format="hwpx"), raw, REPO_ROOT) == raw / "banking-act.hwpx"
    guide = src(url=None, path="corpus/bank/internal/guide-counseling.md", format="md")
    assert (
        source_file(guide, raw, REPO_ROOT) == REPO_ROOT / "corpus/bank/internal/guide-counseling.md"
    )


# ---- 명령줄 (scripts/download_corpus.py) ----


def load_cli():
    # scripts/는 패키지가 아니라 파일 경로로 import한다
    spec = importlib.util.spec_from_file_location(
        "download_corpus", REPO_ROOT / "scripts" / "download_corpus.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bank_routes(*, failing: tuple[str, ...] = ()) -> dict[str, httpx.Response]:
    """저장소의 bank sources.yaml url 항목마다 형식에 맞는 응답. failing에 든 doc_id는 500."""
    routes = {}
    for s in load_sources(REPO_ROOT / "corpus" / "bank" / "sources.yaml"):
        if s.url is None:
            continue
        if s.doc_id in failing:
            routes[s.url] = httpx.Response(500)
        elif s.format == "hwpx":
            routes[s.url] = httpx.Response(200, content=hwpx_bytes())
        else:
            routes[s.url] = httpx.Response(200, text=FIXTURE_HTML)
    return routes


def test_cli_prints_failures_and_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AXB_DATA_DIR", str(tmp_path))
    failing = ("banking-act", "ftc-deposit-terms")
    code = load_cli().main(["bank"], client=mock_client(bank_routes(failing=failing)))

    out, err = capsys.readouterr()
    assert code == 1
    fail_lines = [line for line in out.splitlines() if line.startswith("[FAIL]")]
    assert sorted(line.split()[1] for line in fail_lines) == sorted(failing)
    assert all("500" in line for line in fail_lines)
    assert "13 sources: 5 downloaded, 6 in repo, 2 failed" in out
    assert err.strip() == "failed: banking-act, ftc-deposit-terms"
    raw = tmp_path / "workspaces" / "bank" / "raw"
    assert (raw / "depositor-protection-act.txt").is_file()  # 실패 뒤에도 계속 받는다
    assert not (raw / "banking-act.txt").exists()


def test_cli_exits_0_when_every_source_is_available(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AXB_DATA_DIR", str(tmp_path))
    code = load_cli().main(["bank"], client=mock_client(bank_routes()))

    out, err = capsys.readouterr()
    assert code == 0 and err == ""
    assert "[FAIL]" not in out
    assert "13 sources: 7 downloaded, 6 in repo, 0 failed" in out
