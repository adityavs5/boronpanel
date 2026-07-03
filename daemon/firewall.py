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
import re

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


def _ssh_port() -> int:
    """The real configured SSH port -- defaults to 22 if unset/commented,
    matching sshd's own documented default. Read from the live config
    rather than hardcoded, since an operator who has already moved SSH
    off 22 would otherwise have port 22 "protected" for nothing while
    their real SSH port stayed unprotected."""
    try:
        with open("/etc/ssh/sshd_config") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.lower().startswith("port "):
                    return int(stripped.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 22


def protected_ports() -> set[int]:
    return {_ssh_port(), settings.api_bind_port, 80, 443, 25, 587, 993}


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
    return {"rules": rules, "active": active}


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
    return {"active": active, "raw": result.stdout}


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
            run(["ufw", "allow", str(port), "comment", "forgehost-baseline-protected-port"], timeout=20)


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
