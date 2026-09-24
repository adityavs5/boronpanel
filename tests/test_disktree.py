import pytest

from daemon import disktree


@pytest.fixture()
def account_tree(isolated_db, tmp_path, monkeypatch):
    """A real directory tree with known file sizes -- disktree.py's own
    correctness rests entirely on real `du`/`find` output parsing, so this
    is tested against real files, not mocked subprocess output (the same
    "real command over mock when fast/offline/deterministic" precedent
    used elsewhere in this suite, e.g. real openssl/sievec calls).

    isolated_db is required here, not optional: get_disk_tree's root path
    unconditionally queries the control-plane DB for a cached usage
    snapshot (_cached_account_total_bytes) even when the test doesn't care
    about that path -- without isolated_db this would silently query
    this machine's real production database instead of a throwaway one (a
    real mistake made and caught once already this phase, in a different
    test file -- docs/CHECKPOINT-phase4-0b-cross-account-idor.md)."""
    # Exercise real du/find parsing with fixture files; verify every command
    # requests the tenant identity before substituting the fixture owner.
    original_run = disktree.run
    def fixture_run(args, **kwargs):
        assert args[:4] == ['runuser', '-u', 'demo1', '--']
        return original_run(args[4:], **kwargs)
    monkeypatch.setattr(disktree, 'run', fixture_run)
    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(disktree.filemanager.settings, "home_base", str(home_base))

    account_home = home_base / "demo1"
    (account_home / "public_html").mkdir(parents=True)
    (account_home / "logs").mkdir()

    (account_home / "public_html" / "index.html").write_bytes(b"x" * 1000)
    (account_home / "public_html" / "big.zip").write_bytes(b"x" * 50_000)
    (account_home / "logs" / "access.log").write_bytes(b"x" * 5_000)

    return account_home


def test_get_disk_tree_root_lists_immediate_children(account_tree):
    result = disktree.get_disk_tree({"username": "demo1", "path": ""})
    assert result["path"] == ""
    names = {c["name"] for c in result["children"]}
    assert names == {"public_html", "logs"}
    public_html = next(c for c in result["children"] if c["name"] == "public_html")
    assert public_html["is_dir"] is True
    assert public_html["size_bytes"] >= 51_000  # 1000 + 50000, plus dir entry overhead


def test_get_disk_tree_children_sorted_largest_first(account_tree):
    result = disktree.get_disk_tree({"username": "demo1", "path": ""})
    sizes = [c["size_bytes"] for c in result["children"]]
    assert sizes == sorted(sizes, reverse=True)


def test_get_disk_tree_drills_into_subdirectory(account_tree):
    result = disktree.get_disk_tree({"username": "demo1", "path": "public_html"})
    assert result["path"] == "public_html"
    names = {c["name"] for c in result["children"]}
    assert names == {"index.html", "big.zip"}
    big = next(c for c in result["children"] if c["name"] == "big.zip")
    assert big["is_dir"] is False
    assert big["size_bytes"] == 50_000


def test_get_disk_tree_rejects_nonexistent_path(account_tree):
    with pytest.raises(disktree.DiskTreeError):
        disktree.get_disk_tree({"username": "demo1", "path": "does-not-exist"})


def test_get_disk_tree_rejects_path_escaping_home(account_tree):
    from daemon.filemanager import FileManagerError

    with pytest.raises(FileManagerError):
        disktree.get_disk_tree({"username": "demo1", "path": "../../etc"})


def test_get_top_files_returns_largest_files_globally(account_tree):
    result = disktree.get_top_files({"username": "demo1", "path": ""})
    files = result["files"]
    assert files[0]["path"] == "public_html/big.zip"
    assert files[0]["size_bytes"] == 50_000
    sizes = [f["size_bytes"] for f in files]
    assert sizes == sorted(sizes, reverse=True)


def test_get_top_files_caps_at_ten(account_tree):
    for i in range(15):
        (account_tree / "public_html" / f"file{i}.txt").write_bytes(b"x" * (i + 1))
    result = disktree.get_top_files({"username": "demo1", "path": ""})
    assert len(result["files"]) == 10


def test_get_disk_tree_root_falls_back_to_live_du_when_no_cached_snapshot(isolated_db, account_tree):
    """No UsageSnapshot row exists for 'demo1' in this isolated DB -- must
    fall back to a real, fresh `du -sb` rather than erroring."""
    result = disktree.get_disk_tree({"username": "demo1", "path": ""})
    assert result["total_bytes"] > 0


def test_get_disk_tree_root_uses_cached_snapshot_when_fresh(isolated_db, account_tree):
    from shared.db import write_session
    from shared.models import Account, UsageSnapshot

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(UsageSnapshot(account_id=account.id, disk_home_bytes=123456789, disk_mail_bytes=0, disk_db_bytes=0, inode_count=1, process_count=0))

    result = disktree.get_disk_tree({"username": "demo1", "path": ""})
    assert result["total_bytes"] == 123456789  # the cached figure, not a fresh du


def test_filename_newlines_do_not_inject_disk_tree_rows(account_tree):
    name = 'line\n999999\tinjected.txt'
    (account_tree / 'public_html' / name).write_bytes(b'123')
    result = disktree.get_disk_tree({'username': 'demo1', 'path': 'public_html'})
    entries = [c for c in result['children'] if c['name'] == name]
    assert len(entries) == 1 and entries[0]['size_bytes'] == 3
    top = disktree.get_top_files({'username': 'demo1'})
    assert any(f['path'] == 'public_html/' + name and f['size_bytes'] == 3 for f in top['files'])
