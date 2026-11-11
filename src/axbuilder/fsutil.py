"""작은 설정·기록 파일을 읽고 바꿔 끼우는 연산. Windows의 공유 위반에 잠깐 다시 해 본다.

Windows에서는 다른 스레드가 파일을 열고 있는 동안 그 파일로 os.replace 하거나 지우면
PermissionError(WinError 5·32)가 나고, 바꿔 끼우는 순간에 여는 쪽도 같은 오류를 받는다. 화면이
워크스페이스 목록을 읽는 것(잠금 없음)과 설정 저장·재색인이 겹치면 실제로 일어난다.

읽기-쓰기 잠금으로 막지 않는 이유: 재색인은 워크스페이스 잠금을 임베딩이 끝날 때까지 잡는다.
읽는 쪽이 그 잠금을 기다리면 색인하는 동안 화면의 목록이 멈춘다. 파일은 작아서 여는 시간이
아주 짧으므로, 겹쳤을 때 수 ms 뒤에 다시 하면 된다.

POSIX에서는 열려 있는 파일도 바꿔 끼우고 지울 수 있어 이 오류가 나지 않는다.
"""

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

# 다시 해 보기 전에 기다리는 시간(초). 합쳐 약 1초: 그래도 안 되면 진짜 권한 문제로 본다
RETRY_DELAYS: tuple[float, ...] = (0.002, 0.005, 0.01, 0.02, 0.04, 0.08, 0.15, 0.25, 0.4)


def _retry(action: Callable[[], T]) -> T:
    for delay in RETRY_DELAYS:
        try:
            return action()
        except PermissionError:
            time.sleep(delay)
    return action()


def replace(source: Path, target: Path) -> None:
    """source를 target 자리에 바꿔 끼운다(원자적)."""
    _retry(lambda: os.replace(source, target))


def unlink(path: Path, missing_ok: bool = True) -> None:
    def remove() -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            if not missing_ok:
                raise

    _retry(remove)


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def read_bytes(path: Path) -> bytes:
    return _retry(lambda: _read_bytes(path))


def read_text(path: Path, encoding: str = "utf-8") -> str:
    """없으면 FileNotFoundError, 그 인코딩이 아니면 ValueError(UnicodeDecodeError)."""
    return read_bytes(path).decode(encoding)
