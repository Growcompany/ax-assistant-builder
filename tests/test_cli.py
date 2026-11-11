"""콘솔 명령 axb 테스트. uvicorn·브라우저·Ollama는 모두 가짜로 바꾼다(서버를 띄우지 않는다)."""

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI

import axbuilder
import axbuilder.cli as cli
from axbuilder.generation.llm import LLMUnavailableError, OllamaModel
from axbuilder.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = Settings().local_model


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served(monkeypatch, tmp_path):
    """uvicorn.run·브라우저·Ollama 조회를 가짜로 바꾸고 기록을 돌려준다."""
    record = {"run": [], "browser": [], "threads": []}

    def fake_run(app, **kwargs):
        record["run"].append((app, kwargs))

    def fake_open_later(url, port, **kwargs):
        record["browser"].append((url, port))

    def fake_models(base_url, **kwargs):
        record.setdefault("ollama_url", base_url)
        return [OllamaModel(name=DEFAULT_MODEL)]

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.setattr(cli, "open_browser_when_ready", fake_open_later)
    monkeypatch.setattr(cli, "list_ollama_models", fake_models)
    for name in ("AXB_DATA_DIR", "AXB_LLM_MODE", "AXB_OLLAMA_URL", "AXB_LOCAL_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # 기본 data_dir("data")가 저장소에 생기지 않게
    return record


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"axb {axbuilder.__version__}"


def test_module_can_be_run_with_python_m():
    # axb 실행 파일 없이도: python -m axbuilder.cli
    result = subprocess.run(
        [sys.executable, "-m", "axbuilder.cli", "--version"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        timeout=60,
    )
    assert result.returncode == 0 and result.stdout.strip() == f"axb {axbuilder.__version__}"


def test_console_script_is_declared():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[project.scripts]\naxb = "axbuilder.cli:main"' in pyproject


def test_no_command_prints_help_and_fails(capsys):
    assert cli.main([]) == 2
    assert "serve" in capsys.readouterr().err


def test_serve_defaults():
    args = cli.build_parser().parse_args(["serve"])
    assert args.port == 8000 and args.no_browser is False and args.data_dir is None


def test_serve_host_defaults_to_loopback():
    assert cli.build_parser().parse_args(["serve"]).host == "127.0.0.1"


@pytest.mark.parametrize("host", ["localhost", "example.com", "*", ""])
def test_serve_host_must_be_an_ip_address(served, host):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["serve", "--host", host])
    assert exit_info.value.code == 2 and served["run"] == []


def test_serve_on_another_address_says_what_outside_clients_can_do(served, capsys):
    # Task 16: 밖에서는 API 키로 /v1/* 만. 브라우저는 여전히 이 PC 주소로 연다
    port = free_port()
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", str(port)]) == 0
    ((_, kwargs),) = served["run"]
    assert kwargs["host"] == "0.0.0.0" and kwargs["port"] == port
    assert served["browser"] == [(f"http://127.0.0.1:{port}", port)]
    out = capsys.readouterr().out
    assert "/v1/*" in out and "AXB_ALLOWED_HOSTS" in out


def test_serve_on_loopback_does_not_print_the_outside_notice(served, capsys):
    assert cli.main(["serve", "--port", str(free_port()), "--no-browser"]) == 0
    assert "AXB_ALLOWED_HOSTS" not in capsys.readouterr().out


@pytest.mark.parametrize("port", ["0", "-1", "65536", "abc"])
def test_serve_rejects_bad_port(served, port):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["serve", "--port", port])
    assert exit_info.value.code == 2 and served["run"] == []


def test_serve_no_browser_runs_uvicorn_on_loopback(served, capsys):
    port = free_port()
    assert cli.main(["serve", "--port", str(port), "--no-browser"]) == 0
    ((app, kwargs),) = served["run"]
    assert isinstance(app, FastAPI)
    assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == port
    # 환경변수 FORWARDED_ALLOW_IPS="*"가 있어도 밖에서 온 요청이 X-Forwarded-For로 이 PC인 척할
    # 수 없게 고정한다
    # IPv6 loopback(::1)으로 붙는 같은 PC의 프록시도 넣는다: 빼면 그 프록시가 넘긴 바깥 요청이 이
    # PC에서 온 것으로 남는다
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == "127.0.0.1,::1,::ffff:127.0.0.1"
    assert served["browser"] == []
    assert f"http://127.0.0.1:{port}" in capsys.readouterr().out


def test_serve_opens_browser_by_default(served):
    port = free_port()
    assert cli.main(["serve", "--port", str(port)]) == 0
    assert served["browser"] == [(f"http://127.0.0.1:{port}", port)]
    assert len(served["run"]) == 1


