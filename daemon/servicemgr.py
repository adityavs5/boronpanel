"""Phase 5 feature 2: service manager.

Start/stop/restart/reload the hosting stack's own system services via
systemctl. The service registry below is a closed allowlist, not a
passthrough of whatever unit name a caller supplies -- this is the actual
enforcement of "never allow stopping the panel's own process via UI"
(ARCHITECTURE.md's forgehost-api.service/forgehost-provisiond.service are
simply not addressable through this feature at all, not merely rejected
after the fact) and of "no shell command built from unvalidated input"
(daemon/procutil.py's own hard rule) applied to systemctl specifically.
"""
from __future__ import annotations

from shared.validation import ValidationError

from daemon.procutil import run

# Real unit names, not the friendly key -- discovered by reading each
# service's actual systemd unit (`systemctl show -p ExecStart,ExecReload`)
# rather than assumed. Two are not the "obvious" name:
#   - postfix: `postfix.service` itself is a dummy oneshot wrapper
#     (`ExecStart=/bin/true`, confirmed live) that only `Wants=` the real
#     unit -- controlling it does nothing. `postfix@-.service` is the
#     actual running multi-instance template unit.
#   - pure-ftpd: unit is named `pure-ftpd.service` (no hyphen variant
#     mismatch), included here for completeness of the mapping.
SERVICE_REGISTRY = {
    "ols": "lshttpd.service",
    "postfix": "postfix@-.service",
    "dovecot": "dovecot.service",
    "powerdns": "pdns.service",
    "mariadb": "mariadb.service",
    "pureftpd": "pure-ftpd.service",
}

# Actions that mutate service state and therefore require an explicit
# `confirm=true` from the caller (goal's own "confirm before stop/
# restart" requirement) -- enforced here, not just in the UI, since a
# direct API/bearer-token caller bypasses any client-side confirm dialog
# entirely.
CONFIRM_REQUIRED_ACTIONS = {"stop", "restart"}
ALLOWED_ACTIONS = {"start", "stop", "restart", "reload"}


def _resolve_unit(service_key: str) -> str:
    unit = SERVICE_REGISTRY.get(service_key)
    if unit is None:
        raise ValidationError(
            f"'{service_key}' is not a manageable service (allowed: {sorted(SERVICE_REGISTRY)})"
        )
    return unit


def _status(unit: str) -> dict:
    active = run(["systemctl", "is-active", unit], timeout=10)
    enabled = run(["systemctl", "is-enabled", unit], timeout=10)
    return {
        "active": active.stdout.strip() or active.stderr.strip(),
        "enabled": enabled.stdout.strip() or enabled.stderr.strip(),
    }


def list_services(params: dict) -> dict:
    services = []
    for key, unit in SERVICE_REGISTRY.items():
        status = _status(unit)
        services.append({"service": key, "unit": unit, **status})
    return {"services": services}


def get_service(params: dict) -> dict:
    service_key = params["service"]
    unit = _resolve_unit(service_key)
    status = _status(unit)
    logs = run(["journalctl", "-u", unit, "-n", "50", "--no-pager", "-o", "short-iso"], timeout=15)
    log_lines = logs.stdout.splitlines() if logs.ok else []
    return {"service": service_key, "unit": unit, **status, "log_lines": log_lines}


def control_service(params: dict) -> dict:
    service_key = params["service"]
    action = params["action"]
    confirm = bool(params.get("confirm", False))

    unit = _resolve_unit(service_key)
    if action not in ALLOWED_ACTIONS:
        raise ValidationError(f"action must be one of {sorted(ALLOWED_ACTIONS)}")
    if action in CONFIRM_REQUIRED_ACTIONS and not confirm:
        raise ValidationError(f"'{action}' on '{service_key}' requires confirm=true")

    result = run(["systemctl", action, unit], timeout=60)
    if not result.ok:
        raise RuntimeError(
            f"systemctl {action} {unit} failed (rc={result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    status = _status(unit)
    return {"service": service_key, "unit": unit, "action": action, **status}
