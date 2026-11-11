"""콘솔 명령 axb.

  axb serve [--port 8000] [--host 127.0.0.1] [--no-browser] [--data-dir PATH]
      서버를 띄우고 화면을 연다. 기본은 이 PC(127.0.0.1)에서만 붙을 수 있다
  axb mcp [--data-dir PATH] [--workspace ID --role ROLE]
      MCP 서버(stdio). 선택 설치가 필요하다: pip install -e ".[mcp]"
  axb --version

실행 파일 없이도 된다: python -m axbuilder.cli serve
"""

import argparse
import importlib.util
import ipaddress
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from axbuilder import __version__
from axbuilder.generation.llm import LLMUnavailableError, has_ollama_model, list_ollama_models
from axbuilder.settings import Settings

# 기본은 loopback에만 띄운다. --host로 다른 주소에 띄워도 관리 API와 화면은 이 PC에서 온 요청만
# 받고(axbuilder.api.access), 밖에서는 API 키로 /v1/* 만 쓸 수 있다
HOST = "127.0.0.1"
DEFAULT_PORT = 8000
BROWSER_WAIT_SECONDS = 60.0  # 서버가 이 안에 뜨지 않으면 브라우저를 열지 않는다


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("포트는 1~65535 사이의 숫자여야 합니다")
    return port


def _host(value: str) -> str:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "IP 주소여야 합니다 (예: 127.0.0.1, 0.0.0.0, 192.168.0.10)"
        ) from None
    return value


def mcp_installed() -> bool:
    return importlib.util.find_spec("mcp") is not None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="axb", description="AX Assistant Builder: 회사 문서로 전용 어시스턴트를 만든다"
    )
    parser.add_argument("--version", action="version", version=f"axb {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<명령>")

    serve = commands.add_parser("serve", help="서버를 띄우고 화면을 연다 (기본 127.0.0.1)")
    serve.add_argument(
        "--host",
        type=_host,
        default=HOST,
        help="띄울 주소 (기본 127.0.0.1). 다른 PC에서 API 키로 /v1/* 을 쓰게 하려면 0.0.0.0",
    )
    serve.add_argument("--port", type=_port, default=DEFAULT_PORT, help="포트 (기본 8000)")
    serve.add_argument("--no-browser", action="store_true", help="브라우저를 열지 않는다")
    serve.add_argument(
        "--data-dir", type=Path, default=None, help="데이터 폴더 (기본: AXB_DATA_DIR 또는 data)"
    )
    serve.set_defaults(handler=_serve)

    mcp = commands.add_parser("mcp", help="MCP 서버(stdio)를 띄운다")
    mcp.add_argument(
        "--data-dir", type=Path, default=None, help="데이터 폴더 (기본: AXB_DATA_DIR 또는 data)"
    )
    mcp.add_argument("--workspace", default=None, help="이 워크스페이스로 고정 (--role과 함께)")
    mcp.add_argument("--role", default=None, help="이 역할로 고정 (--workspace와 함께)")
    mcp.set_defaults(handler=_mcp)
    # 명령을 더할 때: commands.add_parser(...) 뒤에 set_defaults(handler=함수)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


def _port_is_free(host: str, port: int) -> bool:
    # uvicorn은 포트를 못 잡으면 로그 여러 줄을 남기고 끝난다. 먼저 확인해 한 줄로 알린다
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _report_llm(settings: Settings) -> None:
    """LLM 상태를 한 줄씩 알린다. 준비되지 않았어도 서버는 띄운다(화면이 할 일을 안내한다)."""
    if settings.llm_mode == "cloud":
        print(f"LLM: 클라우드 모드, Claude 모델 {settings.claude_model}")
        return
    model = settings.local_model
    try:
        installed = list_ollama_models(settings.ollama_url)
    except LLMUnavailableError:
        print("Ollama: 연결 안 됨 (Ollama를 설치하고 실행하세요: ollama serve)")
        print(f"기본 모델: {model} (확인하지 못함)")
        return
    print("Ollama: 연결됨")
    if has_ollama_model(installed, model):
        print(f"기본 모델: {model} (설치됨)")
    else:
        print(f"기본 모델: {model} (없음. 받으려면: ollama pull {model})")


