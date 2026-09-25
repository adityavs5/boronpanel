"""Phase 5 feature 4: firewall UI (UFW). XHIGH effort per the goal --
misconfiguring a live firewall can lock out SSH/the panel itself, so the
hard-protection rules below are enforced here (the actual privileged
process), not merely presented as a UI hint a direct API/bearer-token
caller could skip.

Every mutation goes through `ufw`'s own CLI (never raw iptables), the
same "use the target tool's own stable interface, not its internal
format" principle this project already applies to Postfix's `mailq`
(Phase 5 feature 3) and PowerDNS's REST API (Phase c) -- `ufw`'s rule
storage format is not a documented stable interface, its command syntax
is.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from shared.config import settings
from shared.validation import ValidationError, validate_ip_or_cidr

from daemon.procutil import run

ALLOWED_PROTOCOLS = {"tcp", "udp", "any"}
ALLOWED_ACTIONS = {"allow", "deny"}

# Matches "ufw show added"'s two real line shapes, confirmed against this
# server's own actual output before writing this regex (a plain
# port/proto rule, and one scoped `from <ip> to any port <port> proto
# <proto>`) -- not assumed from `ufw`'s man page alone.
_PLAIN_RULE_RE = re.compile(
    r"\Aufw (?P<action>allow|deny) (?P<port>\d+)(?:/(?P<proto>tcp|udp))?(?:\s+comment\s+'(?P<comment>.*)')?\Z"
)
_SCOPED_RULE_RE = re.compile(
    r"\Aufw (?P<action>allow|deny) from (?P<from>\S+) to any port (?P<port>\d+)"
    r"(?:\s+proto\s+(?P<proto>tcp|udp))?(?:\s+comment\s+'(?P<comment>.*)')?\Z"
)
_BYPASS_RULE_RE = re.compile(
    r"\Aufw allow from (?P<from>\S+)(?:\s+comment\s+'(?P<comment>.*)')?\Z"
)
BYPASS_COMMENT = "boron-full-access-bypass"
CHANGE_CONFIRM_SECONDS = 120
_change_lock = threading.RLock()
_rollback_timer: threading.Timer | None = None


def _ssh_ports() -> set[int]:
    """Read effective SSH configuration, including drop-ins and extra ports.

    Guessing port 22 on a probe failure is unsafe before activating a default
    deny firewall. Refuse the operation until the effective config is known.
    """
    from daemon.procutil import run as probe
    result = probe(['/usr/sbin/sshd', '-T'], timeout=10)
    result.raise_if_failed('Read effective SSH configuration')
    ports = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].lower() == 'port':
            ports.add(_validate_port(fields[1]))
    if not ports:
        raise RuntimeError('Cannot determine SSH listener ports; firewall changes refused')
    return ports


def protected_ports() -> set[int]:
    from shared.panel_ports import listener_ports
    return {*_ssh_ports(), *listener_ports(), 80, 443, 25, 587, 993}


def _validate_port(port) -> int:
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValidationError("port must be an integer") from None
    if not (1 <= port <= 65535):
        raise ValidationError("port must be between 1 and 65535")
    return port


def _validate_protocol(protocol: str) -> str:
    if protocol not in ALLOWED_PROTOCOLS:
        raise ValidationError(f"protocol must be one of {sorted(ALLOWED_PROTOCOLS)}")
    return protocol


def _validate_action(action: str) -> str:
    if action not in ALLOWED_ACTIONS:
        raise ValidationError(f"action must be one of {sorted(ALLOWED_ACTIONS)}")
    return action


def _validate_from_addr(from_addr: str | None) -> str:
    if not from_addr or from_addr.strip().lower() == "any":
        return "any"
    return validate_ip_or_cidr(from_addr)


_COMMENT_RE = re.compile(r"\A[A-Za-z0-9 ._-]{0,200}\Z")


def _validate_comment(comment: str | None) -> str:
    comment = (comment or "").strip()
    if comment and not _COMMENT_RE.match(comment):
        raise ValidationError("comment may only contain letters, digits, spaces, '.', '_', '-'")
    return comment


def _rule_spec_args(action: str, port: int, protocol: str, from_addr: str) -> list[str]:
    if from_addr == "any":
        port_spec = f"{port}/{protocol}" if protocol != "any" else str(port)
        return [action, port_spec]
    args = [action, "from", from_addr, "to", "any", "port", str(port)]
    if protocol != "any":
        args += ["proto", protocol]
    return args


def _rule_id(action: str, port: int, protocol: str, from_addr: str) -> str:
    raw = f"{action}|{port}|{protocol}|{from_addr}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _bypass_id(address: str) -> str:
    return hashlib.sha256(f"bypass|{address}".encode()).hexdigest()[:16]


def _parse_bypass_rules(text: str) -> list[dict]:
    entries = []
    for line in text.splitlines():
        match = _BYPASS_RULE_RE.match(line.strip())
        if not match:
            continue
        comment = match.group("comment") or ""
        if comment != BYPASS_COMMENT and not comment.startswith(BYPASS_COMMENT + "-"):
            continue
        address = validate_ip_or_cidr(match.group("from"))
        entries.append({
            "bypass_id": _bypass_id(address),
            "address": address,
            "label": comment[len(BYPASS_COMMENT):].lstrip("-"),
        })
    return entries


def _parse_added_rules(text: str) -> list[dict]:
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Added user rules") or line == "(None)":
            continue
        match = _PLAIN_RULE_RE.match(line)
        from_addr = "any"
        if not match:
            match = _SCOPED_RULE_RE.match(line)
            if match:
                from_addr = match.group("from")
        if not match:
            continue  # a rule shape this feature didn't create (e.g. app profiles) -- not manageable here
        action = match.group("action")
        port = int(match.group("port"))
        protocol = match.group("proto") or "any"
        rules.append(
            {
                "rule_id": _rule_id(action, port, protocol, from_addr),
                "action": action,
                "port": port,
                "protocol": protocol,
                "from": from_addr,
                "comment": match.group("comment") or "",
                "protected": port in protected_ports(),
            }
        )
    return rules


def list_rules(params: dict) -> dict:
    result = run(["ufw", "show", "added"], timeout=15)
    rules = _parse_added_rules(result.stdout)
    status = run(["ufw", "status"], timeout=15)
    active = status.stdout.strip().startswith("Status: active")
    return {
        "rules": rules,
        "bypass": _parse_bypass_rules(result.stdout),
        "active": active,
        "pending_change": _public_pending(_read_pending()),
    }


def list_bypass(params: dict) -> dict:
    result = run(["ufw", "show", "added"], timeout=15)
    return {"bypass": _parse_bypass_rules(result.stdout)}


def add_bypass(params: dict) -> dict:
    address = validate_ip_or_cidr(params["address"])
    if address in ("0.0.0.0/0", "::/0"):
        raise ValidationError("a global network cannot bypass the firewall; enter a specific trusted IP or CIDR")
    label = _validate_comment(params.get("label"))
    comment = BYPASS_COMMENT + (f"-{label}" if label else "")
    current = _parse_bypass_rules(run(["ufw", "show", "added"], timeout=15).stdout)
    if any(item["address"] == address for item in current):
        raise ValidationError(f"'{address}' already has full-access bypass")
    args = ["ufw", "insert", "1", "allow", "from", address, "comment", comment]
    result = run(args, timeout=20)
    if not result.ok:
        raise RuntimeError(f"ufw bypass add failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"bypass_id": _bypass_id(address), "address": address, "label": label}


def delete_bypass(params: dict) -> dict:
    bypass_id = str(params["bypass_id"])
    current = _parse_bypass_rules(run(["ufw", "show", "added"], timeout=15).stdout)
    target = next((item for item in current if item["bypass_id"] == bypass_id), None)
    if target is None:
        raise ValidationError(f"no bypass entry with id '{bypass_id}' found")
    comment = BYPASS_COMMENT + (f"-{target['label']}" if target["label"] else "")
    result = run(
        ["ufw", "--force", "delete", "allow", "from", target["address"], "comment", comment],
        timeout=20,
    )
    if not result.ok:
        raise RuntimeError(f"ufw bypass delete failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"bypass_id": bypass_id, "status": "deleted"}


def add_rule(params: dict) -> dict:
    action = _validate_action(params["action"])
    port = _validate_port(params["port"])
    protocol = _validate_protocol(params.get("protocol", "any"))
    from_addr = _validate_from_addr(params.get("from_addr"))
    comment = _validate_comment(params.get("comment"))

    if action == "deny" and port in protected_ports():
        raise ValidationError(
            f"port {port} is hard-protected (SSH/panel/web/mail) and cannot be denied via this UI"
        )

    args = _rule_spec_args(action, port, protocol, from_addr)
    if comment:
        args += ["comment", comment]
    result = run(["ufw"] + args, timeout=20)
    if not result.ok:
        raise RuntimeError(f"ufw {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"rule_id": _rule_id(action, port, protocol, from_addr), "action": action, "port": port, "protocol": protocol, "from": from_addr}


def delete_rule(params: dict) -> dict:
    rule_id = params["rule_id"]
    current = _parse_added_rules(run(["ufw", "show", "added"], timeout=15).stdout)
    target = next((r for r in current if r["rule_id"] == rule_id), None)
    if target is None:
        raise ValidationError(f"no rule with id '{rule_id}' found")

    if target["action"] == "allow" and target["port"] in protected_ports():
        remaining = [
            r for r in current
            if r["rule_id"] != rule_id and r["action"] == "allow" and r["port"] == target["port"]
        ]
        if not remaining:
            raise ValidationError(
                f"cannot delete the only allow rule for hard-protected port {target['port']} "
                "(SSH/panel/web/mail) -- this would expose it to the default deny policy once UFW is enabled"
            )

    args = _rule_spec_args(target["action"], target["port"], target["protocol"], target["from"])
    result = run(["ufw", "--force", "delete"] + args, timeout=20)
    if not result.ok:
        raise RuntimeError(f"ufw delete failed: {result.stderr.strip() or result.stdout.strip()}")
    return {"rule_id": rule_id, "status": "deleted"}


def get_status(params: dict) -> dict:
    result = run(["ufw", "status", "verbose"], timeout=15)
    active = result.stdout.strip().startswith("Status: active")
    return {"active": active, "raw": result.stdout, "pending_change": _public_pending(_read_pending())}


# --- Phase 2+3 feature 9: Cloudflare-only web lockdown ----------------------
# CF-only mode makes ports 80/443 reachable ONLY from Cloudflare edge ranges,
# so the origin IP is useless to a direct attacker. This is the one managed
# mode allowed to touch the otherwise hard-protected web ports -- and ONLY
# 80/443: SSH and the panel port are never involved here, so a bug in this
# path can't lock the operator out of the box.
CF_LOCKDOWN_COMMENT = "boron-cf-lockdown"
CF_LOCKDOWN_PORTS = (80, 443)


def _current_rules() -> list[dict]:
    return _parse_added_rules(run(["ufw", "show", "added"], timeout=15).stdout)


def apply_cf_lockdown(cf_ranges: list[str]) -> None:
    """Scope 80/443 to the Cloudflare edge ranges: add a scoped allow per
    (port, CF cidr) tagged CF_LOCKDOWN_COMMENT, then drop the general
    allow-from-any for those ports. Idempotent -- existing scoped rules are
    kept, only missing ones added, and stale scoped rules for CIDRs no longer
    in the set are pruned (so a ranges refresh converges)."""
    if not cf_ranges:
        raise ValidationError(
            "refusing to lock down web ports with an empty Cloudflare allowlist -- "
            "run cf.refresh_ranges first so the ranges file exists"
        )
    wanted = set(cf_ranges)
    existing = _current_rules()
    have = {(r["port"], r["from"]) for r in existing if r["action"] == "allow" and r["from"] != "any"}
    for port in CF_LOCKDOWN_PORTS:
        for cidr in cf_ranges:
            if (port, cidr) not in have:
                run(["ufw", "allow", "from", cidr, "to", "any", "port", str(port), "proto", "tcp",
                     "comment", CF_LOCKDOWN_COMMENT], timeout=20).raise_if_failed("Firewall prerequisite")
    # Re-read AFTER the adds so the general-allow removal is gated on the
    # scoped rules actually being in place. Removing the general (from-any)
    # allow while no scoped CF allow exists for that port would blackhole the
    # web from everywhere -- the one lockout this safety-critical path must
    # never cause. So only drop the general allow for a port that now has at
    # least one scoped CF allow.
    current = _current_rules()
    scoped_by_port: dict[int, list[dict]] = {p: [] for p in CF_LOCKDOWN_PORTS}
    for r in current:
        if r["action"] == "allow" and r["port"] in CF_LOCKDOWN_PORTS and r["from"] != "any":
            scoped_by_port[r["port"]].append(r)
    for port in CF_LOCKDOWN_PORTS:
        if not scoped_by_port[port]:
            # scoped adds didn't take -- leave the general allow in place
            # (fail safe: web stays reachable) rather than locking everyone out.
            continue
        for r in current:
            if r["action"] == "allow" and r["port"] == port and r["from"] == "any":
                run(["ufw", "--force", "delete"] + _rule_spec_args("allow", port, r["protocol"], "any"), timeout=20)
        # prune stale scoped lockdown rules whose CIDR is no longer wanted
        for r in scoped_by_port[port]:
            if r["comment"] == CF_LOCKDOWN_COMMENT and r["from"] not in wanted:
                run(["ufw", "--force", "delete"] + _rule_spec_args("allow", port, r["protocol"], r["from"]), timeout=20)


def remove_cf_lockdown() -> None:
    """Reverse apply_cf_lockdown. Restores the general allow for 80/443 FIRST
    (so web is reachable even if the scoped-rule cleanup below fails partway),
    then deletes every scoped lockdown rule."""
    for port in CF_LOCKDOWN_PORTS:
        run(["ufw", "allow", str(port)], timeout=20).raise_if_failed("Firewall prerequisite")  # idempotent restore
    for r in _current_rules():
        if (
            r["action"] == "allow"
            and r["port"] in CF_LOCKDOWN_PORTS
            and r["from"] != "any"
            and r["comment"] == CF_LOCKDOWN_COMMENT
        ):
            run(["ufw", "--force", "delete"] + _rule_spec_args("allow", r["port"], r["protocol"], r["from"]), timeout=20)


def cf_lockdown_active() -> bool:
    return any(r.get("comment") == CF_LOCKDOWN_COMMENT for r in _current_rules())


def _ensure_baseline_allow_rules() -> None:
    """Called before ever flipping UFW to active -- adds an allow rule
    for every hard-protected port that doesn't already have one. Without
    this, enabling UFW (default policy DROP, confirmed via
    /etc/default/ufw) with no explicit allow rules yet in place would
    immediately lock out SSH/the panel/live hosted traffic the instant
    it took effect -- exactly the kind of self-inflicted outage "pick
    conservative/secure" exists to prevent, not merely a nice-to-have."""
    current = _parse_added_rules(run(["ufw", "show", "added"], timeout=15).stdout)
    covered = {r["port"] for r in current if r["action"] == "allow"}
    for port in sorted(protected_ports()):
        if port not in covered:
            run(["ufw", "allow", str(port), "comment", "boron-baseline-protected-port"], timeout=20).raise_if_failed("Firewall prerequisite")


