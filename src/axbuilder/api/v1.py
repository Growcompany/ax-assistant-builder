"""API 키 관리와, 키로 부르는 OpenAI 호환 API.

  POST/GET/DELETE /workspaces/{id}/keys   키 발급·목록·폐기 (관리 API: 이 PC에서만)
  GET  /v1/models                          그 키의 워크스페이스 하나
  POST /v1/chat/completions                OpenAI chat.completion 모양 (stream=true면 SSE)
  POST /v1/ask                             {question} → AskResponse

키로 온 요청의 워크스페이스와 역할은 키에서만 온다. 본문의 role·workspace 같은 필드, system
메시지, 주소의 질의 문자열은 읽지 않는다. model은 키의 워크스페이스 id여야 한다.

아직 하지 않는 것: 이전 대화·system 메시지는 답에 쓰지 않는다(마지막 user 메시지만 질문으로
쓴다). stream=true는 토큰 단위가 아니라 완성된 답을 한 조각으로 보낸다.
"""

import json
import logging
import time
import unicodedata
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints, field_validator

from axbuilder.api.access import KEY_SCOPE, V1Error, unauthorized
from axbuilder.api.keys import KEY_ID, KeyRecord, TooManyKeysError
from axbuilder.models import Citation
from axbuilder.workspace import Workspace, WorkspaceNotFoundError

logger = logging.getLogger(__name__)

MAX_KEY_NAME_CHARS = 100
MAX_QUESTION_CHARS = 2000  # /ask와 같은 상한


class NewKey(BaseModel):
    # 모르는 필드(workspace, key)를 조용히 무시하면 그대로 된 줄 안다
    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_KEY_NAME_CHARS)
    ]
    role: str

    @field_validator("name")
    @classmethod
    def _single_line(cls, value: str) -> str:
        # 화면의 키 목록에 그대로 보인다: 줄바꿈·제어 문자·보이지 않는 형식 문자를 받지 않는다
        if any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") for char in value):
            raise ValueError("must be a single line without control or format characters")
        return value


class CreatedKey(BaseModel):
    key_id: str
    name: str
    role: str
    key: str  # 원문. 이 응답에서만 나온다
    created_at: str


class KeyInfo(BaseModel):
    key_id: str
    name: str
    role: str
    created_at: str
    last_used_at: str | None
    prefix: str  # "axb_xxxx…"


class _Part(BaseModel):
    type: str = ""
    text: str | None = None


class _Message(BaseModel):
    role: str
    content: str | list[_Part] | None = None


class ChatRequest(BaseModel):
    """OpenAI 클라이언트가 보내는 다른 필드(temperature, tools, user …)는 읽지 않고 버린다."""

    model: str
    messages: list[_Message]
    stream: StrictBool = False


class V1AskRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
    ]


def _last_user_text(messages: list[_Message]) -> str:
    for message in reversed(messages):
        if message.role != "user":
            continue
        if isinstance(message.content, str):
            return message.content.strip()
        # 파트 배열: text 파트만 잇는다(이미지 등은 쓰지 않는다)
        parts = message.content or []
        return "".join(p.text for p in parts if p.type == "text" and p.text).strip()
    return ""


def _source_list(citations: list[Citation]) -> str:
    lines = []
    for number, citation in enumerate(citations, start=1):
        if citation.article:
            where = f" {citation.article}"
        elif citation.page is not None:
            where = f" p.{citation.page}"
        else:
            where = ""
        lines.append(f"[{number}] {citation.title}{where}")
    return "\n\n출처\n" + "\n".join(lines)


