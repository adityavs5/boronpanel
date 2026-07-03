import pytest

from daemon import servicemgr
from daemon.procutil import ProcResult
from shared.validation import ValidationError


def _fake_run_factory(responses):
    """responses: dict mapping the first two argv tokens (e.g. ('systemctl', 'is-active')) to a ProcResult."""

    def fake_run(args, timeout=30, **kwargs):
        key = tuple(args[:2])
        if key in responses:
            return responses[key]
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    return fake_run


def test_service_registry_never_includes_panel_processes():
    assert "forgehost-api" not in servicemgr.SERVICE_REGISTRY
    assert "forgehost-provisiond" not in servicemgr.SERVICE_REGISTRY
    for unit in servicemgr.SERVICE_REGISTRY.values():
        assert "forgehost" not in unit


def test_resolve_unit_rejects_unknown_service():
    with pytest.raises(ValidationError):
        servicemgr._resolve_unit("forgehost-api")
    with pytest.raises(ValidationError):
        servicemgr._resolve_unit("nonexistent")


def test_list_services_reports_every_registered_service(monkeypatch):
    monkeypatch.setattr(
        servicemgr,
        "run",
        _fake_run_factory(
            {
                ("systemctl", "is-active"): ProcResult(args=[], returncode=0, stdout="active\n", stderr=""),
                ("systemctl", "is-enabled"): ProcResult(args=[], returncode=0, stdout="enabled\n", stderr=""),
            }
        ),
    )
    result = servicemgr.list_services({})
    keys = {s["service"] for s in result["services"]}
    assert keys == set(servicemgr.SERVICE_REGISTRY)
    assert all(s["active"] == "active" for s in result["services"])


def test_get_service_includes_last_log_lines(monkeypatch):
    def fake_run(args, timeout=30, **kwargs):
        if args[0] == "journalctl":
            return ProcResult(args=args, returncode=0, stdout="line1\nline2\n", stderr="")
        if args[1] == "is-active":
            return ProcResult(args=args, returncode=0, stdout="active\n", stderr="")
        return ProcResult(args=args, returncode=0, stdout="enabled\n", stderr="")

    monkeypatch.setattr(servicemgr, "run", fake_run)
    result = servicemgr.get_service({"service": "ols"})
    assert result["unit"] == "lshttpd.service"
    assert result["log_lines"] == ["line1", "line2"]


def test_control_service_rejects_stop_without_confirm(monkeypatch):
    monkeypatch.setattr(servicemgr, "run", _fake_run_factory({}))
    with pytest.raises(ValidationError):
        servicemgr.control_service({"service": "dovecot", "action": "stop", "confirm": False})


def test_control_service_rejects_restart_without_confirm(monkeypatch):
    monkeypatch.setattr(servicemgr, "run", _fake_run_factory({}))
    with pytest.raises(ValidationError):
        servicemgr.control_service({"service": "dovecot", "action": "restart"})


def test_control_service_allows_start_without_confirm(monkeypatch):
    calls = []

    def fake_run(args, timeout=30, **kwargs):
        calls.append(args)
        if args[0] == "systemctl" and args[1] not in ("is-active", "is-enabled"):
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        return ProcResult(args=args, returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr(servicemgr, "run", fake_run)
    result = servicemgr.control_service({"service": "mariadb", "action": "start"})
    assert result["action"] == "start"
    assert ["systemctl", "start", "mariadb.service"] in calls


def test_control_service_allows_stop_with_confirm(monkeypatch):
    def fake_run(args, timeout=30, **kwargs):
        if args[0] == "systemctl" and args[1] not in ("is-active", "is-enabled"):
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        return ProcResult(args=args, returncode=0, stdout="inactive\n", stderr="")

    monkeypatch.setattr(servicemgr, "run", fake_run)
    result = servicemgr.control_service({"service": "dovecot", "action": "stop", "confirm": True})
    assert result["action"] == "stop"


def test_control_service_rejects_unknown_action(monkeypatch):
    monkeypatch.setattr(servicemgr, "run", _fake_run_factory({}))
    with pytest.raises(ValidationError):
        servicemgr.control_service({"service": "dovecot", "action": "delete", "confirm": True})


def test_control_service_raises_on_systemctl_failure(monkeypatch):
    def fake_run(args, timeout=30, **kwargs):
        if args[0] == "systemctl" and args[1] == "restart":
            return ProcResult(args=args, returncode=1, stdout="", stderr="Unit not found")
        return ProcResult(args=args, returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr(servicemgr, "run", fake_run)
    with pytest.raises(RuntimeError):
        servicemgr.control_service({"service": "ols", "action": "restart", "confirm": True})
