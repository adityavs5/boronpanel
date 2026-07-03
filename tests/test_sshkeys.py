import os
import pwd as real_pwd
import subprocess

import pytest

from daemon import sshkeys
from shared.validation import ValidationError


def _generate_key(tmp_path, name="testkey", comment="test@example.com"):
    key_path = tmp_path / name
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", comment, "-q"],
        check=True,
    )
    return (tmp_path / f"{name}.pub").read_text().strip()


@pytest.fixture()
def account_with_home(tmp_path, monkeypatch):
    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(sshkeys.settings, "home_base", str(home_base))

    account_home = home_base / "demo1"
    account_home.mkdir()

    shells = {"demo1": sshkeys.sysops.NOLOGIN_SHELL}

    def fake_get_shell(username):
        return shells[username]

    def fake_set_shell(username, shell):
        assert shell in (sshkeys.sysops.NOLOGIN_SHELL, sshkeys.sysops.LOGIN_SHELL)
        shells[username] = shell

    monkeypatch.setattr(sshkeys.sysops, "get_shell", fake_get_shell)
    monkeypatch.setattr(sshkeys.sysops, "set_shell", fake_set_shell)

    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(account_home), sshkeys.sysops.NOLOGIN_SHELL))
    monkeypatch.setattr(sshkeys.pwd, "getpwnam", lambda name: fake_pw)

    return {"home": account_home, "shells": shells}


def test_add_key_creates_authorized_keys_with_correct_permissions(account_with_home, tmp_path):
    key = _generate_key(tmp_path)
    result = sshkeys.add_key({"username": "demo1", "key": key})
    assert result["status"] == "added"
    assert result["type"] == "ED25519"
    assert result["comment"] == "test@example.com"

    ak_path = account_with_home["home"] / ".ssh" / "authorized_keys"
    assert ak_path.exists()
    assert oct(os.stat(ak_path).st_mode & 0o777) == "0o600"
    ssh_dir = account_with_home["home"] / ".ssh"
    assert oct(os.stat(ssh_dir).st_mode & 0o777) == "0o700"
    assert key in ak_path.read_text()


def test_add_key_upgrades_shell_from_nologin(account_with_home, tmp_path):
    key = _generate_key(tmp_path)
    assert account_with_home["shells"]["demo1"] == sshkeys.sysops.NOLOGIN_SHELL
    sshkeys.add_key({"username": "demo1", "key": key})
    assert account_with_home["shells"]["demo1"] == sshkeys.sysops.LOGIN_SHELL


def test_add_key_rejects_malformed_key(account_with_home):
    with pytest.raises(ValidationError):
        sshkeys.add_key({"username": "demo1", "key": "not-a-real-key AAAA"})


def test_add_key_rejects_duplicate(account_with_home, tmp_path):
    key = _generate_key(tmp_path)
    sshkeys.add_key({"username": "demo1", "key": key})
    with pytest.raises(ValidationError):
        sshkeys.add_key({"username": "demo1", "key": key})


def test_add_key_duplicate_detected_even_with_different_comment(account_with_home, tmp_path):
    key = _generate_key(tmp_path)
    sshkeys.add_key({"username": "demo1", "key": key})
    key_type, blob, _comment = key.split(None, 2)
    same_key_new_comment = f"{key_type} {blob} someone-else@otherhost"
    with pytest.raises(ValidationError):
        sshkeys.add_key({"username": "demo1", "key": same_key_new_comment})


def test_list_keys_empty_when_no_file(account_with_home):
    assert sshkeys.list_keys({"username": "demo1"})["keys"] == []


def test_add_list_delete_key_cycle(account_with_home, tmp_path):
    key1 = _generate_key(tmp_path, "key1", "alice@laptop")
    key2 = _generate_key(tmp_path, "key2", "alice@desktop")

    r1 = sshkeys.add_key({"username": "demo1", "key": key1})
    r2 = sshkeys.add_key({"username": "demo1", "key": key2})

    keys = sshkeys.list_keys({"username": "demo1"})["keys"]
    assert {k["comment"] for k in keys} == {"alice@laptop", "alice@desktop"}

    sshkeys.delete_key({"username": "demo1", "fingerprint": r1["fingerprint"]})
    remaining = sshkeys.list_keys({"username": "demo1"})["keys"]
    assert len(remaining) == 1
    assert remaining[0]["comment"] == "alice@desktop"
    # shell must still allow login -- one key remains
    assert account_with_home["shells"]["demo1"] == sshkeys.sysops.LOGIN_SHELL

    sshkeys.delete_key({"username": "demo1", "fingerprint": r2["fingerprint"]})
    assert sshkeys.list_keys({"username": "demo1"})["keys"] == []
    # last key removed -- shell must revert to nologin
    assert account_with_home["shells"]["demo1"] == sshkeys.sysops.NOLOGIN_SHELL


def test_delete_key_rejects_unknown_fingerprint(account_with_home, tmp_path):
    key = _generate_key(tmp_path)
    sshkeys.add_key({"username": "demo1", "key": key})
    with pytest.raises(sshkeys.SshKeyError):
        sshkeys.delete_key({"username": "demo1", "fingerprint": "SHA256:doesnotexist"})


def test_delete_key_rejects_when_no_keys_file(account_with_home):
    with pytest.raises(sshkeys.SshKeyError):
        sshkeys.delete_key({"username": "demo1", "fingerprint": "SHA256:whatever"})


def test_add_key_rejects_private_key_input(account_with_home, tmp_path):
    key_path = tmp_path / "privtest"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"], check=True)
    private_key_text = key_path.read_text().splitlines()[0]  # "-----BEGIN OPENSSH PRIVATE KEY-----"
    with pytest.raises(ValidationError):
        sshkeys.add_key({"username": "demo1", "key": private_key_text})
