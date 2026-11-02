"""Postfix sender-identity hardening shared by install, upgrade and DKIM.

Loopback and the local queue are shared by every tenant, so neither is an
identity. This module closes the three paths that skipped the identity-bound
frontends (mail_submission.py):

* port 25 no longer relays for loopback/mynetworks clients; it accepts only
  mail for hosted destinations, exactly as it would from the internet;
* Postfix's own sendmail/postdrop accept submissions only from service
  accounts. Postfix's binary is diverted to ``/usr/sbin/sendmail.postfix``
  and ``/usr/sbin/sendmail`` becomes a small dispatcher that routes every
  other local user (PHP via OLS hostexec, cron, SSH) to the SO_PEERCRED
  submission socket, which only accepts and DKIM-signs senders on domains
  owned by that user's hosting account;
* (daemon/mail_egress.py) only root and Postfix may open TCP/25, including
  loopback, so a tenant cannot launder spoofed mail through its own forwarders.

The module deliberately imports only the standard library at import time:
the unprivileged Postfix signing filter imports mail_submission, which uses
``postfix_sendmail()`` from here.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

SENDMAIL_PATH = Path("/usr/sbin/sendmail")
POSTFIX_SENDMAIL = Path("/usr/sbin/sendmail.postfix")
MSMTP_BINARY = "/usr/bin/msmtp"
MSMTP_CONFIG = "/etc/boron/msmtprc"
DISPATCHER_MARKER = "# Managed by Boron: local sendmail identity dispatcher"

# Accounts that may hand mail straight to the Postfix queue. root covers the
# identity socket service, borond notifications and system cron; opendkim
# reinjects signed submission mail; debian-spamd reinjects scanned inbound
# mail; vmail sends Sieve vacation/redirect mail from Dovecot LMTP. No
# hosting account is ever on this list.
TRUSTED_SUBMIT_USERS = ("root", "postfix", "opendkim", "debian-spamd", "vmail")

# main.cf values that make port 25 a receive-only service for hosted
# domains. Authenticated relay is granted only by the submission/inet
# override (permit_sasl_authenticated + sender-login enforcement).
POSTFIX_MAIN_POLICY = {
    "mynetworks": "127.0.0.1/32 [::1]/128",
    "smtpd_relay_restrictions": "reject_unauth_destination",
    "authorized_submit_users": ", ".join(TRUSTED_SUBMIT_USERS),
}


class MailHardeningError(RuntimeError):
    pass


def postfix_sendmail() -> str:
    """Postfix's real sendmail, bypassing the dispatcher when installed."""
    return str(POSTFIX_SENDMAIL) if POSTFIX_SENDMAIL.is_file() else str(SENDMAIL_PATH)


def render_dispatcher() -> str:
    trusted = "|".join(TRUSTED_SUBMIT_USERS)
    return f"""#!/bin/sh
{DISPATCHER_MARKER}.
# Postfix's own sendmail lives at {POSTFIX_SENDMAIL} (dpkg-divert) and only
# accepts mail from the service accounts in authorized_submit_users. Every
# other local user submits through the identity-bound Boron socket, which
# only accepts and DKIM-signs senders on domains that user's account owns.
POSTFIX={POSTFIX_SENDMAIL}
case "${{0##*/}}" in
    mailq) exec "$POSTFIX" -bp "$@" ;;
    newaliases) exec "$POSTFIX" -bi "$@" ;;
esac
case "$(/usr/bin/id -run 2>/dev/null)" in
    {trusted}) exec "$POSTFIX" "$@" ;;
esac
exec {MSMTP_BINARY} -C {MSMTP_CONFIG} "$@"
"""


def _is_dispatcher(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(512)
    except OSError:
        return False
    return DISPATCHER_MARKER.encode() in head


def dispatcher_installed() -> bool:
    return POSTFIX_SENDMAIL.is_file() and _is_dispatcher(SENDMAIL_PATH)


def _write_executable(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.boron-new.{os.getpid()}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o755)
        if os.geteuid() == 0:
            os.chown(temporary, 0, 0)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_sendmail_dispatcher(run=None) -> None:
    """Divert Postfix's sendmail once, then (re)write the dispatcher."""
    if run is None:
        from daemon.procutil import run
    if not POSTFIX_SENDMAIL.is_file():
        if _is_dispatcher(SENDMAIL_PATH):
            raise MailHardeningError(
                f"{SENDMAIL_PATH} is the Boron dispatcher but {POSTFIX_SENDMAIL} is missing; "
                "reinstall the postfix package")
        if not SENDMAIL_PATH.is_file():
            raise MailHardeningError("Postfix sendmail is not installed")
        if shutil.which("dpkg-divert") is None:
            raise MailHardeningError("dpkg-divert is unavailable")
        run(["dpkg-divert", "--local", "--rename", "--divert", str(POSTFIX_SENDMAIL),
             "--add", str(SENDMAIL_PATH)], timeout=30, check=True)
        if not POSTFIX_SENDMAIL.is_file():
            raise MailHardeningError("Postfix sendmail divert did not take effect")
    _write_executable(SENDMAIL_PATH, render_dispatcher())


def configure_postfix_policy(run=None) -> None:
    """Install the dispatcher, then restrict relay and queue submission.

    The dispatcher must exist before authorized_submit_users is narrowed, or
    PHP mail() for every native-namespace site would start failing.
    """
    if run is None:
        from daemon.procutil import run
    install_sendmail_dispatcher(run)
    for name, value in POSTFIX_MAIN_POLICY.items():
        run(["postconf", "-e", f"{name}={value}"], timeout=15, check=True)


if __name__ == "__main__":  # pragma: no cover - installer entry point
    configure_postfix_policy()
