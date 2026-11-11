"""MCP 서버(stdio): Claude Desktop 같은 MCP 클라이언트에서 워크스페이스에 질문한다.

  axb mcp [--data-dir PATH] [--workspace ID --role ROLE]

HTTP 서버를 거치지 않고 같은 서비스 계층(axbuilder.api.app의 서비스)을 직접 부른다. 이 명령을
실행하는 사람은 그 PC의 데이터 주인이므로 API 키를 쓰지 않는다. --workspace와 --role을 주면
그것으로 고정하고, 도구는 워크스페이스·역할 인자를 받지 않는다(모델이 역할을 고르지 못하게).

도구 로직(list_workspaces_tool, ask_tool)은 mcp 패키지 없이 부를 수 있는 함수다. mcp 패키지는
build_server()에서만 import한다(선택 설치: pip install -e ".[mcp]").

stdio 서버는 stdout을 프로토콜에만 쓴다. 로그와 안내는 stderr로 보낸다.

한계: Qdrant 로컬 색인은 한 프로세스만 열 수 있다. axb serve가 같은 워크스페이스의 색인을 열고
있으면(질문을 한 번이라도 받았으면) 여기서는 열지 못한다.
"""

import logging
import sys
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from axbuilder.api.app import IndexNotReadyError, create_service, reranker_unavailable_detail
from axbuilder.generation.llm import LLMUnavailableError
from axbuilder.retrieval.rerank import RerankerUnavailableError
from axbuilder.settings import Settings
from axbuilder.workspace import WorkspaceNotFoundError, is_valid_workspace_id

logger = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2000  # API의 질문 상한과 같다


class ToolError(ValueError):
    """도구를 부른 쪽에 그대로 보여 줄 오류 문장."""


@dataclass(frozen=True)
class Fixed:
    """--workspace/--role 로 고정한 대상."""

    workspace: str
    role: str


def _workspace(service: Any, workspace_id: Any):
    if not isinstance(workspace_id, str) or not is_valid_workspace_id(workspace_id):
        raise ToolError("그런 워크스페이스가 없습니다. list_workspaces로 id를 확인하세요")
    try:
        return service.store.get(workspace_id)
    except WorkspaceNotFoundError:
        raise ToolError("그런 워크스페이스가 없습니다. list_workspaces로 id를 확인하세요") from None


def check_fixed(service: Any, workspace: str | None, role: str | None) -> Fixed | None:
    """--workspace/--role 값을 확인한다. 둘 다 없으면 None(고정하지 않음)."""
    if workspace is None and role is None:
        return None
    if workspace is None or role is None:
        raise ToolError("--workspace 와 --role 은 함께 줘야 합니다")
    ws = _workspace(service, workspace)
    if role not in ws.roles:
        raise ToolError(
            f"--workspace {workspace} 에 --role 로 준 역할이 없습니다. "
            f"가능한 역할: {', '.join(ws.roles) or '(없음)'}"
        )
    return Fixed(workspace, role)


def list_workspaces_tool(service: Any, fixed: Fixed | None = None) -> list[dict[str, Any]]:
    """워크스페이스 목록. 고정했으면 그 워크스페이스와 그 역할만."""
    listed = []
    for ws in service.store.list():
        if fixed is not None and ws.id != fixed.workspace:
            continue
        summary = service.summary(ws)
        listed.append(
            {
                "id": ws.id,
                "name": ws.name,
                "industry": ws.industry,
                "roles": [fixed.role] if fixed is not None else list(ws.roles),
                "documents": summary.documents,
                "index": summary.index.state,
            }
        )
    return listed


