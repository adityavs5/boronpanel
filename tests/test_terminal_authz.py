"""Cross-account authorization for the web terminal (Phase 8 f7). The
terminal grants a real interactive shell as the account's Linux user, so
_authorized() is the gate that must keep one customer out of another's shell.
Previously it had zero test coverage."""
from api.routers import terminal
from api.security import Identity
import asyncio
from starlette.datastructures import Headers, URL


def _identity(role, account_id):
    return Identity(panel_user_id=1, username="panellogin", role=role, account_id=account_id, auth_method="session")


def _add_account(username, uid):
    from shared.db import write_session
    from shared.models import Account

    with write_session() as db:
        acct = Account(username=username, uid=uid, gid=uid, status="active")
        db.add(acct)
        db.flush()
        return acct.id


def test_admin_authorized_for_any_account(isolated_db):
    _add_account("alice", 6001)
    assert terminal._authorized(_identity("admin", None), "alice") is True


def test_customer_authorized_for_own_account(isolated_db):
    account_id = _add_account("alice", 6001)
    assert terminal._authorized(_identity("customer", account_id), "alice") is True


def test_customer_denied_for_other_account(isolated_db):
    alice_id = _add_account("alice", 6001)
    _add_account("bob", 6002)
    # Alice's session must not be able to open Bob's terminal.
    assert terminal._authorized(_identity("customer", alice_id), "bob") is False


def test_customer_with_no_account_denied(isolated_db):
    _add_account("alice", 6001)
    assert terminal._authorized(_identity("customer", None), "alice") is False


def test_customer_with_stale_account_id_denied(isolated_db):
    # account_id pointing at a row that no longer exists must not authorize.
    assert terminal._authorized(_identity("customer", 999999), "alice") is False


def test_terminal_websocket_rejects_foreign_origin_before_identity(monkeypatch):
    monkeypatch.setattr(terminal, "_ws_identity", lambda _: (_ for _ in ()).throw(AssertionError("identity reached")))

    class Socket:
        url = URL("wss://panel.example:2222/ws/accounts/alice/terminal")
        def __init__(self, origin):
            self.headers = Headers({"origin": origin}) if origin else Headers({})
            self.closed = None
        async def close(self, code):
            self.closed = code

    for origin in ("https://hosted.example", "https://panel.example:2222.evil.example", None):
        socket = Socket(origin)
        asyncio.run(terminal.terminal_ws(socket, "alice"))
        assert socket.closed == 4403


def test_terminal_websocket_same_origin_matches_exact_host_and_port():
    class Socket:
        url = URL("wss://panel.example:2222/ws/accounts/alice/terminal")
        headers = Headers({"origin": "https://panel.example:2222"})
    assert terminal._same_origin(Socket()) is True


def test_terminal_uses_quiet_interactive_shell(monkeypatch):
    """The browser terminal must not replay the SSH login MOTD/server facts."""
    observed = {}

    class Channel:
        def get_pty(self, **kwargs):
            observed["pty"] = kwargs

        def exec_command(self, command):
            observed["command"] = command

        def settimeout(self, timeout):
            observed["timeout"] = timeout

    class Transport:
        def open_session(self):
            observed["session"] = True
            return Channel()

    class Client:
        def get_host_keys(self):
            return terminal.paramiko.HostKeys()

        def set_missing_host_key_policy(self, _policy):
            assert isinstance(_policy, terminal.paramiko.RejectPolicy)

        def connect(self, *args, **kwargs):
            observed["connect"] = (args, kwargs)

        def get_transport(self):
            return Transport()

    monkeypatch.setattr(terminal.paramiko, "SSHClient", Client)
    monkeypatch.setattr(terminal, "_local_host_keys", lambda: [terminal.paramiko.RSAKey.generate(1024)])
    monkeypatch.setattr(terminal.paramiko.Ed25519Key, "from_private_key", lambda _stream: object())

    _, channel = terminal._connect_ssh("alice", "key", "127.0.0.1", 22, quiet=True)

    assert observed["session"] is True
    assert observed["pty"]["term"] == "xterm-256color"
    assert observed["command"] == terminal.INTERACTIVE_SHELL_COMMAND
    assert "PS1='\\u@\\h:\\w\\$ '" in observed["command"]
    assert observed["timeout"] == terminal._RECV_TIMEOUT
    assert isinstance(channel, Channel)


def test_local_ssh_host_keys_are_pinned(tmp_path, monkeypatch):
    key = terminal.paramiko.RSAKey.generate(1024)
    (tmp_path / 'ssh_host_rsa_key.pub').write_text(f'{key.get_name()} {key.get_base64()} fixture\n')
    monkeypatch.setattr(terminal, 'SSH_HOST_KEY_DIR', tmp_path)
    assert terminal._local_host_keys()[0] == key


def test_missing_host_keys_and_remote_terminal_fail_closed(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(terminal, 'SSH_HOST_KEY_DIR', tmp_path)
    with pytest.raises(RuntimeError, match='No trusted'):
        terminal._connect_ssh('alice', 'unused', '127.0.0.1', 22)
    with pytest.raises(RuntimeError, match='local server'):
        terminal._connect_ssh('alice', 'unused', 'example.com', 22)
