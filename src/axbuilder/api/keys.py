"""API 키: 화면 밖(/v1/*)에서 쓰는 길의 인증. 키 하나는 워크스페이스 하나와 역할 하나에 묶인다.

저장 (data_dir/workspaces/{id}/keys.json):
  {"keys": [{key_id, name, role, hash, prefix, created_at, last_used_at}]}
  원문은 저장하지 않는다. hash는 원문의 SHA-256(16진수)이다. 원문은 256비트 난수라 사전 공격이
  통하지 않으므로 느린 해시(bcrypt 등)나 소금은 쓰지 않는다. 파일이 새어도 원문을 되찾을 수 없다.

찾기: 시작할 때 모든 keys.json을 읽어 "해시 → 키" 색인을 메모리에 만들고, 발급·폐기 때 고친다.
요청마다 파일을 훑지 않는다: 받은 값의 해시를 구해 색인에서 한 번 찾고, 해시끼리 상수 시간으로
비교한다. 걸리는 시간이 워크스페이스·키의 수나 "그런 키가 있는지"에 달라지지 않는다.
이 프로세스가 켜진 뒤 다른 프로세스가 keys.json을 바꾸는 경우는 지원하지 않는다(다시 켜야 반영).

마지막 사용 시각은 메모리에 두고, 요청마다 파일을 쓰지 않는다. 워크스페이스마다
LAST_USED_FLUSH_SECONDS에 한 번, 그리고 발급·폐기·종료 때 기록한다.
"""

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from axbuilder import fsutil
from axbuilder.workspace import WorkspaceStore, is_valid_workspace_id

logger = logging.getLogger(__name__)

KEYS_FILE = "keys.json"
KEY_PREFIX = "axb_"
KEY_ID = re.compile(r"[0-9a-f]{16}")
SHOWN_CHARS = len(KEY_PREFIX) + 4  # 목록에 보여 주는 앞부분: "axb_" + 4자
MAX_KEYS_PER_WORKSPACE = 100
LAST_USED_FLUSH_SECONDS = 60.0
# 이보다 긴 값은 키일 수 없다. 해시할 길이를 묶는다(머리글 크기는 ASGI 서버도 제한한다)
MAX_PRESENTED_CHARS = 256
# 없는 키와 비교할 값. SHA-256 결과와 길이가 같아 비교 시간이 같다
_NO_HASH = "0" * 64


class TooManyKeysError(Exception):
    """워크스페이스의 키가 상한에 닿았다 (API에서는 409)."""


class KeysUnreadableError(RuntimeError):
    """keys.json을 읽을 수 없다. 빈 목록으로 보고 덮어쓰지 않는다 (API에서는 500)."""


@dataclass(frozen=True)
class KeyRecord:
    """키 하나. 원문은 없다."""

    workspace_id: str
    key_id: str
    name: str
    role: str
    key_hash: str
    prefix: str
    created_at: str


class _StoredKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    name: str
    role: str
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prefix: str
    created_at: str
    last_used_at: str | None = None


