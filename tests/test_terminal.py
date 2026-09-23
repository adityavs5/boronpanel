"""Phase 8 feature 7: web terminal — daemon session logic + WS protocol.

The live SSH path (connects as the account user, no sudo, key revoked on close)
was verified end-to-end against this server's real sshd; these cover the
provisioning/lifecycle logic and the WebSocket message protocol.
"""
import io
import os

import paramiko
import pytest
from sqlalchemy import select

from daemon import terminal
from daemon.safeio import UnsafePathError
from shared.db import write_session
from shared.models import Account


# --- pure key/marker helpers -----------------------------------------------


def test_generate_keypair_round_trips_through_paramiko():
    priv, pub = terminal.generate_keypair()
    assert pub.startswith("ssh-ed25519 ")
    assert "BEGIN OPENSSH PRIVATE KEY" in priv
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(priv))
    assert f"{key.get_name()} {key.get_base64()}" == pub


def test_prune_and_count_reaps_stale_keeps_user_keys():
    _, pub = terminal.generate_keypair()
    now = 1_751_800_000
    fresh = terminal.build_authorized_line(pub, "s1", now - 60)
    stale = terminal.build_authorized_line(pub, "s2", now - 20 * 3600)
    user_key = "ssh-rsa AAAAuser user@host"
    kept, active = terminal.prune_and_count([fresh, stale, user_key], now)
    assert active == 1  # only the fresh terminal marker counts
    assert user_key in kept  # user's own key untouched
    assert stale not in kept  # stale terminal key reaped


def test_remove_session_line_targets_only_its_session():
    _, pub = terminal.generate_keypair()
    a = terminal.build_authorized_line(pub, "aaa", 1)
    b = terminal.build_authorized_line(pub, "bbb", 1)
    user_key = "ssh-rsa AAAAuser user@host"
    remaining = terminal.remove_session_line([a, b, user_key], "aaa")
    assert a not in remaining
    assert b in remaining and user_key in remaining


def test_terminal_authorized_key_is_limited_to_localhost():
    _, pub = terminal.generate_keypair()
    line = terminal.build_authorized_line(pub, "aaa", 1)
    assert line.startswith('from="127.0.0.1",')
    assert "no-port-forwarding" in line
    assert "no-user-rc" in line


def test_terminal_marker_parser_keeps_user_key_comments():
    """A normal user SSH key comment must not be treated as Boron's marker."""
    user_key = "ssh-rsa AAAAuser user@host boron-terminal-aaa-1"
    kept, active = terminal.prune_and_count([user_key], 1_751_800_000)
    assert kept == [user_key]
    assert active == 0
    assert terminal.remove_session_line([user_key], "aaa") == [user_key]


def test_legacy_terminal_markers_are_still_cleaned_up():
    _, pub = terminal.generate_keypair()
    legacy = f"no-agent-forwarding,no-port-forwarding,no-X11-forwarding {pub} boron-terminal-old-1"
    kept, active = terminal.prune_and_count([legacy], 1_751_800_000)
    assert kept == []
    assert active == 0


# --- open/close/list lifecycle (against a tmp authorized_keys) --------------


