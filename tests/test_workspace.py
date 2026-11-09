import pytest

from axbuilder.workspace import (
    Workspace,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
    WorkspaceStore,
)


def make_ws(workspace_id: str = "bank") -> Workspace:
    return Workspace(
        id=workspace_id,
        name="OO은행(가상)",
        industry="finance",
        roles={"상담원": ["public"], "준법감시": ["public", "internal"]},
    )


def test_create_and_get_roundtrip(tmp_path):
    store = WorkspaceStore(tmp_path)
    ws = Workspace(
        id="bank",
        name="OO은행(가상)",
        industry="finance",
        roles={"상담원": ["public"], "준법감시": ["public", "internal"]},
    )
    store.create(ws)
    assert store.get("bank") == ws
    assert (tmp_path / "bank" / "workspace.yaml").exists()


def test_duplicate_id_raises(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.create(make_ws())
    with pytest.raises(WorkspaceExistsError):
        store.create(make_ws())


def test_unknown_id_raises(tmp_path):
    with pytest.raises(WorkspaceNotFoundError):
        WorkspaceStore(tmp_path).get("bank")


@pytest.mark.parametrize("bad", ["../etc", "Bank", "a", "x" * 33, "a/b"])
def test_rejects_path_traversal_id(bad):
    with pytest.raises(ValueError):
        Workspace(id=bad, name="x", industry="x", roles={})


@pytest.mark.parametrize("bad", ["..", "a\\b", "bank\n", "bank ", "", "con", "nul"])
def test_rejects_other_unsafe_ids(bad):
    # "bank\n": 정규식 $는 끝 줄바꿈 앞에서도 맞으므로 fullmatch로 검사해야 막힌다
    # "con"·"nul": Windows 예약 장치 이름이라 폴더로 만들 수 없다
    with pytest.raises(ValueError):
        Workspace(id=bad, name="x", industry="x", roles={})


@pytest.mark.parametrize("bad", ["../etc", "..", "a/b", "../../windows"])
def test_store_checks_id_before_touching_disk(tmp_path, bad):
    store = WorkspaceStore(tmp_path / "workspaces")
    for call in (store.path, store.get):
        with pytest.raises(ValueError):
            call(bad)
    assert list(tmp_path.iterdir()) == []


def test_create_when_raw_folder_already_exists(tmp_path):
    # 다운로드 스크립트가 data/workspaces/bank/raw/를 먼저 만들어도 워크스페이스는 만들 수 있다
    (tmp_path / "bank" / "raw").mkdir(parents=True)
    store = WorkspaceStore(tmp_path)
    store.create(make_ws())
    assert store.get("bank").name == "OO은행(가상)"


def test_list_returns_workspaces_in_id_order(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.create(make_ws("card"))
    store.create(make_ws("bank"))
    (tmp_path / "no-config").mkdir()  # workspace.yaml이 없는 폴더는 워크스페이스가 아니다
    assert [ws.id for ws in store.list()] == ["bank", "card"]


def test_list_on_missing_root_is_empty(tmp_path):
    assert WorkspaceStore(tmp_path / "missing").list() == []


def test_config_mismatched_id_is_rejected(tmp_path):
    store = WorkspaceStore(tmp_path)
    store.create(make_ws("bank"))
    (tmp_path / "card").mkdir()
    (tmp_path / "card" / "workspace.yaml").write_bytes(
        (tmp_path / "bank" / "workspace.yaml").read_bytes()
    )
    with pytest.raises(ValueError):
        store.get("card")


def test_roles_accept_only_known_access_levels():
    with pytest.raises(ValueError):
        Workspace(id="bank", name="x", industry="x", roles={"상담원": ["secret"]})
