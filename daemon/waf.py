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
from pathlib import Path

from sqlalchemy import delete, select

from shared.db import write_session
from shared.validation import ValidationError, validate_domain

from daemon import ols
from daemon.ols import WAF_AUDIT_LOG, WAF_RULES_FILE
from daemon.safeio import secure_replace_file
from daemon.procutil import run
from shared.models import WafCustomRule, WafDomainOverride, WafSettings

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
        enabled = bool(settings_row and settings_row.enabled)
        overrides = {o.domain for o in session.scalars(select(WafDomainOverride)).all()}
        rules = session.scalars(select(WafCustomRule)).all()
        rule_list = [{"id": r.id, "domain": r.domain, "target": r.target, "pattern": r.pattern} for r in rules]
    return {
        "available": is_available(),
        "enabled": enabled,
        "domain_overrides": sorted(overrides),
        "custom_rules": rule_list,
    }


def set_enabled(params: dict) -> dict:
    if not is_available():
        raise ValidationError("ModSecurity module is not installed on this server")
    enabled = bool(params["enabled"])
    if enabled:
        bootstrap_rules()
    with write_session() as session:
        row = session.get(WafSettings, 1)
        if row is None:
            row = WafSettings(id=1, enabled=enabled)
            session.add(row)
        else:
            row.enabled = enabled
    ols.refresh_main_config()
    return {"enabled": enabled}


def set_domain_override(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    disabled = bool(params.get("disabled", True))
    with write_session() as session:
        row = session.scalar(select(WafDomainOverride).where(WafDomainOverride.domain == domain))
        if disabled:
            if row is None:
                session.add(WafDomainOverride(domain=domain, disabled=True))
        elif row is not None:
            session.delete(row)
    ols.refresh_main_config()
    return {"domain": domain, "disabled": disabled}


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
    domain = validate_domain(params["domain"])
    target = _validate_target(params["target"])
    pattern = _validate_pattern(params["pattern"])
    with write_session() as session:
        row = WafCustomRule(domain=domain, target=target, pattern=pattern)
        session.add(row)
        session.flush()
        rule_id = row.id
    ols.refresh_main_config()
    return {"id": rule_id, "domain": domain, "target": target, "pattern": pattern}


def delete_custom_rule(params: dict) -> dict:
    rule_id = int(params["rule_id"])
    with write_session() as session:
        row = session.get(WafCustomRule, rule_id)
        if row is None:
            raise ValidationError(f"custom rule {rule_id} not found")
        session.delete(row)
    ols.refresh_main_config()
    return {"id": rule_id, "status": "deleted"}


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
_DENIED_RE = re.compile(r"Access denied with code (?P<status>\d+).*msg\s+\"(?P<msg>[^\"]*)\"")


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
                transactions[current_txid] = {"txid": current_txid, "timestamp": None, "client_ip": None, "method": None, "path": None, "host": None, "status": None, "message": None}
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
        elif current_part == "B":
            m = _REQUEST_LINE_RE.match(line)
            if m:
                tx["method"] = m.group("method")
                tx["path"] = m.group("path")
            m = _HOST_HEADER_RE.match(line)
            if m:
                tx["host"] = m.group("host")
        elif current_part == "H":
            m = _DENIED_RE.search(line)
            if m:
                tx["status"] = int(m.group("status"))
                tx["message"] = m.group("msg")
    return [transactions[txid] for txid in order if transactions[txid]["status"] is not None]


def list_blocked_requests(params: dict) -> dict:
    domain_filter = params.get("domain")
    limit = int(params.get("limit", 50) or 50)
    result = run(["tail", "-n", "5000", WAF_AUDIT_LOG], timeout=15)
    events = _parse_audit_log(result.stdout)
    if domain_filter:
        events = [e for e in events if e["host"] == domain_filter]
    events.reverse()
    return {"events": events[:limit]}
