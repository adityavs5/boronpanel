"""Mailbox autoresponders via Dovecot Sieve's `vacation` extension
(Phase 3 feature 4) -- deliberately not the classic Unix `vacation(1)`
binary, which needs a real system account + `.forward` file + its own
per-user database, none of which fits this project's virtual (non-Unix-
account) mailbox model at all. Dovecot's own Pigeonhole plugin implements
`vacation` natively at LMTP delivery time, including the "don't reply to
the same sender more than once per N days" loop-prevention Dovecot
itself tracks -- avoiding a whole class of mail-loop/spam bugs a
hand-rolled responder would have to solve from scratch.

The script is written directly to the mailbox's own Maildir home
(`~/.dovecot.sieve`, where `~` is Dovecot's own per-mailbox home from
dovecot-sql.conf.ext's user_query -- /var/vmail/<domain>/<local_part>),
validated with Pigeonhole's own `sievec` compiler before being moved into
place (this project's usual validate-before-apply discipline, applied
here without needing daemon/configtx.py's shared ConfigWriter machinery:
there's no service to reload -- Dovecot recompiles/reloads a mailbox's
active Sieve script on its own at the next delivery, same "no reload
needed for routine mail CRUD" property Phase e's SQL-backed mailboxes
already have).
"""
from __future__ import annotations

import os
import pwd
import tempfile
from pathlib import Path

from shared.config import settings

from daemon.mail import VMAIL_GID, VMAIL_UID, VMAIL_BASE
from daemon.procutil import run
from daemon.database_operations import serialized

SIEVEC_BIN = "/usr/bin/sievec"
SIEVE_FILENAME = ".dovecot.sieve"


class AutoresponderError(Exception):
    pass


def _mailbox_home(domain: str, local_part: str) -> Path:
    return Path(VMAIL_BASE) / domain / local_part


def _sieve_path(domain: str, local_part: str) -> Path:
    return _mailbox_home(domain, local_part) / SIEVE_FILENAME


def _escape_quoted_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _text_block(value: str) -> str:
    """Sieve's `text:` extended-string form -- avoids needing to escape
    quotes inside a free-form body, at the cost of "dot-stuffing" any
    line that would otherwise be confused with the block's own
    terminator (a lone "." on its own line ends the block, so a literal
    line consisting of just "." must be doubled, the same convention
    SMTP's own DATA command uses). Per RFC 5228 the terminating "."
    must be ALONE on its line -- nothing else may share that line, so
    this returns the block ending in a bare "." with no trailing
    semicolon/whitespace; the caller must put anything that follows
    (e.g. the statement's own terminating ";") on the NEXT line.
    Confirmed by real `sievec` validation: putting ". ;" on one line
    instead of "." then ";" on separate lines failed with "end of file
    before end of multi-line string" -- the parser didn't recognize the
    line as a bare terminator."""
    lines = value.replace("\r\n", "\n").split("\n")
    stuffed = [("." + ln if ln.startswith(".") else ln) for ln in lines]
    return "text:\n" + "\n".join(stuffed) + "\n."


def render_sieve_script(subject: str, body: str, start_date: str | None, end_date: str | None, mailbox_address: str | None = None) -> str:
    condition = None
    if start_date and end_date:
        condition = f'allof(currentdate :value "ge" "date" "{start_date}", currentdate :value "le" "date" "{end_date}")'
    elif start_date:
        condition = f'currentdate :value "ge" "date" "{start_date}"'
    elif end_date:
        condition = f'currentdate :value "le" "date" "{end_date}"'

    # `:addresses` tells Sieve's vacation extension this response is valid
    # for this mailbox's own address specifically, regardless of whether
    # that exact address literally appears in the message's To/Cc headers
    # -- without it, RFC 5230's default anti-loop safety check silently
    # discards the auto-reply for any mail that reached this mailbox any
    # way other than a direct, header-visible "To: local@domain" (e.g. via
    # a forwarder rule pointed at this mailbox, or BCC) -- confirmed live
    # via a real test message with no To: header at all, where Dovecot
    # logged "discarding vacation response ... no known (envelope)
    # recipient address found in message headers".
    addresses_clause = f' :addresses ["{mailbox_address}"]' if mailbox_address else ""
    vacation_stmt = (
        f'vacation :subject "{_escape_quoted_string(subject)}"{addresses_clause} :days 1 :mime {_text_block(body)}\n;'
    )

    lines = ['require ["vacation", "date", "relational"];', ""]
    if condition:
        lines.append(f"if {condition} {{")
        lines.append(vacation_stmt)
        lines.append("}")
    else:
        lines.append(vacation_stmt)
    return "\n".join(lines) + "\n"


def _validate_sieve_content(content: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sieve", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        result = run([SIEVEC_BIN, str(tmp_path)], timeout=15)
        if not result.ok:
            raise AutoresponderError(f"generated Sieve script failed validation: {result.stderr.strip() or result.stdout.strip()}")
    finally:
        tmp_path.unlink(missing_ok=True)
        # sievec compiles <name>.sieve -> <name>.svbin (extension
        # *replaced*, not appended -- confirmed empirically), if it
        # doesn't already exist.
        tmp_path.with_suffix(".svbin").unlink(missing_ok=True)


@serialized
def apply_autoresponder(domain: str, local_part: str, subject: str, body: str, start_date: str | None, end_date: str | None) -> None:
    content = render_sieve_script(subject, body, start_date, end_date, mailbox_address=f"{local_part}@{domain}")
    _validate_sieve_content(content)

    # Dovecot creates a mailbox's Maildir home *lazily*, at first delivery
    # or first login -- not at mailbox-creation time. Confirmed live: an
    # autoresponder set up right after creating a brand new mailbox (a
    # completely reasonable thing to do -- "set my out-of-office before
    # any mail arrives") failed here because the directory genuinely
    # didn't exist yet. Create it ourselves rather than requiring
    # Dovecot to have already done so.
    home = _mailbox_home(domain, local_part)
    if not Path(VMAIL_BASE, domain).is_dir():
        raise AutoresponderError(f"mail domain '{domain}' is not provisioned")
    home.mkdir(parents=True, exist_ok=True)
    os.chown(home, VMAIL_UID, VMAIL_GID)
    os.chmod(home, 0o700)

    target = _sieve_path(domain, local_part)
    tmp_target = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
    tmp_target.write_text(content)
    os.chown(tmp_target, VMAIL_UID, VMAIL_GID)
    os.chmod(tmp_target, 0o600)
    os.replace(tmp_target, target)

    # Drop any stale compiled cache from a previous script version --
    # Pigeonhole recompiles automatically if missing/out of date (it
    # checks mtime), but removing it here means a content change is
    # never served from a stale binary even for a moment.
    target.with_suffix(".svbin").unlink(missing_ok=True)


@serialized
def remove_autoresponder(domain: str, local_part: str) -> None:
    target = _sieve_path(domain, local_part)
    target.unlink(missing_ok=True)
    target.with_suffix(".svbin").unlink(missing_ok=True)