def ask_tool(
    service: Any,
    question: Any,
    workspace: str | None = None,
    role: str | None = None,
    fixed: Fixed | None = None,
) -> dict[str, Any]:
    """워크스페이스의 문서를 근거로 답한다 → {answer, citations, passages, refused, model,
    latency_ms}. 고정했으면 workspace·role 인자는 쓰지 않는다."""
    if fixed is not None:
        workspace, role = fixed.workspace, fixed.role
    if not isinstance(question, str) or not question.strip():
        raise ToolError("질문이 비어 있습니다")
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        raise ToolError(f"질문이 너무 깁니다(상한 {MAX_QUESTION_CHARS}자)")
    ws = _workspace(service, workspace)
    if role is not None and not isinstance(role, str):
        raise ToolError("역할은 역할 이름(문자열) 하나여야 합니다")
    if role is None and len(ws.roles) == 1:
        role = next(iter(ws.roles))
    if role is None or role not in ws.roles:
        # 역할이 여럿이면 넓은 쪽으로 대신 정하지 않는다
        raise ToolError(f"역할을 골라 주세요. 가능한 역할: {', '.join(ws.roles) or '(없음)'}")
    try:
        response, _ = service.answer_with_usage(ws, question, role)
    except IndexNotReadyError as e:
        raise ToolError(f"색인이 준비되지 않았습니다: {e}") from None
    except LLMUnavailableError as e:
        raise ToolError(f"LLM을 사용할 수 없습니다: {e}") from None
    except RerankerUnavailableError as e:
        # 이 명령을 실행한 사람이 설정한 사람이다: 원인과 해결 방법을 그대로 보여 준다
        raise ToolError(reranker_unavailable_detail(e)) from None
    except HTTPException as e:
        raise ToolError(str(e.detail)) from None
    return response.model_dump(mode="json")


def build_server(service: Any, fixed: Fixed | None = None) -> Any:
    """MCP 서버에 도구를 등록해 돌려준다. mcp 패키지(2.x)가 있어야 한다."""
    # mcp 2.x에서 FastMCP가 MCPServer로 바뀌었다. 1.x는 지원하지 않는다(pyproject의 mcp>=2)
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError as McpToolError

    def surfaced(call: Any, *args: Any, **kwargs: Any) -> Any:
        # SDK는 자기 ToolError의 문장만 클라이언트에 전하고, 다른 예외는 문장을 숨긴다
        try:
            return call(*args, **kwargs)
        except ToolError as e:
            raise McpToolError(str(e)) from None

    # 기본 log_level은 INFO라 요청마다 stderr에 한 줄씩 찍는다
    server = MCPServer("axbuilder", log_level="WARNING")

    @server.tool()
    def list_workspaces() -> list[dict[str, Any]]:
        """질문할 수 있는 워크스페이스 목록(id, 이름, 역할, 문서 수, 색인 상태)."""
        return surfaced(list_workspaces_tool, service, fixed)

    if fixed is not None:

        @server.tool()
        def ask(question: str) -> dict[str, Any]:
            """회사 문서를 근거로 질문에 답한다. 답변, 인용(문서·조항), 인용한 원문을 돌려준다."""
            return surfaced(ask_tool, service, question, fixed=fixed)

    else:

        @server.tool()
        def ask(workspace: str, question: str, role: str | None = None) -> dict[str, Any]:
            """회사 문서를 근거로 질문에 답한다. workspace는 list_workspaces의 id, role은 그
            워크스페이스의 역할 이름(역할이 하나뿐이면 생략 가능). 답변, 인용, 원문을 돌려준다."""
            return surfaced(ask_tool, service, question, workspace=workspace, role=role)

    return server


def run(settings: Settings, workspace: str | None, role: str | None) -> int:
    """stdio MCP 서버를 돌린다. 끝나면 0, 옵션이 틀리면 2."""
    # stdout은 프로토콜 전용: 로그는 stderr로
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    service = create_service(settings)
    try:
        try:
            fixed = check_fixed(service, workspace, role)
        except ToolError as e:
            print(str(e), file=sys.stderr)
            return 2
        build_server(service, fixed).run()
    finally:
        service.close()
    return 0