def enable_firewall(params: dict) -> dict:
    if not bool(params.get("confirm", False)):
        raise ValidationError("enabling the firewall requires confirm=true")
    _ensure_baseline_allow_rules()
    result = run(["ufw", "--force", "enable"], timeout=20)
    if not result.ok:
        raise RuntimeError(f"ufw enable failed: {result.stderr.strip() or result.stdout.strip()}")
    return get_status({})


def disable_firewall(params: dict) -> dict:
    if not bool(params.get("confirm", False)):
        raise ValidationError("disabling the firewall requires confirm=true")
    result = run(["ufw", "disable"], timeout=20)
    if not result.ok:
        raise RuntimeError(f"ufw disable failed: {result.stderr.strip() or result.stdout.strip()}")
    return get_status({})


# --- Lockout-safe panel mutations ------------------------------------------
#
# UFW is intentionally still the source of truth; this small journal only
# records the inverse of the single panel change awaiting confirmation.  The
# confirmation secret is returned to the initiating browser once and only its
# digest is written to disk.  A daemon restart fails safe by reverting any
# pending change immediately instead of silently extending its exposure.


def _state_dir() -> Path:
    return Path(settings.firewall_state_dir)


def _state_file() -> Path:
    return _state_dir() / "pending.json"


def _write_pending(state: dict) -> None:
    directory = _state_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    target = _state_file()
    temporary = directory / f".pending-{os.getpid()}-{threading.get_ident()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_pending() -> dict | None:
    path = _state_file()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        state = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"firewall recovery journal is invalid: {path}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("undo"), list):
        raise RuntimeError(f"firewall recovery journal is invalid: {path}")
    return state


