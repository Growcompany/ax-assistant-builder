"""MCP 도구(Task 16). 도구 로직은 mcp 패키지 없이 부를 수 있는 함수라 프로토콜 없이 검사한다.

mcp 패키지가 있어야 하는 부분(MCPServer 조립)은 설치돼 있을 때만 돈다.
"""

import sys
from pathlib import Path

import pytest

import axbuilder.cli as cli
from axbuilder.api.app import create_service
from axbuilder.generation.llm import FakeLLM, LLMUnavailableError
from axbuilder.mcp_server import (
    Fixed,
    ToolError,
    ask_tool,
    build_server,
    check_fixed,
    list_workspaces_tool,
)
from axbuilder.models import LLMResponse
from axbuilder.retrieval.dense import HashEmbedder
from axbuilder.settings import Settings

sys.path.insert(0, str(Path(__file__).parent / "api"))
from test_app import (  # noqa: E402
    GUIDE_MD,
    GUIDE_META,
    LAW_TXT,
    META,
    WS,
    open_client,
    setup_indexed,
)

INTERNAL_MARK = "내부전용표식XYZ"


class CiteEverythingLLM:
    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        text = " ".join(f"내용 [{n}]." for n in range(1, 21))
        return LLMResponse(text=text, model="all", input_tokens=0, output_tokens=0, latency_ms=0.0)


class DownLLM:
    def generate(self, system: str, user: str, max_tokens: int = 2048) -> LLMResponse:
        raise LLMUnavailableError("Ollama 서버에 연결할 수 없습니다: ollama serve")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def prepared(settings) -> Settings:
    """화면(API)으로 만든 워크스페이스: bank(public 문서 + internal 문서, 색인됨), card(빈 것)."""
    with open_client(settings, FakeLLM("x")) as client:
        setup_indexed(client, ("law.txt", LAW_TXT, META), ("guide.md", GUIDE_MD, GUIDE_META))
        solo = {**WS, "id": "card", "name": "카드", "roles": {"직원": ["public"]}}
        assert client.post("/workspaces", json=solo).status_code == 201
    return settings


@pytest.fixture
def service(prepared):
    service = create_service(prepared, llm=CiteEverythingLLM(), embedder=HashEmbedder())
    yield service
    service.close()


# ---- list_workspaces ----


def test_list_workspaces(service):
    assert list_workspaces_tool(service) == [
        {
            "id": "bank",
            "name": WS["name"],
            "industry": WS["industry"],
            "roles": ["상담원", "준법감시", "견학생"],
            "documents": 2,
            "index": "ready",
        },
        {
            "id": "card",
            "name": "카드",
            "industry": WS["industry"],
            "roles": ["직원"],
            "documents": 0,
            "index": "none",
        },
    ]


def test_list_workspaces_when_fixed_shows_only_that_one(service):
    listed = list_workspaces_tool(service, Fixed("bank", "상담원"))
    assert [w["id"] for w in listed] == ["bank"]
    assert listed[0]["roles"] == ["상담원"]  # 고정된 역할만


# ---- ask ----


def test_ask_returns_answer_citations_and_passages(service):
    result = ask_tool(service, "보호 한도?", workspace="bank", role="상담원")
    assert set(result) == {"answer", "citations", "passages", "refused", "model", "latency_ms"}
    assert result["refused"] is False and result["model"] == "all"
    assert result["citations"] and len(result["passages"]) == len(result["citations"])
    assert set(result["citations"][0]) == {"chunk_id", "doc_id", "title", "page", "article"}
    assert all(p["chunk_id"].startswith("law-1::") for p in result["passages"])


def test_ask_applies_the_role_filter(service):
    question = "상담원 내부 절차 확인"
    public = ask_tool(service, question, workspace="bank", role="상담원")
    assert INTERNAL_MARK not in str(public)
    internal = ask_tool(service, question, workspace="bank", role="준법감시")
    assert INTERNAL_MARK in str(internal)
    refused = ask_tool(service, question, workspace="bank", role="견학생")
    assert refused["refused"] is True and refused["passages"] == []


def test_ask_role_may_be_omitted_only_when_there_is_one(service):
    # 역할이 하나뿐이면 그 역할로 간다: 역할 오류가 아니라 그다음 단계(색인 없음)에서 멈춘다
    with pytest.raises(ToolError, match="색인"):
        ask_tool(service, "질문", workspace="card")
    with pytest.raises(ToolError, match="상담원"):
        ask_tool(
            service, "질문", workspace="bank"
        )  # 여럿이면 고르게 한다(넓은 쪽으로 정하지 않는다)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"workspace": "nope", "role": "상담원"}, "워크스페이스"),
        ({"workspace": "../etc", "role": "상담원"}, "워크스페이스"),
        ({"workspace": None, "role": "상담원"}, "워크스페이스"),
        ({"workspace": "bank", "role": "없는역할"}, "역할"),
        ({"workspace": "bank", "role": ""}, "역할"),
    ],
)
def test_ask_unknown_workspace_or_role(service, kwargs, message):
    with pytest.raises(ToolError, match=message):
        ask_tool(service, "질문", **kwargs)


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001, None, 5])
def test_ask_bad_question(service, question):
    with pytest.raises(ToolError, match="질문"):
        ask_tool(service, question, workspace="bank", role="상담원")


