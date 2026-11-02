"""Ordinary caller mistakes reach the API as 400/404/409 with their message;
only real internal failures become the opaque 502 "internal operation
failure" (live finding: not-found/already-exists/limit errors raised as
RuntimeError or module-specific Exception subclasses all surfaced as 502)."""
import asyncio

import pytest
from fastapi import HTTPException

from api import rpc as api_rpc
from api.security import Identity
from daemon import server
from shared.failures import OperationFailure
from shared.rpc import RpcError
from shared.validation import BusyError, ConflictError, NotFoundError, ValidationError


@pytest.fixture
def synchronous_dispatch(monkeypatch):
    def run_immediately(loop, _executor, handler, params):
        future = loop.create_future()
        try:
            future.set_result(handler(params))
        except Exception as exc:
            future.set_exception(exc)
        return future

    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", run_immediately)
    from daemon.rpc_authority import Principal
    monkeypatch.setattr(server, "resolve_principal", lambda _: Principal("admin", "admin", None, 1, "session"))
    monkeypatch.setattr(server.audit, "record", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(server.POLICY_BY_OPERATION, "test.user_error", "global_admin")


def test_user_error_types_keep_historic_catch_contracts():
    # Restore/retry paths catch RuntimeError for "already exists"; every
    # existing `except ValidationError` must keep treating them as user errors.
    for kind in (NotFoundError, ConflictError, BusyError):
        assert issubclass(kind, ValidationError)
        assert issubclass(kind, RuntimeError)
    assert issubclass(BusyError, ConflictError)


@pytest.mark.parametrize("exception,code", [
    (NotFoundError("account 'ghost' not found"), "not_found"),
    (ConflictError("account 'demo' already exists"), "conflict"),
    (BusyError("A DNS backup, restore or management operation is in progress. Try again shortly."), "conflict"),
    (ValidationError("reseller account limit reached"), "bad_request"),
    (ValueError("invalid request"), "bad_request"),
    (OperationFailure({"reference": "0123456789abcdef"}), "internal_error"),
    (RuntimeError("vendor output"), "internal_error"),
])
def test_daemon_wire_code_for_each_error_kind(exception, code):
    assert server.rpc_error_code(exception) == code


@pytest.mark.parametrize("exception,kind", [
    (NotFoundError("plan 999999 not found"), NotFoundError),
    (ConflictError("database 'demo_shop' already exists"), ConflictError),
])
def test_dispatch_returns_user_errors_with_their_message(synchronous_dispatch, monkeypatch, exception, kind):
    def fail(_params):
        raise exception

    monkeypatch.setitem(server.OP_TABLE, "test.user_error", fail)
    with pytest.raises(kind, match=str(exception)) as raised:
        asyncio.run(server.dispatch("test.user_error", {}))
    assert server.rpc_error_code(raised.value) in ("not_found", "conflict")


def test_dispatch_redaction_keeps_the_not_found_status(synchronous_dispatch, monkeypatch):
    def fail(_params):
        raise NotFoundError("no mailbox for password Hunter2Secret!")

    monkeypatch.setitem(server.OP_TABLE, "test.user_error", fail)
    with pytest.raises(NotFoundError) as raised:
        asyncio.run(server.dispatch("test.user_error", {"password": "Hunter2Secret!"}))
    assert "Hunter2Secret!" not in str(raised.value)
    assert server.rpc_error_code(raised.value) == "not_found"


def test_dispatch_still_hides_internal_failures(synchronous_dispatch, monkeypatch):
    def fail(_params):
        raise RuntimeError("mysqld: Access denied for user 'root'@'localhost' (password: GeneratedSecret)")

    monkeypatch.setitem(server.OP_TABLE, "test.user_error", fail)
    with pytest.raises(OperationFailure) as raised:
        asyncio.run(server.dispatch("test.user_error", {}))
    assert "GeneratedSecret" not in str(raised.value)
    assert server.rpc_error_code(raised.value) == "internal_error"


@pytest.mark.parametrize("code,status", [
    ("bad_request", 400), ("not_found", 404), ("conflict", 409),
    ("internal_error", 502), ("something_new", 502),
])
def test_api_maps_daemon_codes_to_http_status(monkeypatch, code, status):
    class Client:
        def call(self, *_args, **_kwargs):
            raise RpcError(code, "account 'ghost' not found")

    monkeypatch.setattr(api_rpc, "_client", Client())
    identity = Identity(1, "admin", "admin", None, "session", rpc_credential="opaque-session")
    with pytest.raises(HTTPException) as raised:
        api_rpc.call_daemon("account.get", identity, username="ghost")
    assert raised.value.status_code == status
    assert raised.value.detail == "account 'ghost' not found"


def test_handlers_raise_typed_user_errors(isolated_db):
    from daemon import handlers_account, handlers_auth, plans

    with pytest.raises(NotFoundError, match="account 'ghost' not found"):
        handlers_account.get_account({"username": "ghost"})
    with pytest.raises(NotFoundError, match="plan 999999 not found"):
        plans.get_plan({"plan_id": 999999})
    with pytest.raises(NotFoundError, match="token not found"):
        handlers_auth.revoke_api_token({"token_id": 999999})


def test_module_errors_for_caller_mistakes_are_user_errors():
    from daemon import (appinstaller, backup, cron, fileauth, gitrepo, ipban, nameservers,
                        portable_archive, resellers, sshkeys, staging)

    for kind in (resellers.ResellerError, ipban.IpBanError, sshkeys.SshKeyError,
                 nameservers.NameserverError, cron.CronError, fileauth.FileAuthError,
                 gitrepo.GitRepoError, staging.StagingError, backup.BackupError,
                 portable_archive.PortableArchiveError, appinstaller.AppInstallError):
        assert issubclass(kind, ValidationError), kind
    # crontab(1) failing is not the caller's mistake.
    assert not issubclass(cron.CrontabCommandError, ValidationError)
    with pytest.raises(cron.CronError, match="not a valid 5-field cron expression"):
        cron._validate_schedule("61 * * * *")