def _clear_pending() -> None:
    global _rollback_timer
    if _rollback_timer is not None:
        _rollback_timer.cancel()
        _rollback_timer = None
    state = _read_pending()
    try:
        _state_file().unlink()
    except FileNotFoundError:
        pass
    if state and state.get("timer_unit"):
        timer_unit = state["timer_unit"] + ".timer"
        run(["systemctl", "stop", timer_unit], timeout=15)
        run(["systemctl", "reset-failed", timer_unit], timeout=15)


def _public_pending(state: dict | None, *, token: str | None = None) -> dict | None:
    if state is None:
        return None
    result = {
        "change_id": state["change_id"],
        "operation": state["operation"],
        "summary": state["summary"],
        "created_at": state["created_at"],
        "expires_at": state["expires_at"],
        "stage": state.get("stage", "pending"),
    }
    if token is not None:
        result["confirmation_token"] = token
    return result


def _rule_for_id(rule_id: str) -> dict | None:
    return next((rule for rule in _current_rules() if rule["rule_id"] == rule_id), None)


def _run_checked(args: list[str], label: str) -> None:
    result = run(args, timeout=20)
    if not result.ok:
        raise RuntimeError(f"{label}: {result.stderr.strip() or result.stdout.strip()}")


def _undo_one(step: dict) -> None:
    kind = step.get("kind")
    if kind == "delete_rule":
        target = _rule_for_id(step["rule_id"])
        if target is not None:
            _run_checked(
                ["ufw", "--force", "delete"]
                + _rule_spec_args(target["action"], target["port"], target["protocol"], target["from"]),
                "firewall rollback could not delete rule",
            )
        return
    if kind == "add_rule":
        if _rule_for_id(step["rule_id"]) is None:
            add_rule(step["params"])
        return
    if kind == "delete_bypass":
        current = _parse_bypass_rules(run(["ufw", "show", "added"], timeout=15).stdout)
        target = next((entry for entry in current if entry["address"] == step["address"]), None)
        if target is not None:
            delete_bypass({"bypass_id": target["bypass_id"]})
        return
    if kind == "add_bypass":
        current = _parse_bypass_rules(run(["ufw", "show", "added"], timeout=15).stdout)
        if not any(entry["address"] == step["params"]["address"] for entry in current):
            add_bypass(step["params"])
        return
    if kind == "disable":
        disable_firewall({"confirm": True})
        return
    if kind == "enable":
        enable_firewall({"confirm": True})
        return
    raise RuntimeError(f"unsupported firewall rollback step: {kind!r}")


