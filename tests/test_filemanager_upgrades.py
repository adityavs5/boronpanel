"""Phase 8 feature 13: file manager upgrades (copy, bulk, zip, search) --
all jailed to the account home."""
import os
import zipfile

import pytest

from daemon import filemanager


@pytest.fixture()
def home(monkeypatch, tmp_path):
    base = tmp_path / "home"
    (base / "demo1").mkdir(parents=True)
    monkeypatch.setattr(filemanager.settings, "home_base", str(base))
    monkeypatch.setattr(filemanager, "_account_uid_gid", lambda u: (os.getuid(), os.getgid()))
    return base / "demo1"


def _write(home, rel, content=""):
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# --- copy ------------------------------------------------------------------


def test_copy_file(home):
    _write(home, "public_html/a.txt", "hello")
    filemanager.copy({"username": "demo1", "src": "public_html/a.txt", "dst": "public_html/b.txt"})
    assert (home / "public_html/b.txt").read_text() == "hello"


def test_copy_dir(home):
    _write(home, "src/x.txt", "1")
    _write(home, "src/sub/y.txt", "2")
    filemanager.copy({"username": "demo1", "src": "src", "dst": "dst"})
    assert (home / "dst/x.txt").read_text() == "1"
    assert (home / "dst/sub/y.txt").read_text() == "2"


def test_copy_refuses_existing_dst(home):
    _write(home, "a.txt", "x")
    _write(home, "b.txt", "y")
    with pytest.raises(filemanager.FileManagerError, match="already exists"):
        filemanager.copy({"username": "demo1", "src": "a.txt", "dst": "b.txt"})


def test_copy_jailed(home):
    _write(home, "a.txt", "x")
    with pytest.raises(filemanager.FileManagerError, match="escapes"):
        filemanager.copy({"username": "demo1", "src": "a.txt", "dst": "../../escape.txt"})


# --- bulk ops --------------------------------------------------------------


def test_bulk_delete(home):
    _write(home, "a.txt")
    _write(home, "b.txt")
    _write(home, "keep.txt")
    result = filemanager.bulk_delete({"username": "demo1", "paths": ["a.txt", "b.txt"]})
    assert result["ok_count"] == 2
    assert not (home / "a.txt").exists()
    assert (home / "keep.txt").exists()


def test_bulk_delete_reports_per_item_failure(home):
    _write(home, "a.txt")
    result = filemanager.bulk_delete({"username": "demo1", "paths": ["a.txt", "missing.txt"]})
    assert result["ok_count"] == 1
    outcomes = {r["path"]: r["ok"] for r in result["results"]}
    assert outcomes == {"a.txt": True, "missing.txt": False}


def test_bulk_move_into_dest(home):
    _write(home, "a.txt", "1")
    _write(home, "b.txt", "2")
    (home / "dest").mkdir()
    filemanager.bulk_move({"username": "demo1", "paths": ["a.txt", "b.txt"], "dest": "dest"})
    assert (home / "dest/a.txt").read_text() == "1"
    assert not (home / "a.txt").exists()


def test_bulk_copy_into_dest(home):
    _write(home, "a.txt", "1")
    (home / "dest").mkdir()
    filemanager.bulk_copy({"username": "demo1", "paths": ["a.txt"], "dest": "dest"})
    assert (home / "dest/a.txt").read_text() == "1"
    assert (home / "a.txt").exists()  # original kept


# --- zip -------------------------------------------------------------------


def test_zip_files(home):
    _write(home, "docs/a.txt", "aaa")
    _write(home, "docs/b.txt", "bbb")
    result = filemanager.make_zip({"username": "demo1", "paths": ["docs"], "archive": "backup"})
    assert result["archive"] == "backup.zip"
    archive = home / "backup.zip"
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert "docs/a.txt" in names and "docs/b.txt" in names


def test_zip_refuses_existing(home):
    _write(home, "a.txt", "x")
    _write(home, "out.zip", "notazip")
    with pytest.raises(filemanager.FileManagerError, match="already exists"):
        filemanager.make_zip({"username": "demo1", "paths": ["a.txt"], "archive": "out.zip"})


# --- search ----------------------------------------------------------------


def test_search_by_name(home):
    _write(home, "public_html/index.php")
    _write(home, "public_html/style.css")
    _write(home, "public_html/app.php")
    result = filemanager.search({"username": "demo1", "query": ".php", "mode": "name", "path": "public_html"})
    names = {r["name"] for r in result["results"]}
    assert names == {"index.php", "app.php"}


def test_search_by_content(home):
    _write(home, "a.php", "<?php echo 'needle here';")
    _write(home, "b.php", "<?php echo 'nothing';")
    result = filemanager.search({"username": "demo1", "query": "needle", "mode": "content"})
    assert len(result["results"]) == 1
    assert result["results"][0]["name"] == "a.php"
    assert "needle" in result["results"][0]["text"]


def test_search_empty_query_rejected(home):
    with pytest.raises(filemanager.FileManagerError, match="query"):
        filemanager.search({"username": "demo1", "query": "  ", "mode": "name"})
