"""fsutil: Windows의 공유 위반(PermissionError)에 잠깐 다시 해 보는 파일 연산."""

import os

import pytest

import axbuilder.fsutil as fsutil


class Flaky:
    """처음 failures번은 PermissionError를 던지고 그 뒤에는 원래 함수를 부른다."""

    def __init__(self, real, failures: int, error: type[OSError] = PermissionError) -> None:
        self.real = real
        self.failures = failures
        self.error = error
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error(13, "The process cannot access the file")
        return self.real(*args, **kwargs)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(fsutil, "RETRY_DELAYS", (0.0, 0.0, 0.0))  # 다시 해 보기 3번, 기다리지 않음


def test_replace_retries_a_sharing_violation(tmp_path, monkeypatch):
    source, target = tmp_path / "a.tmp", tmp_path / "a.json"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    flaky = Flaky(os.replace, failures=3)
    monkeypatch.setattr(fsutil.os, "replace", flaky)
    fsutil.replace(source, target)
    assert flaky.calls == 4 and target.read_text(encoding="utf-8") == "new" and not source.exists()


def test_replace_gives_up_after_the_last_retry(tmp_path, monkeypatch):
    source, target = tmp_path / "a.tmp", tmp_path / "a.json"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    flaky = Flaky(os.replace, failures=99)
    monkeypatch.setattr(fsutil.os, "replace", flaky)
    with pytest.raises(PermissionError):
        fsutil.replace(source, target)
    assert flaky.calls == 4 and target.read_text(encoding="utf-8") == "old"


def test_other_errors_are_not_retried(tmp_path, monkeypatch):
    flaky = Flaky(os.replace, failures=99, error=FileNotFoundError)
    monkeypatch.setattr(fsutil.os, "replace", flaky)
    with pytest.raises(FileNotFoundError):
        fsutil.replace(tmp_path / "missing.tmp", tmp_path / "a.json")
    assert flaky.calls == 1


def test_unlink_retries_and_tolerates_a_missing_file(tmp_path, monkeypatch):
    target = tmp_path / "index.json"
    target.write_text("{}", encoding="utf-8")
    flaky = Flaky(os.unlink, failures=2)
    monkeypatch.setattr(fsutil.os, "unlink", flaky)
    fsutil.unlink(target)
    assert flaky.calls == 3 and not target.exists()
    fsutil.unlink(target)  # 이미 없다
    with pytest.raises(FileNotFoundError):
        fsutil.unlink(target, missing_ok=False)


def test_read_retries_a_sharing_violation(tmp_path, monkeypatch):
    target = tmp_path / "workspace.yaml"
    target.write_bytes("이름: 은행\n".encode())
    flaky = Flaky(fsutil._read_bytes, failures=2)
    monkeypatch.setattr(fsutil, "_read_bytes", flaky)
    assert fsutil.read_text(target) == "이름: 은행\n"
    assert flaky.calls == 3


def test_read_of_a_missing_file_fails_at_once(tmp_path, monkeypatch):
    flaky = Flaky(fsutil._read_bytes, failures=0)
    monkeypatch.setattr(fsutil, "_read_bytes", flaky)
    with pytest.raises(FileNotFoundError):
        fsutil.read_text(tmp_path / "nope.json")
    assert flaky.calls == 1


def test_read_text_reports_bad_encoding_as_value_error(tmp_path):
    target = tmp_path / "index.json"
    target.write_bytes(b"\xff\xfe\x00{")
    with pytest.raises(ValueError):
        fsutil.read_text(target)