def _rollback_state(state: dict, *, reason: str) -> dict:
    errors: list[str] = []
    for step in state["undo"]:
        try:
            _undo_one(step)
        except Exception as exc:  # retain the journal for direct-console recovery
            errors.append(str(exc))
    if errors:
        state["stage"] = "rollback_failed"
        state["rollback_reason"] = reason
        state["rollback_errors"] = errors
        _write_pending(state)
        raise RuntimeError("firewall rollback needs local-console recovery: " + "; ".join(errors))
    _clear_pending()
    return {"status": "reverted", "change_id": state["change_id"], "reason": reason}


def _auto_rollback() -> None:
    with _change_lock:
        state = _read_pending()
        if state is None:
            return
        try:
            _rollback_state(state, reason="confirmation window expired")
        except Exception:
            # The retained 0600 journal is deliberately left for the local
            # recovery command. The daemon logger captures timer exceptions
            # poorly, so the UI also exposes rollback_failed on its next poll.
            return


def _arm_rollback(state: dict) -> None:
    global _rollback_timer
    if _rollback_timer is not None:
        _rollback_timer.cancel()
    delay = max(0.0, float(state["expires_at"]) - time.time())
    _rollback_timer = threading.Timer(delay, _auto_rollback)
    _rollback_timer.daemon = True
    _rollback_timer.start()
    # The thread gives prompt rollback while borond is healthy. The transient
    # systemd timer is the authoritative independent guard: it invokes the
    # local recovery tool even if borond or boron-api is killed mid-window.
    result = run(
        [
            "systemd-run", "--quiet", "--collect",
            "--unit", state["timer_unit"],
            f"--on-active={max(1, int(delay))}s",
            "/usr/local/sbin/boron-firewall-recover",
        ],
        timeout=20,
    )
    if not result.ok:
        _rollback_timer.cancel()
        _rollback_timer = None
        raise RuntimeError(
            "could not schedule the independent firewall rollback timer: "
            + (result.stderr.strip() or result.stdout.strip())
        )