def test_ask_before_index_and_llm_down_are_readable_errors(prepared):
    service = create_service(prepared, llm=DownLLM(), embedder=HashEmbedder())
    try:
        with pytest.raises(ToolError, match="색인"):
            ask_tool(service, "질문", workspace="card", role="직원")
        with pytest.raises(ToolError, match="ollama serve"):
            ask_tool(service, "보호 한도?", workspace="bank", role="상담원")
    finally:
        service.close()


# ---- --workspace/--role 로 고정 ----


def test_fixed_ignores_workspace_and_role_arguments(service):
    fixed = Fixed("bank", "상담원")
    question = "상담원 내부 절차 확인"
    result = ask_tool(service, question, workspace="card", role="준법감시", fixed=fixed)
    assert INTERNAL_MARK not in str(result) and result["passages"]
    assert ask_tool(service, question, fixed=fixed)["passages"]


def test_check_fixed(service):
    assert check_fixed(service, None, None) is None
    assert check_fixed(service, "bank", "상담원") == Fixed("bank", "상담원")
    for workspace, role in [("bank", None), (None, "상담원"), ("nope", "상담원"), ("bank", "x")]:
        with pytest.raises(ToolError):
            check_fixed(service, workspace, role)


# ---- axb mcp ----


def test_axb_mcp_without_the_package_tells_how_to_install(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "mcp_installed", lambda: False)
    assert cli.main(["mcp", "--data-dir", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert 'pip install -e ".[mcp]"' in captured.err
    assert len(captured.err.strip().splitlines()) == 1
    assert captured.out == ""  # stdout은 MCP 프로토콜 전용이다


def test_axb_mcp_options():
    args = cli.build_parser().parse_args(["mcp"])
    assert args.data_dir is None and args.workspace is None and args.role is None
    args = cli.build_parser().parse_args(["mcp", "--workspace", "bank", "--role", "상담원"])
    assert (args.workspace, args.role) == ("bank", "상담원")


def test_axb_mcp_runs_the_server_with_the_options(monkeypatch, capsys, prepared):
    import axbuilder.mcp_server as mcp_server

    calls = []
    monkeypatch.setattr(cli, "mcp_installed", lambda: True)
    monkeypatch.setattr(
        mcp_server,
        "run",
        lambda settings, workspace, role: calls.append((settings, workspace, role)) or 0,
    )
    monkeypatch.delenv("AXB_DATA_DIR", raising=False)
    argv = ["mcp", "--data-dir", str(prepared.data_dir), "--workspace", "bank", "--role", "상담원"]
    assert cli.main(argv) == 0
    ((settings, workspace, role),) = calls
    assert settings.data_dir == prepared.data_dir and (workspace, role) == ("bank", "상담원")
    assert capsys.readouterr().out == ""


def test_run_rejects_bad_fixed_options_before_starting(monkeypatch, capsys, prepared):
    import axbuilder.mcp_server as mcp_server

    started = []
    monkeypatch.setattr(mcp_server, "build_server", lambda *a, **k: started.append(a))
    monkeypatch.setattr(
        mcp_server,
        "create_service",
        lambda settings: create_service(settings, llm=FakeLLM("x"), embedder=HashEmbedder()),
    )
    assert mcp_server.run(prepared, "bank", None) == 2
    assert mcp_server.run(prepared, "nope", "상담원") == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "--workspace" in captured.err
    assert started == []


def test_build_server_registers_the_tools(service):
    pytest.importorskip("mcp")  # 선택 설치([mcp])가 있을 때만
    assert build_server(service) is not None
    assert build_server(service, Fixed("bank", "상담원")) is not None


def test_tool_errors_reach_the_client_with_their_message(service):
    pytest.importorskip("mcp")
    import asyncio

    from mcp.server.mcpserver.exceptions import ToolError as McpToolError

    server = build_server(service)
    with pytest.raises(McpToolError) as error:
        asyncio.run(
            server.call_tool("ask", {"workspace": "bank", "question": "x", "role": "없는역할"})
        )
    assert "역할" in str(error.value)
    assert "Traceback" not in str(error.value)