def open_browser_when_ready(
    url: str, port: int, timeout: float = BROWSER_WAIT_SECONDS, interval: float = 0.2
) -> threading.Thread:
    """서버가 port에서 연결을 받기 시작하면 브라우저로 url을 연다. 기다리는 스레드를 돌려준다.

    uvicorn.run은 서버가 끝날 때까지 돌아오지 않으므로, 먼저 열면 "연결할 수 없음" 화면이 뜬다.
    """

    def wait_and_open() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                socket.create_connection((HOST, port), timeout=interval).close()
            except OSError:
                time.sleep(interval)
                continue
            try:
                webbrowser.open(url)
            except Exception:  # 브라우저가 없는 환경: 주소는 이미 출력했다
                pass
            return

    thread = threading.Thread(target=wait_and_open, name="axb-open-browser", daemon=True)
    thread.start()
    return thread


def _serve(args: argparse.Namespace) -> int:
    # 실제 임베딩 모델·LLM 클라이언트를 끌어오므로 명령을 실행할 때만 import한다
    from axbuilder.api.app import create_app

    try:
        settings = Settings.from_env()
        if args.data_dir is not None:
            settings = settings.model_copy(update={"data_dir": args.data_dir})
        app = create_app(settings)
    except ValueError as e:  # 쓸 수 없는 AXB_* 값
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2
    if not _port_is_free(args.host, args.port):
        print(
            f"포트 {args.port}을 이미 다른 프로그램이 쓰고 있습니다. 다른 포트로 실행하세요: "
            f"axb serve --port {args.port + 1 if args.port < 65535 else DEFAULT_PORT}",
            file=sys.stderr,
        )
        return 1
    url = f"http://{HOST}:{args.port}"
    _report_llm(settings)
    print(f"화면: {url}  (끝내려면 Ctrl+C)")
    if not ipaddress.ip_address(args.host).is_loopback:
        print(
            f"다른 PC에서 붙을 수 있게 {args.host}에 띄웁니다. 밖에서는 API 키로 /v1/* 만 쓸 수 "
            "있고 화면과 관리 API는 이 PC에서만 열립니다. 이 PC의 이름·IP를 AXB_ALLOWED_HOSTS에 "
            "넣어야 밖에서 붙을 수 있습니다"
        )
    if not args.no_browser:
        open_browser_when_ready(url, args.port)
    # uvicorn은 기본으로 FORWARDED_ALLOW_IPS 환경변수(없으면 127.0.0.1)에서 온 요청의
    # X-Forwarded-For를 믿어 클라이언트 주소를 바꾼다. 환경변수가 "*"면 밖에서 온 요청이 머리글
    # 하나로 이 PC인 척할 수 있으므로(관리 API가 열린다) 값을 고정한다. 127.0.0.1만 믿으면 주소가
    # "이 PC → 밖"으로만 바뀔 수 있다(같은 PC의 프록시가 넘겨준 바깥 요청을 밖으로 본다)
    uvicorn.run(
        app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1"
    )
    return 0


def _mcp(args: argparse.Namespace) -> int:
    # stdout은 MCP 프로토콜 전용이다. 안내는 모두 stderr로
    if not mcp_installed():
        print(
            'MCP 서버에는 mcp 패키지가 필요합니다. 설치: pip install -e ".[mcp]"',
            file=sys.stderr,
        )
        return 2
    from axbuilder import mcp_server

    try:
        settings = Settings.from_env()
        if args.data_dir is not None:
            settings = settings.model_copy(update={"data_dir": args.data_dir})
        return mcp_server.run(settings, args.workspace, args.role)
    except ValueError as e:  # 쓸 수 없는 AXB_* 값
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