def test_serve_data_dir_option_overrides_environment(served, monkeypatch, tmp_path):
    monkeypatch.setenv("AXB_DATA_DIR", str(tmp_path / "from-env"))
    port = free_port()
    assert (
        cli.main(["serve", "--port", str(port), "--no-browser", "--data-dir", str(tmp_path / "x")])
        == 0
    )
    ((app, _),) = served["run"]
    assert app.state.service.store.root == tmp_path / "x" / "workspaces"


def test_serve_uses_environment_settings(served, monkeypatch, tmp_path):
    monkeypatch.setenv("AXB_DATA_DIR", str(tmp_path / "from-env"))
    monkeypatch.setenv("AXB_OLLAMA_URL", "http://ollama.test:1234")
    assert cli.main(["serve", "--port", str(free_port()), "--no-browser"]) == 0
    ((app, _),) = served["run"]
    assert app.state.service.store.root == tmp_path / "from-env" / "workspaces"
    assert served["ollama_url"] == "http://ollama.test:1234"


def test_serve_reports_ollama_and_default_model(served, capsys):
    assert cli.main(["serve", "--port", str(free_port()), "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "Ollama: 연결됨" in out
    assert f"기본 모델: {DEFAULT_MODEL} (설치됨)" in out


def test_serve_starts_even_when_ollama_is_down(served, monkeypatch, capsys):
    def down(base_url, **kwargs):
        raise LLMUnavailableError("Ollama 서버(http://localhost:11434)에 연결할 수 없습니다.")

    monkeypatch.setattr(cli, "list_ollama_models", down)
    assert cli.main(["serve", "--port", str(free_port()), "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "Ollama: 연결 안 됨" in out and "ollama serve" in out
    assert len(served["run"]) == 1  # 없어도 서버는 뜬다


def test_serve_tells_how_to_pull_missing_default_model(served, monkeypatch, capsys):
    monkeypatch.setattr(cli, "list_ollama_models", lambda url, **kw: [OllamaModel(name="x:1b")])
    assert cli.main(["serve", "--port", str(free_port()), "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert "Ollama: 연결됨" in out
    assert f"ollama pull {DEFAULT_MODEL}" in out
    assert len(served["run"]) == 1


def test_serve_in_cloud_mode_does_not_ask_ollama(served, monkeypatch, capsys):
    monkeypatch.setenv("AXB_LLM_MODE", "cloud")

    def must_not_call(base_url, **kwargs):
        raise AssertionError("cloud mode must not call Ollama")

    monkeypatch.setattr(cli, "list_ollama_models", must_not_call)
    assert cli.main(["serve", "--port", str(free_port()), "--no-browser"]) == 0
    assert Settings().claude_model in capsys.readouterr().out


def test_serve_port_in_use_is_one_readable_line(served, capsys):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        assert cli.main(["serve", "--port", str(port)]) == 1
    captured = capsys.readouterr()
    (line,) = captured.err.strip().splitlines()
    assert str(port) in line and "--port" in line
    assert "Traceback" not in captured.err
    assert served["run"] == [] and served["browser"] == []


def test_serve_bad_settings_is_one_readable_line(served, monkeypatch, capsys):
    monkeypatch.setenv("AXB_OLLAMA_URL", "user:s3cret@host:11434")  # 스킴 없음
    assert cli.main(["serve", "--port", str(free_port()), "--no-browser"]) == 2
    captured = capsys.readouterr()
    assert "AXB_OLLAMA_URL" in captured.err and "s3cret" not in captured.err
    assert "Traceback" not in captured.err and served["run"] == []


# ---- 브라우저는 서버가 뜬 뒤에 연다 ----


def test_browser_opens_only_after_the_port_accepts_connections(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    port = free_port()
    thread = cli.open_browser_when_ready(
        f"http://127.0.0.1:{port}", port, timeout=10, interval=0.02
    )
    assert isinstance(thread, threading.Thread) and thread.daemon
    thread.join(0.2)
    assert opened == []  # 아직 아무도 듣지 않는다
    with socket.socket() as server:
        server.bind(("127.0.0.1", port))
        server.listen(1)
        thread.join(5)
    assert not thread.is_alive()
    assert opened == [f"http://127.0.0.1:{port}"]


def test_browser_is_not_opened_when_the_server_never_starts(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    port = free_port()
    thread = cli.open_browser_when_ready(
        f"http://127.0.0.1:{port}", port, timeout=0.2, interval=0.02
    )
    thread.join(5)
    assert not thread.is_alive() and opened == []


def test_browser_failure_does_not_crash_the_server_thread(monkeypatch):
    def broken(url):
        raise RuntimeError("no display")

    monkeypatch.setattr(cli.webbrowser, "open", broken)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        thread = cli.open_browser_when_ready("http://127.0.0.1", port, timeout=5, interval=0.02)
        thread.join(5)
    assert not thread.is_alive()