def _verify_protected_access() -> None:
    status = run(["ufw", "status"], timeout=15)
    if not status.ok:
        raise RuntimeError("unable to verify firewall status after the change")
    if not status.stdout.strip().startswith("Status: active"):
        return
    rules = _current_rules()
    covered = {rule["port"] for rule in rules if rule["action"] == "allow"}
    missing = sorted(protected_ports() - covered)
    if missing:
        raise RuntimeError(
            "firewall verification found no allow rule for protected ports: "
            + ", ".join(str(port) for port in missing)
        )


def _start_change(operation: str, summary: str, undo: list[dict], apply) -> dict:
    with _change_lock:
        existing = _read_pending()
        if existing is not None:
            if float(existing["expires_at"]) <= time.time():
                _rollback_state(existing, reason="expired before the next change")
            else:
                raise ValidationError(
                    "confirm or revert the pending firewall change before making another change"
                )
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        state = {
            "version": 1,
            "change_id": secrets.token_hex(12),
            "operation": operation,
            "summary": summary,
            "created_at": now,
            "expires_at": now + CHANGE_CONFIRM_SECONDS,
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "stage": "applying",
            "timer_unit": "boron-firewall-rollback-" + secrets.token_hex(8),
            "undo": undo,
        }
        _write_pending(state)
        try:
            result = apply()
            _verify_protected_access()
            state["stage"] = "pending_confirmation"
            _write_pending(state)
            _arm_rollback(state)
        except Exception:
            _rollback_state(state, reason="change or verification failed")
            raise
        response = dict(result)
        response["pending_change"] = _public_pending(state, token=token)
        return response


