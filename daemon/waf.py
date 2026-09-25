"""Phase 5 feature 7: ModSecurity/WAF.

**Availability, confirmed live rather than assumed**: this OLS build's own
`openlitespeed -v` banner advertises `mod_security 1.4 (with libmodsecurity
v3.0.14)` as a compiled-in capability, but the actual loadable
`mod_security.so` was NOT present in `/usr/local/lsws/modules/` on this
fresh install -- confirmed by a real `openlitespeed -t` failure
("cannot open shared object file") before this feature assumed it was
usable. Fixed by installing LiteSpeed's own `ols-modsecurity` apt package
(from the same `rpms.litespeedtech.com` repo `openlitespeed` itself came
from) plus Ubuntu's official `modsecurity-crs` package for the OWASP Core
Rule Set -- both are the project's answer to "if yes: ...", not the
"if no: implement OLS-native rate limiting" fallback, since ModSecurity
genuinely is usable here once its module + a ruleset are actually
installed.

**Scope limitation, confirmed live**: OpenLiteSpeed has no per-vhost
ModSecurity configuration at all -- the engine and its rule files load
exactly once, server-wide, in one `module mod_security {}` block
(confirmed both by the OpenLiteSpeed project's own forum -- "I don't
think there's a way to apply modsecurity by user/virtual host on
OpenLiteSpeed" -- and by this feature's own testing). "WAF status per
vhost" / "enable/disable per domain" / "custom rules per domain" are
therefore all implemented as conditional SecRule chains keyed off the
`Host` request header (see `daemon/ols.py`'s `_waf_template_context` and
`templates/httpd_config.conf.j2`), not as separate per-vhost engine
instances -- the engine is one global on/off switch; individual rules
can still be scoped to a single domain via ModSecurity's own rule
language, which is the actual mechanism this feature relies on.
"""
from __future__ import annotations

import os
import re
import datetime as dt
from pathlib import Path

from sqlalchemy import delete, select

from shared.db import write_session
from shared.config import settings
from shared.validation import ValidationError, validate_domain

from daemon import ols
from daemon.ols import WAF_AUDIT_LOG, WAF_RULES_FILE
from daemon.safeio import secure_replace_file
from daemon.procutil import run
from shared.models import Domain, WafCustomRule, WafDomainOverride, WafException, WafSettings, utcnow

MODSEC_MODULE_PATH = "/usr/local/lsws/modules/mod_security.so"

# A small, safe allowlist of ModSecurity variables -- not free-text, since
# `target` is interpolated directly into a live, server-wide config file
# (templates/httpd_config.conf.j2); an arbitrary variable name here is a
# config-injection surface the same way an arbitrary shell argument would
# be in daemon/procutil.py.
ALLOWED_TARGETS = {
    "ARGS", "ARGS_NAMES", "REQUEST_URI", "QUERY_STRING", "REQUEST_BODY",
    "REQUEST_COOKIES", "REQUEST_HEADERS:User-Agent", "REQUEST_HEADERS:Referer",
}

# Rejects backtick/double-quote/newline/CR -- the actual injection defense:
# `pattern` lands inside a backtick-delimited `modsecurity_rules` block AND
# inside a double-quoted SecRule action list (see the .j2 template), so any
# of those four characters would let a crafted pattern break out of its
# intended string context and inject arbitrary additional config/rule
# lines into a server-wide file every domain shares.
WAF_PATTERN_RE = re.compile(r'\A[^`"\n\r]{1,300}\Z')
WAF_MODES = {"disabled", "detect", "protect"}
WAF_DOMAIN_MODES = {"inherit", *WAF_MODES}
WAF_CATEGORY_RE = re.compile(r"\A[A-Za-z0-9_.:/-]{1,100}\Z")
WAF_URI_RE = re.compile(r"\A/[A-Za-z0-9_./~%+@:-]{0,499}\Z")
WAF_PARAMETER_RE = re.compile(r"\A[A-Za-z0-9_.:-]{1,100}\Z")


CRS_SETUP = Path('/etc/modsecurity/crs/crs-setup.conf')
CRS_RULES = Path('/usr/share/modsecurity-crs/rules')


