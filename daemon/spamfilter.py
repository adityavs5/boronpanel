"""SpamAssassin integration (Phase 4 feature 1).

Architecture:

- **spamd runs once, server-wide**, scanning every inbound message
  unconditionally (Postfix's `content_filter`, restricted to the `smtp
  inet` service only -- inbound mail from the internet, never the
  `submission` service customers send *through*, which would be a strange
  thing to spam-scan). Per-domain "enable/disable" and "custom threshold"
  are **not** implemented as separate scanning paths -- they're both just
  SpamAssassin's own `required_score`, looked up per recipient via spamd's
  `--virtual-config-dir` (built for exactly this "no real Unix user"
  scenario, the same problem this project's virtual mailboxes already
  posed for autoresponders in Phase 3 feature 4). "Disabled" sets
  `required_score` to a value no real message will ever reach -- simpler
  and more robust than trying to route disabled domains around the
  content filter at the Postfix transport level, and it still means a
  disabled domain's mail is scanned (so `X-Spam-Score` is still visible
  for the curious) but never *acted on*.
- **The Junk-filing decision itself lives in ONE global Dovecot Sieve
  script** (`sieve_before`, run before any mailbox's own personal script,
  Phase 3 feature 4's autoresponder Sieve), not duplicated per mailbox --
  it only ever checks `X-Spam-Flag: YES`, a header spamd itself sets
  per-message based on the per-recipient threshold above. One rule,
  identical for every mailbox on the server; the interesting per-account
  behavior already happened one layer up, at scan time.
- Settings are **per mail domain**, not per mailbox -- matches this
  project's existing domain-scoped email-feature API shape (Phase 3
  feature 4: catchall/forwarders/autoresponders are all per-domain).
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from shared.db import write_session
from shared.models import MailDomain, SpamGlobalSettings
from shared.validation import ValidationError, validate_domain, validate_spam_threshold

from daemon.configtx import ConfigWriter, StepResult
from daemon.procutil import run

logger = logging.getLogger("forgehostd.spamfilter")

VIRTUAL_CONFIG_BASE = "/etc/forgehost/spamassassin"
SPAMD_DEFAULTS_PATH = "/etc/default/spamd"
LOCAL_CF_PATH = "/etc/spamassassin/local.cf"
MASTER_CF_PATH = "/etc/postfix/master.cf"
POSTFIX_DIR = "/etc/postfix"
SIEVE_PLUGIN_CONF_PATH = "/etc/dovecot/conf.d/90-sieve.conf"
GLOBAL_SIEVE_DIR = "/etc/dovecot/sieve-global"
GLOBAL_SIEVE_PATH = f"{GLOBAL_SIEVE_DIR}/spam-to-junk.sieve"
SIEVEC_BIN = "/usr/bin/sievec"

# A message no real SpamAssassin score will ever reach (default rule set
# tops out well under 100) -- "disabled" without a separate scanning path,
# see module docstring.
DISABLED_THRESHOLD = 1000.0

BACKUP_DIR = "/var/lib/forgehost/backups"


class SpamFilterError(Exception):
    pass


# --- per-domain settings (no service reload needed -- spamd reads a
# virtual-config-dir prefs file fresh per scan, same "no reload for
# routine mail CRUD" property Phase e's SQL-backed mailboxes already
# have) -----------------------------------------------------------------


def _user_prefs_path(domain: str) -> Path:
    return Path(VIRTUAL_CONFIG_BASE) / domain / "user_prefs"


def apply_domain_spam_settings(domain: str, enabled: bool, threshold: float | None) -> None:
    """threshold=None (with enabled=True) means "use the server-wide admin
    default" -- no per-domain file is written at all in that case (and any
    previous override is removed), so a later change to the global default
    is picked up automatically rather than needing every domain using it
    rewritten."""
    domain = validate_domain(domain)
    path = _user_prefs_path(domain)
    if enabled and threshold is None:
        path.unlink(missing_ok=True)
        return

    score = threshold if enabled else DISABLED_THRESHOLD
    content = f"required_score {score}\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    tmp_path.write_text(content)
    os.chmod(tmp_path, 0o644)
    os.replace(tmp_path, path)


def remove_domain_spam_settings(domain: str) -> None:
    """TERMINATE_HOOKS-adjacent cleanup (called from handlers_domain's
    domain-removal path, mirroring redirects/DNS-zone per-domain cleanup)
    -- idempotent, safe even if the domain never had an override."""
    shutil.rmtree(Path(VIRTUAL_CONFIG_BASE) / domain, ignore_errors=True)


# --- global admin default -------------------------------------------------


def get_global_default_threshold() -> float:
    with write_session() as session:
        row = session.scalar(select(SpamGlobalSettings).where(SpamGlobalSettings.id == 1))
        return row.default_threshold if row else 5.0


def set_global_default_threshold(threshold: float) -> dict:
    threshold = validate_spam_threshold(threshold)
    with write_session() as session:
        row = session.scalar(select(SpamGlobalSettings).where(SpamGlobalSettings.id == 1))
        if row is None:
            row = SpamGlobalSettings(id=1, default_threshold=threshold)
            session.add(row)
        else:
            row.default_threshold = threshold
        session.flush()
        result_threshold = row.default_threshold

    _write_local_cf(result_threshold)
    return {"default_threshold": result_threshold}


def _write_local_cf(default_threshold: float) -> None:
    content = (
        "# Managed by Forgehost (daemon/spamfilter.py) -- do not hand-edit,\n"
        "# changes are overwritten the next time the admin default changes.\n"
        f"required_score {default_threshold}\n"
        "rewrite_header Subject [SPAM]\n"
        "report_safe 0\n"
    )
    path = Path(LOCAL_CF_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    tmp_path.write_text(content)
    os.replace(tmp_path, path)

    result = run(["spamassassin", "--lint"], timeout=30)
    if not result.ok:
        raise SpamFilterError(f"spamassassin --lint failed after writing local.cf: {result.stderr.strip() or result.stdout.strip()}")


# --- one-time global bootstrap (system.bootstrap_spamassassin RPC op,
# mirroring daemon/ols.py's bootstrap_baseline / daemon/pma.py's
# bootstrap_pma -- run once, deliberately, not automatically on daemon
# startup) -------------------------------------------------------------


def bootstrap_spamassassin() -> dict:
    default_threshold = get_global_default_threshold()
    _write_local_cf(default_threshold)
    _write_spamd_defaults()
    _restart_spamd()
    postfix_result = _ensure_postfix_wiring()
    sieve_result = _ensure_dovecot_sieve_wiring()
    return {
        "status": "ok",
        "postfix": postfix_result.summary() if postfix_result else "already configured",
        "dovecot": sieve_result.summary() if sieve_result else "already configured",
    }


def _write_spamd_defaults() -> None:
    """--virtual-config-dir is spamd's own documented mechanism for
    per-recipient config with no real Unix account backing the recipient
    (spamd manpage: "needs -x") -- exactly this project's virtual-mailbox
    problem, same shape as Phase 3 feature 4's autoresponder Sieve
    solving the equivalent problem for vacation replies. %d is the
    recipient's domain (split from -u's argument by spamd itself); this
    project's settings are per-domain, so %l (local-part) is deliberately
    not part of the pattern."""
    content = (
        "# Managed by Forgehost (daemon/spamfilter.py).\n"
        'OPTIONS="--create-prefs --max-children 5 --helper-home-dir '
        f'--virtual-config-dir={VIRTUAL_CONFIG_BASE}/%d -x -u debian-spamd -H"\n'
    )
    path = Path(SPAMD_DEFAULTS_PATH)
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    tmp_path.write_text(content)
    os.replace(tmp_path, path)


def _restart_spamd() -> None:
    run(["systemctl", "enable", "spamd"], timeout=15)
    result = run(["systemctl", "restart", "spamd"], timeout=30)
    if not result.ok:
        raise SpamFilterError(f"failed to restart spamd: {result.stderr.strip() or result.stdout.strip()}")


# --- Postfix: one new pipe service ("scan") + a content_filter override
# on the inbound smtp service only (never `submission` -- that's mail
# customers send *through* Forgehost, not mail arriving *at* it, and
# scanning your own customers' outbound mail for spam is a different
# feature this goal doesn't ask for). Content_filter is deliberately a
# per-service master.cf override, not a main.cf global -- main.cf's
# content_filter would also apply to the *reinjected* mail spamc's own
# `-e sendmail` hands back to Postfix, looping the scan forever; scoping
# it to the `smtp inet` service line only means the reinjection (which
# re-enters via `sendmail`/pickup, a different path entirely) is never
# rescanned. -----------------------------------------------------------


SCAN_SERVICE_BLOCK = (
    "scan      unix  -       n       n       -       10      pipe\n"
    "  user=debian-spamd argv=/usr/bin/spamc -f -u ${recipient} -e /usr/sbin/sendmail -oi -f ${sender} ${recipient}\n"
)
CONTENT_FILTER_OVERRIDE = "  -o content_filter=scan:\n"


def _master_cf_needs_scan_service(content: str) -> bool:
    return "\nscan " not in content and not content.startswith("scan ")


def _render_master_cf(content: str) -> str:
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    added_override = False
    for i, line in enumerate(lines):
        out.append(line)
        if line.startswith("smtp") and "inet" in line.split() and not added_override:
            # only the *first* whitespace-delimited field must be exactly
            # "smtp" (master.cf's service-name column) -- "smtp unix" (the
            # outbound relay transport, further down the file) must not
            # match, confirmed by reading the stock file's own layout
            # before writing this check.
            if line.split()[0] == "smtp":
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                if "content_filter=scan:" not in next_line:
                    out.append(CONTENT_FILTER_OVERRIDE)
                added_override = True
    if not added_override:
        raise SpamFilterError("master.cf has no 'smtp inet' service line to attach content_filter to")
    rendered = "".join(out)
    if _master_cf_needs_scan_service(rendered):
        if not rendered.endswith("\n"):
            rendered += "\n"
        rendered += "\n" + SCAN_SERVICE_BLOCK
    return rendered


def _postfix_validate(tmp_path: Path) -> StepResult:
    """ARCHITECTURE.md SS7: postfix check against a scratch copy of the
    config dir (postfix's own -c flag), never against the live tree."""
    with tempfile.TemporaryDirectory() as scratch:
        for name in os.listdir(POSTFIX_DIR):
            src = Path(POSTFIX_DIR) / name
            if src.is_file():
                shutil.copy2(src, Path(scratch) / name)
        shutil.copy2(tmp_path, Path(scratch) / "master.cf")
        result = run(["postfix", "-c", scratch, "check"], timeout=20)
        if not result.ok:
            return StepResult(False, result.stderr.strip() or result.stdout.strip())
        return StepResult(True)


def _postfix_reload() -> StepResult:
    result = run(["postfix", "reload"], timeout=20)
    if not result.ok:
        return StepResult(False, result.stderr.strip() or result.stdout.strip())
    return StepResult(True)


def _postfix_verify() -> StepResult:
    result = run(["systemctl", "is-active", "postfix"], timeout=10)
    if result.stdout.strip() != "active":
        return StepResult(False, f"postfix not active after reload: {result.stdout.strip()}")
    return StepResult(True)


def _ensure_postfix_wiring():
    current = Path(MASTER_CF_PATH).read_text()
    if "content_filter=scan:" in current and not _master_cf_needs_scan_service(current):
        return None  # already configured, idempotent no-op

    new_content = _render_master_cf(current)
    writer = ConfigWriter(
        target_path=MASTER_CF_PATH,
        validate=_postfix_validate,
        reload=_postfix_reload,
        verify=_postfix_verify,
        backup_dir=BACKUP_DIR,
        subsystem="postfix-spamfilter",
    )
    result = writer.apply(new_content)
    if not result.ok:
        raise SpamFilterError(f"Postfix content_filter wiring failed: {result.summary()}")
    return result


# --- Dovecot: one global sieve_before script -----------------------------


GLOBAL_SIEVE_SOURCE = (
    'require ["fileinto", "mailbox"];\n'
    "\n"
    'if header :contains "X-Spam-Flag" "YES" {\n'
    '    fileinto :create "Junk";\n'
    "    stop;\n"
    "}\n"
)


def _validate_sieve(content: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sieve", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        result = run([SIEVEC_BIN, str(tmp_path)], timeout=15)
        if not result.ok:
            raise SpamFilterError(f"global Sieve script failed validation: {result.stderr.strip() or result.stdout.strip()}")
    finally:
        tmp_path.unlink(missing_ok=True)
        tmp_path.with_suffix(".svbin").unlink(missing_ok=True)


def _install_global_sieve_script() -> None:
    _validate_sieve(GLOBAL_SIEVE_SOURCE)
    Path(GLOBAL_SIEVE_DIR).mkdir(parents=True, exist_ok=True, mode=0o755)
    tmp_path = Path(f"{GLOBAL_SIEVE_PATH}.tmp.{os.getpid()}")
    tmp_path.write_text(GLOBAL_SIEVE_SOURCE)
    os.replace(tmp_path, GLOBAL_SIEVE_PATH)
    Path(GLOBAL_SIEVE_PATH).with_suffix(".svbin").unlink(missing_ok=True)
    result = run([SIEVEC_BIN, GLOBAL_SIEVE_PATH], timeout=15)
    if not result.ok:
        raise SpamFilterError(f"failed to compile global Sieve script in place: {result.stderr.strip() or result.stdout.strip()}")


def _dovecot_validate(tmp_path: Path) -> StepResult:
    result = run(["doveconf", "-n", "-c", str(tmp_path)], timeout=15)
    if not result.ok:
        return StepResult(False, result.stderr.strip() or result.stdout.strip())
    return StepResult(True)


def _dovecot_reload() -> StepResult:
    result = run(["systemctl", "reload", "dovecot"], timeout=20)
    if not result.ok:
        return StepResult(False, result.stderr.strip() or result.stdout.strip())
    return StepResult(True)


def _dovecot_verify() -> StepResult:
    result = run(["systemctl", "is-active", "dovecot"], timeout=10)
    if result.stdout.strip() != "active":
        return StepResult(False, f"dovecot not active after reload: {result.stdout.strip()}")
    return StepResult(True)


def _ensure_dovecot_sieve_wiring():
    current = Path(SIEVE_PLUGIN_CONF_PATH).read_text()
    if f"sieve_before = {GLOBAL_SIEVE_PATH}" in current:
        _install_global_sieve_script()
        return None  # already wired, idempotent no-op (script content still refreshed)

    _install_global_sieve_script()
    marker = "  sieve = file:~/sieve;active=~/.dovecot.sieve\n"
    if marker not in current:
        raise SpamFilterError("90-sieve.conf's expected 'sieve =' line not found -- refusing to guess where to insert sieve_before")
    new_content = current.replace(marker, marker + f"  sieve_before = {GLOBAL_SIEVE_PATH}\n", 1)

    writer = ConfigWriter(
        target_path=SIEVE_PLUGIN_CONF_PATH,
        validate=_dovecot_validate,
        reload=_dovecot_reload,
        verify=_dovecot_verify,
        backup_dir=BACKUP_DIR,
        subsystem="dovecot-spamfilter",
    )
    result = writer.apply(new_content)
    if not result.ok:
        raise SpamFilterError(f"Dovecot sieve_before wiring failed: {result.summary()}")
    return result