def transactional_add_rule(params: dict) -> dict:
    action = _validate_action(params["action"])
    port = _validate_port(params["port"])
    protocol = _validate_protocol(params.get("protocol", "any"))
    from_addr = _validate_from_addr(params.get("from_addr"))
    rule_id = _rule_id(action, port, protocol, from_addr)
    return _start_change(
        "rule.add",
        f"Add {action} rule for {port}/{protocol} from {from_addr}",
        [{"kind": "delete_rule", "rule_id": rule_id}],
        lambda: add_rule(params),
    )


def transactional_delete_rule(params: dict) -> dict:
    target = _rule_for_id(str(params["rule_id"]))
    if target is None:
        raise ValidationError(f"no rule with id '{params['rule_id']}' found")
    restore = {
        "action": target["action"], "port": target["port"], "protocol": target["protocol"],
        "from_addr": target["from"], "comment": target["comment"],
    }
    return _start_change(
        "rule.delete",
        f"Delete {target['action']} rule for {target['port']}/{target['protocol']} from {target['from']}",
        [{"kind": "add_rule", "rule_id": target["rule_id"], "params": restore}],
        lambda: delete_rule(params),
    )


def transactional_add_bypass(params: dict) -> dict:
    address = validate_ip_or_cidr(params["address"])
    return _start_change(
        "bypass.add", f"Trust {address} on every port",
        [{"kind": "delete_bypass", "address": address}],
        lambda: add_bypass(params),
    )


