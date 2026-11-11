"""접근 규칙: 누가 어디까지 올 수 있는가.

  /v1/*        API 키가 있어야 한다(없거나 틀리면 401). 어디서 오든 된다
  그 밖의 경로  이 PC(loopback)에서 온 요청만. 워크스페이스·업로드·색인·키 발급과 화면이 여기 있다

"이 PC에서 왔는가"는 ASGI scope의 client(소켓의 상대 주소)만으로 정한다. X-Forwarded-For,
Forwarded, X-Real-IP 같은 머리글은 요청을 보낸 쪽이 마음대로 쓸 수 있으므로 읽지 않는다. 주소가
없거나 읽을 수 없으면 밖에서 온 것으로 본다.

주의: scope의 client는 ASGI 서버가 채운다. uvicorn은 --forwarded-allow-ips(환경변수
FORWARDED_ALLOW_IPS, 기본 127.0.0.1)에 있는 상대가 보낸 X-Forwarded-For로 이 값을 바꾼다. 기본값이면
"이 PC → 밖"으로만 바뀌어 안전하지만, "*"로 두면 밖에서 온 요청이 이 PC인 척할 수 있다. axb serve는
127.0.0.1로 고정한다. 같은 PC의 리버스 프록시 뒤에 둘 때는 프록시가 X-Forwarded-For를 붙이게 한다
(붙이지 않으면 프록시가 넘긴 요청이 모두 이 PC에서 온 것으로 보인다).

/v1/* 의 오류는 OpenAI 모양({"error": {message, type, code, param}})이다.
"""

import ipaddress
import re
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import MutableHeaders

from axbuilder.api.keys import KeyStore

KEY_SCOPE = "axb.key"  # 인증된 키(KeyRecord)를 ASGI scope에 두는 자리. 이 미들웨어만 쓴다
UNAUTHORIZED_MESSAGE = "API 키가 없거나 올바르지 않습니다"
LOOPBACK_ONLY_MESSAGE = (
    "이 PC에서만 쓸 수 있는 주소입니다. 다른 곳에서는 API 키로 /v1/* 만 쓸 수 있습니다"
)
_BEARER = re.compile(r"[Bb][Ee][Aa][Rr][Ee][Rr] +(\S+)")
# preflight가 물어 온 머리글 이름 목록(쉼표로 구분한 토큰)만 되돌려 준다
_HEADER_NAMES = re.compile(r"[A-Za-z0-9-]+(?: *, *[A-Za-z0-9-]+)*")
CORS_METHODS = "GET, POST, OPTIONS"
CORS_DEFAULT_HEADERS = "Authorization, Content-Type"
CORS_MAX_AGE = "600"


def is_v1_path(path: str) -> bool:
    return path == "/v1" or path.startswith("/v1/")


def is_loopback_client(client: Any) -> bool:
    """ASGI scope의 client가 loopback 주소(127.0.0.0/8, ::1)인가. 모르면 False."""
    try:
        host = client[0]
    except (TypeError, IndexError, KeyError):
        return False
    if not isinstance(host, str):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:  # 이름("localhost", "testclient")은 풀지 않는다
        return False
    mapped = getattr(address, "ipv4_mapped", None)  # ::ffff:127.0.0.1
    return (mapped or address).is_loopback


class V1Error(HTTPException):
    """/v1/* 에서 내는 오류. code는 OpenAI 오류의 code 자리에 들어간다."""

    def __init__(
        self, status_code: int, message: str, code: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status_code, message, headers)
        self.code = code


_DEFAULT_CODES = {
    400: "invalid_request",
    401: "invalid_api_key",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "request_too_large",
    500: "internal_error",
    503: "service_unavailable",
}


def openai_error(
    status_code: int,
    message: str,
    code: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "server_error" if status_code >= 500 else "invalid_request_error",
                "code": code or _DEFAULT_CODES.get(status_code, "error"),
                "param": None,
            }
        },
        headers=headers,
    )


def unauthorized() -> V1Error:
    """키 없음·형식 틀림·없는 키·폐기된 키·역할이 없어진 키 모두 이 오류 하나다."""
    return V1Error(401, UNAUTHORIZED_MESSAGE, "invalid_api_key", {"WWW-Authenticate": "Bearer"})


def _unauthorized_response() -> JSONResponse:
    error = unauthorized()
    return openai_error(error.status_code, error.detail, error.code, error.headers)


def _single_header(scope: Any, name: bytes) -> str | None:
    values = [value for key, value in scope["headers"] if key == name]
    if len(values) != 1:  # 없거나 여러 개: 어느 것을 믿을지 정하지 않는다
        return None
    return values[0].decode("latin-1")


class AccessRules:
    """loopback이 아닌 클라이언트는 /v1/* 만. /v1/* 는 유효한 키가 있어야 한다.

    유효한 키를 scope[KEY_SCOPE]에 둔다. 안쪽의 Origin·Sec-Fetch-Site 검사는 그 값이 있으면
    건너뛴다(키가 인증이다). 키가 없거나 틀리면 그 검사까지 가지 않고 여기서 401이다.

    CORS: cors_origins에 정확히 일치하는 Origin에만 응답 머리글을 단다. preflight(OPTIONS +
    Access-Control-Request-Method)는 브라우저가 Authorization을 붙이지 않으므로 키 없이 답한다.
    """

    def __init__(self, app: Any, keys: KeyStore, cors_origins: tuple[str, ...]) -> None:
        self.app = app
        self.keys = keys
        self.cors_origins = frozenset(cors_origins)

    def _cors_origin(self, scope: Any) -> str | None:
        origin = _single_header(scope, b"origin")
        return origin if origin is not None and origin.lower() in self.cors_origins else None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        v1 = is_v1_path(scope["path"])
        if scope["type"] == "websocket":
            # websocket 경로는 없다. 생기더라도 밖에서, 또는 /v1 아래로는 열리지 않게
            if v1 or not is_loopback_client(scope.get("client")):
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        if not v1:
            if not is_loopback_client(scope.get("client")):
                response = JSONResponse(status_code=403, content={"detail": LOOPBACK_ONLY_MESSAGE})
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        origin = self._cors_origin(scope)
        cors = {"Access-Control-Allow-Origin": origin} if origin is not None else {}
        if self.cors_origins:
            cors["Vary"] = "Origin"  # 캐시가 한 출처의 응답을 다른 출처에 주지 않게

        if scope["method"] == "OPTIONS" and _single_header(scope, b"access-control-request-method"):
            headers = dict(cors)
            if origin is not None:
                asked = _single_header(scope, b"access-control-request-headers") or ""
                headers["Access-Control-Allow-Methods"] = CORS_METHODS
                headers["Access-Control-Allow-Headers"] = (
                    asked if _HEADER_NAMES.fullmatch(asked) else CORS_DEFAULT_HEADERS
                )
                headers["Access-Control-Max-Age"] = CORS_MAX_AGE
            await Response(status_code=204, headers=headers)(scope, receive, send)
            return

        authorization = _single_header(scope, b"authorization")
        match = _BEARER.fullmatch(authorization) if authorization is not None else None
        record = self.keys.authenticate(match.group(1) if match else None)
        if record is None:
            response = _unauthorized_response()
            response.headers.update(cors)
            await response(scope, receive, send)
            return
        scope[KEY_SCOPE] = record

        async def send_with_cors(message: Any) -> None:
            if message["type"] == "http.response.start" and cors:
                headers = MutableHeaders(scope=message)
                for name, value in cors.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_cors)