def bootstrap_rules() -> None:
    """Create the missing libModSecurity-compatible packaged CRS include.

    Ubuntu's Apache loader uses IncludeOptional, which libModSecurity rejects.
    Preserve an existing operator-managed include; create only the absent file.
    """
    destination = Path(WAF_RULES_FILE)
    if destination.is_file():
        return
    if not CRS_SETUP.is_file() or not CRS_RULES.is_dir():
        raise RuntimeError('OWASP CRS package files are missing; install modsecurity-crs')
    before = CRS_SETUP.parent / 'REQUEST-900-EXCLUSION-RULES-BEFORE-CRS.conf'
    after = CRS_SETUP.parent / 'RESPONSE-999-EXCLUSION-RULES-AFTER-CRS.conf'
    includes = [str(CRS_SETUP)]
    if before.is_file():
        includes.append(str(before))
    includes.append(str(CRS_RULES / '*.conf'))
    if after.is_file():
        includes.append(str(after))
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    content = '# Boron: packaged OWASP CRS for libModSecurity\n' + ''.join(
        'Include ' + name + '\n' for name in includes)
    secure_replace_file(str(destination.parent), destination.name, content.encode(), 0, 0, 0o644)


def is_available() -> bool:
    return os.path.exists(MODSEC_MODULE_PATH)


def get_status(params: dict) -> dict:
    with write_session() as session:
        settings_row = session.get(WafSettings, 1)
        mode = _settings_mode(settings_row)
        overrides = session.scalars(select(WafDomainOverride).order_by(WafDomainOverride.domain)).all()
        rules = session.scalars(select(WafCustomRule)).all()
        rule_list = [{"id": r.id, "domain": r.domain, "target": r.target, "pattern": r.pattern} for r in rules]
        exceptions = session.scalars(
            select(WafException).order_by(WafException.expires_at.desc())
        ).all()
        hosted_domains = session.scalars(select(Domain.domain).order_by(Domain.domain)).all()
    return {
        "available": is_available(),
        "enabled": mode != "disabled",
        "mode": mode,
        "paranoia_level": settings_row.paranoia_level if settings_row else 1,
        "anomaly_threshold": settings_row.anomaly_threshold if settings_row else 5,
        "wp_login_limit": settings_row.wp_login_limit if settings_row else 10,
        "wp_xmlrpc_limit": settings_row.wp_xmlrpc_limit if settings_row else 5,
        "wp_rate_window_seconds": settings_row.wp_rate_window_seconds if settings_row else 60,
        "domain_overrides": [o.domain for o in overrides if _domain_mode(o) == "disabled"],
        "domain_policies": [{"domain": o.domain, "mode": _domain_mode(o)} for o in overrides],
        "custom_rules": rule_list,
        "exceptions": [_exception_dict(row) for row in exceptions],
        "available_domains": sorted({*hosted_domains, *([settings.webmail_hostname] if settings.webmail_hostname else [])}),
    }


def _settings_mode(row: WafSettings | None) -> str:
    if row is None:
        return "disabled"
    if row.mode in WAF_MODES and (row.mode != "disabled" or not row.enabled):
        return row.mode
    return "protect" if row.enabled else "disabled"


def _domain_mode(row: WafDomainOverride) -> str:
    if row.mode in WAF_DOMAIN_MODES:
        return row.mode
    return "disabled" if row.disabled else "inherit"


def _require_known_domain(session, value: str) -> str:
    domain = validate_domain(value)
    exists = session.scalar(select(Domain.id).where(Domain.domain == domain)) is not None
    if not exists and domain != settings.webmail_hostname:
        raise ValidationError(f"domain '{domain}' is not an active Boron virtual host")
    return domain


