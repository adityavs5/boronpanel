import os

import pytest

from daemon import filemanager as fm


@pytest.fixture()
def fake_account_home(tmp_path, monkeypatch):
    """Builds a fake account home dir tree under tmp_path and points
    settings.home_base + pwd.getpwnam at it, so filemanager's real
    filesystem operations (and real os.path.realpath jail checks) run
    against a throwaway directory instead of a real /home/<account>."""
    from shared.config import settings

    home_base = tmp_path / "home"
    home_base.mkdir()
    account_home = home_base / "demo1"
    account_home.mkdir()
    (account_home / "public_html").mkdir()
    (account_home / "public_html" / "index.php").write_text("<?php echo 'hi'; ?>")

    outside = tmp_path / "outside_the_jail"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret, not this account's business")

    monkeypatch.setattr(settings, "home_base", str(home_base))

    import pwd as real_pwd

    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(account_home), "/usr/sbin/nologin"))
    monkeypatch.setattr(fm.pwd, "getpwnam", lambda name: fake_pw)
    monkeypatch.setattr(fm.os, "chown", lambda *a, **k: None)  # not running as root in tests

    return {"home": account_home, "outside": outside}


def test_list_dir(fake_account_home):
    result = fm.list_dir({"username": "demo1", "path": "public_html"})
    names = {e["name"] for e in result["entries"]}
    assert names == {"index.php"}


def test_read_file(fake_account_home):
    result = fm.read_file({"username": "demo1", "path": "public_html/index.php"})
    assert result["content"] == "<?php echo 'hi'; ?>"
    assert result["encoding"] == "utf-8"


def test_write_file_creates_new_file(fake_account_home):
    fm.write_file({"username": "demo1", "path": "public_html/new.txt", "content": "hello"})
    assert (fake_account_home["home"] / "public_html" / "new.txt").read_text() == "hello"


def test_write_file_creates_intermediate_dirs(fake_account_home):
    fm.write_file({"username": "demo1", "path": "a/b/c.txt", "content": "nested"})
    assert (fake_account_home["home"] / "a" / "b" / "c.txt").read_text() == "nested"


def test_mkdir(fake_account_home):
    fm.mkdir({"username": "demo1", "path": "uploads"})
    assert (fake_account_home["home"] / "uploads").is_dir()


def test_delete_file(fake_account_home):
    fm.delete({"username": "demo1", "path": "public_html/index.php"})
    assert not (fake_account_home["home"] / "public_html" / "index.php").exists()


def test_delete_refuses_home_dir_itself(fake_account_home):
    with pytest.raises(fm.FileManagerError):
        fm.delete({"username": "demo1", "path": ""})


def test_move(fake_account_home):
    fm.move({"username": "demo1", "src": "public_html/index.php", "dst": "public_html/old.php"})
    assert not (fake_account_home["home"] / "public_html" / "index.php").exists()
    assert (fake_account_home["home"] / "public_html" / "old.php").exists()


# --- the jail itself: every one of these must be rejected ---


def test_rejects_dotdot_traversal(fake_account_home):
    with pytest.raises(fm.FileManagerError):
        fm.read_file({"username": "demo1", "path": "../outside_the_jail/secret.txt"})


def test_rejects_dotdot_traversal_deeper(fake_account_home):
    with pytest.raises(fm.FileManagerError):
        fm.read_file({"username": "demo1", "path": "public_html/../../outside_the_jail/secret.txt"})


def test_rejects_absolute_path_escape(fake_account_home):
    with pytest.raises(fm.FileManagerError):
        fm.read_file({"username": "demo1", "path": str(fake_account_home["outside"] / "secret.txt")})


def test_rejects_symlink_escape_on_read(fake_account_home):
    link = fake_account_home["home"] / "escape_link"
    link.symlink_to(fake_account_home["outside"] / "secret.txt")
    with pytest.raises(fm.FileManagerError):
        fm.read_file({"username": "demo1", "path": "escape_link"})


def test_can_delete_an_escaping_symlink_it_owns(fake_account_home):
    """Deleting a symlink only removes the directory entry inside the
    account's own jail -- it never touches the target -- so this must be
    allowed even though the link points outside the jail (and reading
    through it must still be rejected, covered by the read test above)."""
    link = fake_account_home["home"] / "escape_link"
    target = fake_account_home["outside"] / "secret.txt"
    link.symlink_to(target)

    fm.delete({"username": "demo1", "path": "escape_link"})

    assert not link.exists() and not link.is_symlink()
    assert target.exists(), "deleting the symlink must never delete its target"


def test_can_rename_an_escaping_symlink_it_owns(fake_account_home):
    link = fake_account_home["home"] / "escape_link"
    link.symlink_to(fake_account_home["outside"] / "secret.txt")

    fm.move({"username": "demo1", "src": "escape_link", "dst": "renamed_link"})

    assert not link.is_symlink()
    assert (fake_account_home["home"] / "renamed_link").is_symlink()


def test_rejects_symlinked_directory_escape_on_write(fake_account_home):
    link_dir = fake_account_home["home"] / "escape_dir"
    link_dir.symlink_to(fake_account_home["outside"])
    with pytest.raises(fm.FileManagerError):
        fm.write_file({"username": "demo1", "path": "escape_dir/new_evil_file.txt", "content": "pwned"})
    assert not (fake_account_home["outside"] / "new_evil_file.txt").exists()


def test_rejects_move_destination_escape(fake_account_home):
    with pytest.raises(fm.FileManagerError):
        fm.move({"username": "demo1", "src": "public_html/index.php", "dst": "../outside_the_jail/stolen.php"})


def test_write_file_size_limit_enforced(fake_account_home, monkeypatch):
    monkeypatch.setattr(fm, "MAX_WRITE_BYTES", 10)
    with pytest.raises(fm.FileManagerError):
        fm.write_file({"username": "demo1", "path": "too_big.txt", "content": "this is way more than 10 bytes"})


def test_read_file_size_limit_enforced(fake_account_home, monkeypatch):
    big = fake_account_home["home"] / "public_html" / "big.txt"
    big.write_text("x" * 1000)
    monkeypatch.setattr(fm, "MAX_READ_BYTES", 10)
    with pytest.raises(fm.FileManagerError):
        fm.read_file({"username": "demo1", "path": "public_html/big.txt"})


def test_read_binary_file_returns_base64(fake_account_home):
    binary_path = fake_account_home["home"] / "public_html" / "image.bin"
    binary_path.write_bytes(bytes(range(256)))
    result = fm.read_file({"username": "demo1", "path": "public_html/image.bin"})
    assert result["encoding"] == "base64"
    import base64

    assert base64.b64decode(result["content"]) == bytes(range(256))