def transactional_delete_bypass(params: dict) -> dict:
    entries = _parse_bypass_rules(run(["ufw", "show", "added"], timeout=15).stdout)
    target = next((item for item in entries if item["bypass_id"] == str(params["bypass_id"])), None)
    if target is None:
        raise ValidationError(f"no bypass entry with id '{params['bypass_id']}' found")
    restore = {"address": target["address"], "label": target["label"]}
    return _start_change(
        "bypass.delete", f"Remove full-access trust for {target['address']}",
        [{"kind": "add_bypass", "params": restore}],
        lambda: delete_bypass(params),
    )


def transactional_enable(params: dict) -> dict:
    if not bool(params.get("confirm", False)):
        raise ValidationError("enabling the firewall requires confirm=true")
    if get_status({})["active"]:
        raise ValidationError("the firewall is already enabled")
    covered = {rule["port"] for rule in _current_rules() if rule["action"] == "allow"}
    added_baseline = sorted(protected_ports() - covered)
    undo = [{"kind": "disable"}]
    undo.extend(
        {
            "kind": "delete_rule",
            "rule_id": _rule_id("allow", port, "any", "any"),
        }
        for port in added_baseline
    )
    return _start_change(
        "firewall.enable", "Enable UFW enforcement",
        undo,
        lambda: enable_firewall(params),
    )


def transactional_disable(params: dict) -> dict:
    if not bool(params.get("confirm", False)):
        raise ValidationError("disabling the firewall requires confirm=true")
    if not get_status({})["active"]:
        raise ValidationError("the firewall is already disabled")
    return _start_change(
        "firewall.disable", "Disable UFW enforcement",
        [{"kind": "enable"}],
        lambda: disable_firewall(params),
    )


def pending_change(params: dict) -> dict:
    with _change_lock:
        state = _read_pending()
        return {"pending_change": _public_pending(state)}


def confirm_change(params: dict) -> dict:
    token = str(params.get("confirmation_token") or "")
    with _change_lock:
        state = _read_pending()
        if state is None:
            raise ValidationError("there is no pending firewall change")
        if float(state["expires_at"]) <= time.time():
            _rollback_state(state, reason="confirmation arrived after expiry")
            raise ValidationError("the confirmation window expired and the change was reverted")
        supplied = hashlib.sha256(token.encode()).hexdigest()
        if not token or not hmac.compare_digest(supplied, state["token_sha256"]):
            raise ValidationError("the firewall confirmation token is invalid")
        change_id = state["change_id"]
        _clear_pending()
        return {"status": "confirmed", "change_id": change_id}


def rollback_change(params: dict) -> dict:
    with _change_lock:
        state = _read_pending()
        if state is None:
            return {"status": "no_pending_change"}
        return _rollback_state(state, reason=str(params.get("reason") or "reverted by administrator"))


def recover_pending_changes() -> dict:
    """Fail safe during daemon startup and from the root console helper."""
    with _change_lock:
        state = _read_pending()
        if state is None:
            return {"status": "no_pending_change"}
        return _rollback_state(state, reason="daemon startup or local-console recovery")