@pytest.fixture()
def term_env(isolated_db, monkeypatch, tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ak = ssh_dir / "authorized_keys"
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(terminal, "_ssh_paths", lambda username: (str(ssh_dir), str(ak), uid, gid))
    shell = {"value": terminal.sysops.NOLOGIN_SHELL}
    monkeypatch.setattr(terminal.sysops, "get_shell", lambda u: shell["value"])
    monkeypatch.setattr(terminal.sysops, "set_shell", lambda u, s: shell.update(value=s))
    with write_session() as db:
        db.add(Account(username="demo1", status="active", uid=5001, gid=5001))
    return {"ak": ak, "shell": shell}


def test_open_session_injects_key_and_upgrades_shell(term_env):
    result = terminal.open_session({"username": "demo1"})
    assert result["private_key"].startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert result["shell_upgraded"] is True
    assert term_env["shell"]["value"] == terminal.sysops.LOGIN_SHELL
    text = term_env["ak"].read_text()
    assert result["session_id"] in text
    assert "no-port-forwarding" in text  # forwarding disabled, pty allowed


def test_open_session_enforces_max_concurrent(term_env):
    for _ in range(terminal.MAX_CONCURRENT_SESSIONS):
        terminal.open_session({"username": "demo1"})
    with pytest.raises(RuntimeError, match="maximum"):
        terminal.open_session({"username": "demo1"})


def test_close_session_removes_key_and_reverts_shell(term_env):
    opened = terminal.open_session({"username": "demo1"})
    terminal.close_session({"username": "demo1", "session_id": opened["session_id"]})
    text = term_env["ak"].read_text()
    assert opened["session_id"] not in text
    # No keys left at all -> shell reverted to nologin (symmetric w/ sshkeys).
    assert term_env["shell"]["value"] == terminal.sysops.NOLOGIN_SHELL


def test_close_keeps_shell_when_other_keys_remain(term_env):
    # A user key present -> closing the terminal must NOT revert the shell.
    ak = term_env["ak"]
    ak.parent.mkdir(exist_ok=True)
    ak.write_text("ssh-rsa AAAAuserkey user@host\n")
    opened = terminal.open_session({"username": "demo1"})
    terminal.close_session({"username": "demo1", "session_id": opened["session_id"]})
    assert term_env["shell"]["value"] == terminal.sysops.LOGIN_SHELL  # stays up
    assert "AAAAuserkey" in ak.read_text()


def test_list_sessions(term_env):
    terminal.open_session({"username": "demo1"})
    terminal.open_session({"username": "demo1"})
    result = terminal.list_sessions({"username": "demo1"})
    assert result["active"] == 2
    assert result["max"] == terminal.MAX_CONCURRENT_SESSIONS


def test_open_rejects_suspended_account(term_env):
    with write_session() as db:
        db.scalar(select(Account).where(Account.username == "demo1")).status = "suspended"
    with pytest.raises(RuntimeError, match="status"):
        terminal.open_session({"username": "demo1"})


def test_open_refuses_preplanted_ssh_directory_symlink(isolated_db, monkeypatch, tmp_path):
    """Root terminal provisioning must never chown a symlink target."""
    target = tmp_path / "target"
    target.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".ssh").symlink_to(target, target_is_directory=True)
    uid, gid = os.getuid(), os.getgid()
    monkeypatch.setattr(terminal, "_ssh_paths", lambda username: (str(home / ".ssh"), str(home / ".ssh" / "authorized_keys"), uid, gid))
    with write_session() as db:
        db.add(Account(username="demo1", status="active", uid=5001, gid=5001))
    with pytest.raises(UnsafePathError):
        terminal.open_session({"username": "demo1"})
    assert target.stat().st_uid == os.getuid()


def test_write_lines_removes_temp_file_on_short_write(tmp_path, monkeypatch):
    dir_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    monkeypatch.setattr(terminal.os, "write", lambda _fd, _data: 0)
    try:
        with pytest.raises(OSError, match="short write"):
            terminal._write_lines(dir_fd, ["ssh-rsa AAAAuser user@host"], os.getuid(), os.getgid())
    finally:
        os.close(dir_fd)
    assert not list(tmp_path.glob(".authorized_keys.tmp.*"))


# --- WebSocket client protocol ---------------------------------------------


def test_parse_client_message_input():
    from api.routers.terminal import parse_client_message
    assert parse_client_message('{"t":"i","d":"ls\\n"}') == ("input", "ls\n")


def test_parse_client_message_resize():
    from api.routers.terminal import parse_client_message
    assert parse_client_message('{"t":"r","c":120,"r":40}') == ("resize", 120, 40)


def test_parse_client_message_raw_fallback():
    from api.routers.terminal import parse_client_message
    assert parse_client_message("not json") == ("input", "not json")


def test_parse_client_message_unknown_type_ignored():
    from api.routers.terminal import parse_client_message
    assert parse_client_message('{"t":"x"}') == ("ignore",)


def test_parse_client_message_bounds_paste_and_resize():
    from api.routers.terminal import MAX_INPUT_CHARS, MAX_RESIZE_COLUMNS, MAX_RESIZE_ROWS, parse_client_message

    assert parse_client_message("x" * (MAX_INPUT_CHARS + 1)) == ("input", "x" * MAX_INPUT_CHARS)
    assert parse_client_message('{"t":"r","c":99999,"r":99999}') == (
        "resize",
        MAX_RESIZE_COLUMNS,
        MAX_RESIZE_ROWS,
    )