class _StoredKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[_StoredKey]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class KeyStore:
    def __init__(
        self,
        store: WorkspaceStore,
        flush_seconds: float = LAST_USED_FLUSH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._flush_seconds = flush_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._by_hash: dict[str, KeyRecord] = {}
        self._by_workspace: dict[str, list[KeyRecord]] = {}
        self._last_used: dict[tuple[str, str], str] = {}
        self._dirty: set[str] = set()  # 마지막 사용 시각이 파일보다 새로운 워크스페이스
        self._written_at: dict[str, float] = {}
        self._unreadable: set[str] = set()
        self._load()

    # ---- 읽기·쓰기 ----

    def _load(self) -> None:
        root = self._store.root
        if not root.is_dir():
            return
        started = self._clock()
        for entry in sorted(root.iterdir()):
            path = entry / KEYS_FILE
            if not is_valid_workspace_id(entry.name) or not path.is_file():
                continue
            try:
                stored = _StoredKeys.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                # 내용은 로그에 남기지 않는다. 이 워크스페이스의 키는 쓸 수 없다(fail-closed)
                logger.error("could not read %s of workspace %s", KEYS_FILE, entry.name)
                self._unreadable.add(entry.name)
                continue
            records = []
            for item in stored.keys:
                record = KeyRecord(
                    workspace_id=entry.name,
                    key_id=item.key_id,
                    name=item.name,
                    role=item.role,
                    key_hash=item.hash,
                    prefix=item.prefix,
                    created_at=item.created_at,
                )
                records.append(record)
                self._by_hash[record.key_hash] = record
                if item.last_used_at is not None:
                    self._last_used[(entry.name, record.key_id)] = item.last_used_at
            self._by_workspace[entry.name] = records
            self._written_at[entry.name] = started

    def _write_locked(self, workspace_id: str, records: list[KeyRecord] | None = None) -> None:
        """records(없으면 지금 메모리의 목록)를 파일에 쓴다. 실패하면 예외, 파일은 그대로다."""
        if records is None:
            records = self._by_workspace.get(workspace_id, [])
        directory = self._store.path(workspace_id)
        if not directory.is_dir():  # 워크스페이스가 지워졌다
            return
        data = {
            "keys": [
                {
                    "key_id": record.key_id,
                    "name": record.name,
                    "role": record.role,
                    "hash": record.key_hash,
                    "prefix": record.prefix,
                    "created_at": record.created_at,
                    "last_used_at": self._last_used.get((workspace_id, record.key_id)),
                }
                for record in records
            ]
        }
        path = directory / KEYS_FILE
        temp = path.with_name(f".{KEYS_FILE}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            fsutil.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        self._dirty.discard(workspace_id)
        self._written_at[workspace_id] = self._clock()

    def _check_readable(self, workspace_id: str) -> None:
        if workspace_id in self._unreadable:
            raise KeysUnreadableError(f"{KEYS_FILE} of workspace {workspace_id!r} is unreadable")

    # ---- 발급·목록·폐기 (관리 API) ----

    def create(self, workspace_id: str, name: str, role: str) -> tuple[KeyRecord, str]:
        """(키 기록, 원문). 원문은 여기서만 나온다."""
        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        record = KeyRecord(
            workspace_id=workspace_id,
            key_id=secrets.token_hex(8),
            name=name,
            role=role,
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            prefix=raw[:SHOWN_CHARS] + "…",
            created_at=_timestamp(),
        )
        with self._lock:
            self._check_readable(workspace_id)
            records = self._by_workspace.get(workspace_id, [])
            if len(records) >= MAX_KEYS_PER_WORKSPACE:
                raise TooManyKeysError(workspace_id)
            # 파일에 먼저 쓰고, 성공한 뒤에만 메모리에 넣는다. 쓰지 못하면 예외가 나가고 원문은
            # 아무에게도 가지 않는다(저장되지 않은 키를 사용자가 받아 쓰는 일이 없다)
            self._write_locked(workspace_id, [*records, record])
            self._by_workspace[workspace_id] = [*records, record]
            self._by_hash[record.key_hash] = record
        logger.info("issued key %s for %s (role %s)", record.key_id, workspace_id, role)
        return record, raw

    def list(self, workspace_id: str) -> list[tuple[KeyRecord, str | None]]:
        """(키 기록, 마지막 사용 시각) 목록. 만든 순서."""
        with self._lock:
            self._check_readable(workspace_id)
            return [
                (record, self._last_used.get((workspace_id, record.key_id)))
                for record in self._by_workspace.get(workspace_id, [])
            ]

    def revoke(self, workspace_id: str, key_id: str) -> bool:
        with self._lock:
            self._check_readable(workspace_id)
            records = self._by_workspace.get(workspace_id, [])
            record = next((r for r in records if r.key_id == key_id), None)
            if record is None:
                return False
            # 파일에서 먼저 지우고, 성공한 뒤에만 메모리에서 뺀다. 순서가 반대면 쓰기가 실패했을
            # 때 키가 목록에서는 사라지고(다시 폐기할 수 없다) 파일에는 남아, 다시 켜면 되살아난다.
            # 쓰지 못하면 폐기 자체가 실패한다: 키는 목록에 그대로 있고 다시 폐기할 수 있다
            remaining = [r for r in records if r is not record]
            self._write_locked(workspace_id, remaining)
            self._by_workspace[workspace_id] = remaining
            self._by_hash.pop(record.key_hash, None)
            self._last_used.pop((workspace_id, key_id), None)
        logger.info("revoked key %s of %s", key_id, workspace_id)
        return True

    # ---- 인증 (/v1/*) ----

    def authenticate(self, presented: str | None) -> KeyRecord | None:
        """받은 값이 발급된 키면 그 기록, 아니면 None.

        값이 없든, 형식이 틀리든, 없는 키든 같은 일을 한다: 해시를 구하고, 색인에서 한 번 찾고,
        해시끼리 hmac.compare_digest로 비교한다. 원문끼리 비교하지 않는다.
        """
        if not isinstance(presented, str) or len(presented) > MAX_PRESENTED_CHARS:
            presented = ""
        digest = hashlib.sha256(presented.encode("utf-8", "surrogatepass")).hexdigest()
        with self._lock:
            record = self._by_hash.get(digest)
        expected = record.key_hash if record is not None else _NO_HASH
        if hmac.compare_digest(expected, digest) and record is not None:
            return record
        return None

    def is_active(self, record: KeyRecord) -> bool:
        """인증한 뒤 그 사이에 폐기되지 않았는가."""
        with self._lock:
            return self._by_hash.get(record.key_hash) is record

    def touch(self, record: KeyRecord) -> None:
        """이 키를 지금 썼다고 적는다. 파일은 워크스페이스마다 일정 간격으로만 쓴다."""
        workspace_id = record.workspace_id
        with self._lock:
            if self._by_hash.get(record.key_hash) is not record:
                return
            self._last_used[(workspace_id, record.key_id)] = _timestamp()
            self._dirty.add(workspace_id)
            if self._clock() - self._written_at.get(workspace_id, 0.0) >= self._flush_seconds:
                self._write_safely_locked(workspace_id)

    def flush(self) -> None:
        """아직 파일에 쓰지 않은 마지막 사용 시각을 쓴다 (종료할 때)."""
        with self._lock:
            for workspace_id in sorted(self._dirty):
                self._write_safely_locked(workspace_id)

    def _write_safely_locked(self, workspace_id: str) -> None:
        # 마지막 사용 시각을 못 써도 요청은 실패시키지 않는다
        try:
            self._write_locked(workspace_id)
        except OSError:
            logger.warning("could not write %s of workspace %s", KEYS_FILE, workspace_id)