def _bounded_int(value, name: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be an integer") from None
    if not minimum <= result <= maximum:
        raise ValidationError(f"{name} must be between {minimum} and {maximum}")
    return result


def _refresh_with_rollback(restore) -> None:
    """Keep SQLite source-of-truth aligned with OLS config transactions."""
    try:
        ols.refresh_main_config()
    except Exception as apply_error:
        restore()
        try:
            ols.refresh_main_config()
        except Exception as rollback_error:
            raise RuntimeError(
                f"WAF update failed ({apply_error}); database was restored but OLS rollback validation also failed: {rollback_error}"
            ) from apply_error
        raise


def set_enabled(params: dict) -> dict:
    # Compatibility endpoint: first enable is deliberately detection-only.
    current = get_status({})
    return update_settings({
        "mode": "detect" if bool(params["enabled"]) else "disabled",
        "paranoia_level": current["paranoia_level"],
        "anomaly_threshold": current["anomaly_threshold"],
        "wp_login_limit": current["wp_login_limit"],
        "wp_xmlrpc_limit": current["wp_xmlrpc_limit"],
        "wp_rate_window_seconds": current["wp_rate_window_seconds"],
    })


def update_settings(params: dict) -> dict:
    mode = str(params.get("mode", "detect"))
    if mode not in WAF_MODES:
        raise ValidationError(f"mode must be one of {sorted(WAF_MODES)}")
    if mode != "disabled":
        if not is_available():
            raise ValidationError("ModSecurity module is not installed on this server")
        bootstrap_rules()
    values = {
        "paranoia_level": _bounded_int(params.get("paranoia_level", 1), "paranoia_level", 1, 4),
        "anomaly_threshold": _bounded_int(params.get("anomaly_threshold", 5), "anomaly_threshold", 1, 100),
        "wp_login_limit": _bounded_int(params.get("wp_login_limit", 10), "wp_login_limit", 0, 10000),
        "wp_xmlrpc_limit": _bounded_int(params.get("wp_xmlrpc_limit", 5), "wp_xmlrpc_limit", 0, 10000),
        "wp_rate_window_seconds": _bounded_int(params.get("wp_rate_window_seconds", 60), "wp_rate_window_seconds", 10, 3600),
    }
    previous = None
    with write_session() as session:
        row = session.get(WafSettings, 1)
        if row is not None:
            previous = {
                "enabled": row.enabled, "mode": row.mode,
                "paranoia_level": row.paranoia_level,
                "anomaly_threshold": row.anomaly_threshold,
                "wp_login_limit": row.wp_login_limit,
                "wp_xmlrpc_limit": row.wp_xmlrpc_limit,
                "wp_rate_window_seconds": row.wp_rate_window_seconds,
            }
        else:
            row = WafSettings(id=1)
            session.add(row)
        row.mode = mode
        row.enabled = mode != "disabled"
        for key, value in values.items():
            setattr(row, key, value)
    def restore():
        with write_session() as session:
            row = session.get(WafSettings, 1)
            if previous is None:
                if row is not None:
                    session.delete(row)
            else:
                if row is None:
                    row = WafSettings(id=1)
                    session.add(row)
                for key, value in previous.items():
                    setattr(row, key, value)
    _refresh_with_rollback(restore)
    return {"enabled": mode != "disabled", "mode": mode, **values}


def set_domain_override(params: dict) -> dict:
    disabled = bool(params.get("disabled", True))
    result = set_domain_policy({
        "domain": params["domain"], "mode": "disabled" if disabled else "inherit"
    })
    return {"domain": result["domain"], "disabled": disabled}


def set_domain_policy(params: dict) -> dict:
    mode = str(params.get("mode", "inherit"))
    if mode not in WAF_DOMAIN_MODES:
        raise ValidationError(f"mode must be one of {sorted(WAF_DOMAIN_MODES)}")
    previous = None
    with write_session() as session:
        domain = _require_known_domain(session, params["domain"])
        row = session.scalar(select(WafDomainOverride).where(WafDomainOverride.domain == domain))
        if row is not None:
            previous = {"disabled": row.disabled, "mode": row.mode}
        if mode != "inherit":
            if row is None:
                session.add(WafDomainOverride(domain=domain, disabled=mode == "disabled", mode=mode))
            else:
                row.disabled = mode == "disabled"
                row.mode = mode
        elif row is not None:
            session.delete(row)
    def restore():
        with write_session() as session:
            row = session.scalar(select(WafDomainOverride).where(WafDomainOverride.domain == domain))
            if previous is None:
                if row is not None:
                    session.delete(row)
            else:
                if row is None:
                    row = WafDomainOverride(domain=domain)
                    session.add(row)
                row.disabled = previous["disabled"]
                row.mode = previous["mode"]
    _refresh_with_rollback(restore)
    return {"domain": domain, "mode": mode}


def _validate_target(target: str) -> str:
    if target not in ALLOWED_TARGETS:
        raise ValidationError(f"target must be one of {sorted(ALLOWED_TARGETS)}")
    return target


def _validate_pattern(pattern: str) -> str:
    if not isinstance(pattern, str) or not WAF_PATTERN_RE.match(pattern):
        raise ValidationError(
            "pattern must be 1-300 characters and must not contain backtick, double-quote, or newline characters"
        )
    return pattern


def add_custom_rule(params: dict) -> dict:
    target = _validate_target(params["target"])
    pattern = _validate_pattern(params["pattern"])
    with write_session() as session:
        domain = _require_known_domain(session, params["domain"])
        row = WafCustomRule(domain=domain, target=target, pattern=pattern)
        session.add(row)
        session.flush()
        rule_id = row.id
    def restore():
        with write_session() as session:
            row = session.get(WafCustomRule, rule_id)
            if row is not None:
                session.delete(row)
    _refresh_with_rollback(restore)
    return {"id": rule_id, "domain": domain, "target": target, "pattern": pattern}


def delete_custom_rule(params: dict) -> dict:
    rule_id = int(params["rule_id"])
    deleted = None
    with write_session() as session:
        row = session.get(WafCustomRule, rule_id)
        if row is None:
            raise ValidationError(f"custom rule {rule_id} not found")
        deleted = {"id": row.id, "domain": row.domain, "target": row.target, "pattern": row.pattern, "created_at": row.created_at}
        session.delete(row)
    def restore():
        with write_session() as session:
            if session.get(WafCustomRule, rule_id) is None:
                session.add(WafCustomRule(**deleted))
    _refresh_with_rollback(restore)
    return {"id": rule_id, "status": "deleted"}


def _exception_dict(row: WafException) -> dict:
    now = utcnow()
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    return {
        "id": row.id, "domain": row.domain, "rule_id": row.rule_id,
        "category": row.category, "uri_prefix": row.uri_prefix,
        "parameter": row.parameter, "reason": row.reason,
        "expires_at": expires.isoformat(), "active": expires > now,
    }


def add_exception(params: dict) -> dict:
    raw_rule_id = params.get("rule_id")
    category = str(params.get("category") or "").strip() or None
    if raw_rule_id in (None, "") and category is None:
        raise ValidationError("select a rule ID or category for the exception")
    if raw_rule_id not in (None, "") and category is not None:
        raise ValidationError("select either a rule ID or category, not both")
    rule_id = None
    if raw_rule_id not in (None, ""):
        rule_id = _bounded_int(raw_rule_id, "rule_id", 1, 999_999_999)
    if category is not None and not WAF_CATEGORY_RE.match(category):
        raise ValidationError("category contains unsupported characters")
    uri_prefix = str(params.get("uri_prefix") or "").strip() or None
    parameter = str(params.get("parameter") or "").strip() or None
    if uri_prefix is not None and not WAF_URI_RE.match(uri_prefix):
        raise ValidationError("URI prefix must begin with / and contain URL path characters only")
    if parameter is not None and not WAF_PARAMETER_RE.match(parameter):
        raise ValidationError("parameter contains unsupported characters")
    hours = _bounded_int(params.get("duration_hours", 24), "duration_hours", 1, 24 * 90)
    reason = str(params.get("reason") or "").strip()[:300] or None
    expires_at = utcnow() + dt.timedelta(hours=hours)
    with write_session() as session:
        domain = _require_known_domain(session, params["domain"])
        row = WafException(
            domain=domain, rule_id=rule_id, category=category,
            uri_prefix=uri_prefix, parameter=parameter, reason=reason,
            expires_at=expires_at,
        )
        session.add(row)
        session.flush()
        result = _exception_dict(row)
    def restore():
        with write_session() as session:
            row = session.get(WafException, result["id"])
            if row is not None:
                session.delete(row)
    _refresh_with_rollback(restore)
    return result


def delete_exception(params: dict) -> dict:
    exception_id = int(params["exception_id"])
    deleted = None
    with write_session() as session:
        row = session.get(WafException, exception_id)
        if row is None:
            raise ValidationError(f"WAF exception {exception_id} not found")
        deleted = {
            "id": row.id, "domain": row.domain, "rule_id": row.rule_id,
            "category": row.category, "uri_prefix": row.uri_prefix,
            "parameter": row.parameter, "reason": row.reason,
            "expires_at": row.expires_at, "created_at": row.created_at,
        }
        session.delete(row)
    def restore():
        with write_session() as session:
            if session.get(WafException, exception_id) is None:
                session.add(WafException(**deleted))
    _refresh_with_rollback(restore)
    return {"id": exception_id, "status": "deleted"}


# --- blocked-request log parsing ---------------------------------------

# Confirmed live against this server's own real ModSecurity audit log
# (SecAuditLogType Serial): a transaction begins with `---<id>---A--`
# (timestamp + client ip/port), `---<id>---B--` has the request line +
# Host header, and `---<id>---H--` has one or more `ModSecurity: ...`
# lines -- the final "Access denied" line carries the actual block
# decision + anomaly score, which is what's surfaced as the summary.
_TX_START_RE = re.compile(r"\A---(?P<txid>\w+)---A--\Z")
_SECTION_RE = re.compile(r"\A---(?P<txid>\w+)---(?P<part>[A-Z])--\Z")
_A_LINE_RE = re.compile(r"\A\[(?P<ts>[^\]]+)\]\s+\S+\s+(?P<client_ip>\S+)\s+\S+\s+\S+\s+\S+\Z")
_REQUEST_LINE_RE = re.compile(r"\A(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP")
_HOST_HEADER_RE = re.compile(r"\Ahost:\s*(?P<host>\S+)\Z", re.IGNORECASE)
_DENIED_RE = re.compile(r"Access denied with code (?P<status>\d+)")
_RULE_ID_RE = re.compile(r'\[id "(?P<rule_id>\d+)"\]')
_MESSAGE_RE = re.compile(r'\[msg "(?P<message>[^"]*)"\]')
_TAG_RE = re.compile(r'\[tag "(?P<tag>[^"]*)"\]')


def _parse_audit_log(text: str) -> list[dict]:
    transactions: dict[str, dict] = {}
    order: list[str] = []
    current_part = None
    current_txid = None
    for line in text.splitlines():
        section = _SECTION_RE.match(line)
        if section:
            current_txid = section.group("txid")
            current_part = section.group("part")
            if current_txid not in transactions:
                transactions[current_txid] = {
                    "txid": current_txid, "timestamp": None,
                    "client_ip": None, "verified_client_ip": None,
                    "method": None, "path": None, "host": None,
                    "status": None, "message": None, "rule_id": None,
                    "tags": [], "action": "detected", "findings": [],
                }
                order.append(current_txid)
            continue
        if current_txid is None:
            continue
        tx = transactions[current_txid]
        if current_part == "A" and tx["timestamp"] is None:
            m = _A_LINE_RE.match(line)
            if m:
                tx["timestamp"] = m.group("ts")
                tx["client_ip"] = m.group("client_ip")
                tx["verified_client_ip"] = m.group("client_ip")
        elif current_part == "B":
            m = _REQUEST_LINE_RE.match(line)
            if m:
                tx["method"] = m.group("method")
                tx["path"] = m.group("path")
            m = _HOST_HEADER_RE.match(line)
            if m:
                tx["host"] = m.group("host")
        elif current_part == "H":
            rule_match = _RULE_ID_RE.search(line)
            message_match = _MESSAGE_RE.search(line)
            if rule_match:
                finding = {
                    "rule_id": int(rule_match.group("rule_id")),
                    "message": message_match.group("message") if message_match else "ModSecurity rule matched",
                    "tags": _TAG_RE.findall(line),
                }
                tx["findings"].append(finding)
                # The anomaly aggregation rule explains the final decision,
                # while the first concrete rule explains what was detected.
                if tx["rule_id"] is None or tx["rule_id"] in {949110, 959100}:
                    tx["rule_id"] = finding["rule_id"]
                    tx["tags"] = finding["tags"]
            denied = _DENIED_RE.search(line)
            if denied:
                tx["status"] = int(denied.group("status"))
                tx["action"] = "blocked"
                tx["message"] = message_match.group("message") if message_match else "Request blocked"
            elif message_match and tx["message"] is None:
                tx["message"] = message_match.group("message")
    return [transactions[txid] for txid in order if transactions[txid]["findings"]]


def list_blocked_requests(params: dict) -> dict:
    domain_filter = params.get("domain")
    limit = int(params.get("limit", 50) or 50)
    result = run(["tail", "-n", "5000", WAF_AUDIT_LOG], timeout=15)
    events = _parse_audit_log(result.stdout)
    if domain_filter:
        events = [e for e in events if e["host"] == domain_filter]
    events.reverse()
    return {"events": events[:limit]}


def unblock_ip(params: dict) -> dict:
    """Remove an incident IP from active Fail2ban jails only.

    A deliberate permanent administrator ban is never silently removed from
    the WAF screen. Those remain visible and reversible in IP Bans.
    """
    import ipaddress
    from daemon import fail2ban

    value = str(params.get("ip") or "")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        raise ValidationError("ip must be a single IPv4 or IPv6 address") from None
    unbanned = []
    errors = []
    for jail in fail2ban.list_jails({})["jails"]:
        if value not in jail.get("banned_ips", []):
            continue
        try:
            fail2ban.unban_ip({"jail": jail["name"], "ip": value})
            unbanned.append(jail["name"])
        except Exception as exc:
            errors.append({"jail": jail["name"], "error": str(exc)})
    return {"ip": value, "unbanned_from": unbanned, "errors": errors}