def add_routes(app: FastAPI, service: Any) -> None:
    """service는 app.py의 _Service(store, keys, workspace(), answer_with_usage())."""

    # ---- 키 관리 ----

    @app.post("/workspaces/{workspace_id}/keys", status_code=201)
    def create_key(workspace_id: str, body: NewKey) -> CreatedKey:
        ws = service.workspace(workspace_id)
        if body.role not in ws.roles:
            raise HTTPException(
                422, f"알 수 없는 역할입니다. 가능한 역할: {', '.join(ws.roles) or '(없음)'}"
            )
        try:
            record, raw = service.keys.create(ws.id, body.name, body.role)
        except TooManyKeysError:
            raise HTTPException(
                409, "이 워크스페이스의 키가 너무 많습니다. 쓰지 않는 키를 폐기하세요"
            ) from None
        return CreatedKey(
            key_id=record.key_id,
            name=record.name,
            role=record.role,
            key=raw,
            created_at=record.created_at,
        )

    @app.get("/workspaces/{workspace_id}/keys")
    def list_keys(workspace_id: str) -> list[KeyInfo]:
        ws = service.workspace(workspace_id)
        return [
            KeyInfo(
                key_id=record.key_id,
                name=record.name,
                role=record.role,
                created_at=record.created_at,
                last_used_at=last_used_at,
                prefix=record.prefix,
            )
            for record, last_used_at in service.keys.list(ws.id)
        ]

    @app.delete("/workspaces/{workspace_id}/keys/{key_id}", status_code=204)
    def revoke_key(workspace_id: str, key_id: str) -> Response:
        ws = service.workspace(workspace_id)
        if not KEY_ID.fullmatch(key_id) or not service.keys.revoke(ws.id, key_id):
            raise HTTPException(404, "키를 찾을 수 없습니다")
        return Response(status_code=204)

    # ---- 키로 부르는 API ----

    def key_workspace(request: Request) -> tuple[Workspace, KeyRecord]:
        """요청의 키가 가리키는 (워크스페이스, 키). 워크스페이스·역할은 여기서만 정해진다.

        키가 가리키는 워크스페이스나 역할이 없어졌으면 없는 키와 똑같이 401이다(fail-closed).
        """
        record = request.scope.get(KEY_SCOPE)
        if not isinstance(record, KeyRecord) or not service.keys.is_active(record):
            raise unauthorized()
        try:
            ws = service.store.get(record.workspace_id)
        except WorkspaceNotFoundError:
            raise unauthorized() from None
        if record.role not in ws.roles:
            logger.warning(
                "key %s of %s is bound to a role that no longer exists",
                record.key_id,
                record.workspace_id,
            )
            raise unauthorized()
        service.keys.touch(record)
        return ws, record

    @app.get("/v1/models")
    def v1_models(request: Request) -> dict[str, Any]:
        ws, _ = key_workspace(request)
        return {
            "object": "list",
            "data": [{"id": ws.id, "object": "model", "created": 0, "owned_by": "axbuilder"}],
        }

    @app.post("/v1/ask")
    def v1_ask(request: Request, body: V1AskRequest) -> JSONResponse:
        ws, record = key_workspace(request)
        response, _ = service.answer_with_usage(ws, body.question, record.role)
        return JSONResponse(response.model_dump(mode="json"))

    @app.post("/v1/chat/completions")
    def v1_chat_completions(request: Request, body: ChatRequest) -> Response:
        ws, record = key_workspace(request)
        if body.model != ws.id:
            raise V1Error(
                404,
                f"그런 모델이 없습니다. 이 키로 쓸 수 있는 모델은 '{ws.id}'입니다",
                "model_not_found",
            )
        question = _last_user_text(body.messages)
        if not question:
            raise V1Error(400, "messages에 내용이 있는 user 메시지가 없습니다", "invalid_request")
        if len(question) > MAX_QUESTION_CHARS:
            raise V1Error(
                400, f"질문이 너무 깁니다(상한 {MAX_QUESTION_CHARS}자)", "invalid_request"
            )
        response, llm = service.answer_with_usage(ws, question, record.role)

        content = response.answer
        if response.citations and not response.refused:
            content += _source_list(response.citations)
        prompt_tokens = llm.input_tokens if llm is not None else 0
        completion_tokens = llm.output_tokens if llm is not None else 0
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        axb = response.model_dump(
            mode="json", include={"refused", "citations", "passages", "model", "latency_ms"}
        )
        base = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "created": int(time.time()),
            "model": ws.id,
        }
        if not body.stream:
            return JSONResponse(
                {
                    **base,
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                    "axb": axb,
                }
            )

        def chunk(delta: dict[str, str], finish_reason: str | None, **extra: Any) -> str:
            data = {
                **base,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                **extra,
            }
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        # 답은 이미 다 만들어졌다: 본문을 한 조각으로 보낸다(토큰 단위 스트리밍이 아니다)
        events = [
            chunk({"role": "assistant", "content": ""}, None),
            chunk({"content": content}, None),
            chunk({}, "stop", usage=usage, axb=axb),
            "data: [DONE]\n\n",
        ]
        return StreamingResponse(
            iter(events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
