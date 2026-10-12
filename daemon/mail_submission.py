"""Identity-bound local mail submission and DKIM signing.

Loopback is shared by every tenant and is therefore never an identity. Local
application submission is authenticated with SO_PEERCRED by the socket
frontend; authenticated mailbox submission is checked against the panel mail
inventory by the Postfix filter frontend. Both paths converge here before a
hosted DKIM key can be used.
"""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
import os
from pathlib import Path
import subprocess

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import settings
from shared.db import make_engine
from shared.models import Account, DkimKey, Domain, MailDomain, MailUser
from shared.validation import validate_email_address

MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_HEADER_BYTES = 256 * 1024
MAX_RECIPIENTS = 100
SIGN_TIMEOUT_SECONDS = 30


class MailSubmissionError(ValueError):
    pass


def _session() -> Session:
    return Session(make_engine(read_only=True), future=True)


def account_for_uid(uid: int) -> tuple[int, str, str | None, set[str]]:
    if isinstance(uid, bool) or uid <= 0:
        raise MailSubmissionError("local mail submitter is not a hosting account")
    with _session() as db:
        account = db.scalar(select(Account).where(Account.uid == uid, Account.status == "active"))
        if account is None:
            raise MailSubmissionError("local mail submitter is not an active hosting account")
        domains = set(db.scalars(select(Domain.domain).where(
            Domain.account_id == account.id, Domain.suspended.is_(False))).all())
        domains.update(db.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all())
        if not domains:
            raise MailSubmissionError("account has no active hosted domain for mail submission")
        return account.id, account.username, account.primary_domain, {item.lower() for item in domains}


def account_for_mailbox(address: str) -> tuple[int, str, str | None, set[str]]:
    address = validate_email_address(address).lower()
    local, domain = address.rsplit("@", 1)
    with _session() as db:
        row = db.execute(
            select(Account, MailDomain)
            .join(MailDomain, MailDomain.account_id == Account.id)
            .join(MailUser, MailUser.mail_domain_id == MailDomain.id)
            .where(Account.status == "active", MailDomain.domain == domain,
                   MailUser.local_part == local, MailUser.domain == domain)
        ).first()
        if row is None:
            raise MailSubmissionError("envelope sender is not an active hosted mailbox")
        account, _mail_domain = row
        domains = set(db.scalars(select(Domain.domain).where(
            Domain.account_id == account.id, Domain.suspended.is_(False))).all())
        domains.update(db.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all())
        return account.id, account.username, account.primary_domain, {item.lower() for item in domains}


def _address(value: str, label: str) -> str:
    _display, parsed = parseaddr(value or "")
    try:
        return validate_email_address(parsed).lower()
    except Exception as exc:
        raise MailSubmissionError(f"{label} must contain one valid email address") from exc


def _header_addresses(message, name: str, *, required: bool = False) -> list[str]:
    values = message.get_all(name, [])
    if required and len(values) != 1:
        raise MailSubmissionError("message must contain exactly one From header")
    if name.lower() in {"from", "sender"} and len(values) > 1:
        raise MailSubmissionError(f"message must not contain duplicate {name} headers")
    parsed = []
    for _display, address in getaddresses([str(item) for item in values]):
        try:
            parsed.append(validate_email_address(address).lower())
        except Exception as exc:
            raise MailSubmissionError(f"{name} contains an invalid email address") from exc
    if values and not parsed:
        raise MailSubmissionError(f"{name} must contain a valid email address")
    return parsed


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[1].lower()


def _canonical_sender(username: str, primary_domain: str | None, owned_domains: set[str]) -> str:
    domain = primary_domain.lower() if primary_domain and primary_domain.lower() in owned_domains else sorted(owned_domains)[0]
    return f"{username}@{domain}"


def authorize_message(raw: bytes, envelope_sender: str | None, *, username: str,
                      primary_domain: str | None, owned_domains: set[str],
                      allow_local_placeholder: bool) -> tuple[bytes, str, str]:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MailSubmissionError("message exceeds the 25 MB submission limit")
    if b"\0" in raw:
        raise MailSubmissionError("message contains invalid binary header data")
    separator = raw.find(b"\r\n\r\n")
    if separator < 0:
        separator = raw.find(b"\n\n")
    if separator < 0 or separator > MAX_HEADER_BYTES:
        raise MailSubmissionError("message headers exceed the submission limit")
    try:
        message = BytesParser(policy=policy.SMTP).parsebytes(raw)
    except Exception as exc:
        raise MailSubmissionError("message headers could not be parsed") from exc
    for internal in ("X-Boron-Account", "X-Boron-Authorized-Domain"):
        while internal in message:
            del message[internal]

    canonical = _canonical_sender(username, primary_domain, owned_domains)
    placeholder_domains = {"localhost", (settings.panel_hostname or "").lower(), os.uname().nodename.lower()}
    envelope = (envelope_sender or "").strip()
    try:
        envelope_address = _address(envelope, "Envelope sender") if envelope else canonical
    except MailSubmissionError:
        raise
    local, envelope_domain = envelope_address.rsplit("@", 1)
    if envelope_domain not in owned_domains:
        if allow_local_placeholder and local == username and envelope_domain in placeholder_domains:
            envelope_address = canonical
            envelope_domain = _domain(canonical)
        else:
            raise MailSubmissionError("envelope sender must use a domain owned by this account")

    from_values = _header_addresses(message, "From")
    if not from_values:
        if not allow_local_placeholder:
            raise MailSubmissionError("message must contain exactly one From header")
        message["From"] = canonical
        from_values = [canonical]
    elif len(from_values) != 1:
        raise MailSubmissionError("message must contain exactly one From address")
    from_local, from_domain = from_values[0].rsplit("@", 1)
    if from_domain not in owned_domains:
        if allow_local_placeholder and from_local == username and from_domain in placeholder_domains:
            message.replace_header("From", canonical)
            from_domain = _domain(canonical)
        else:
            raise MailSubmissionError("From header must use a domain owned by this account")
    for name in ("Sender", "Resent-From", "Resent-Sender"):
        for address in _header_addresses(message, name):
            if _domain(address) not in owned_domains:
                raise MailSubmissionError(f"{name} must use a domain owned by this account")
    if envelope_domain != from_domain:
        raise MailSubmissionError("envelope sender and From header must use the same hosted domain")
    return message.as_bytes(policy=policy.SMTP), envelope_address, from_domain


def sign_if_configured(raw: bytes, domain: str) -> bytes:
    with _session() as db:
        key = db.scalar(select(DkimKey).where(
            DkimKey.domain == domain, DkimKey.signing_active.is_(True)))
        if key is None:
            return raw
        selector = key.selector
    key_path = Path(settings.dkim_base_dir) / domain / f"{selector}.private"
    return _sign(raw, domain, selector, key_path)


def _sign(raw: bytes, domain: str, selector: str, key_path: Path) -> bytes:
    if not key_path.is_file():
        raise MailSubmissionError("DKIM signing key is unavailable")
    try:
        result = subprocess.run(
            ["opendkim-testmsg", "-C", "-d", domain, "-s", selector, "-k", str(key_path)],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=SIGN_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MailSubmissionError("DKIM signing service is unavailable") from exc
    if result.returncode != 0 or len(result.stdout) > MAX_MESSAGE_BYTES + MAX_HEADER_BYTES:
        detail = result.stderr.decode(errors="replace").strip()[:300]
        raise MailSubmissionError(f"DKIM signing failed{': ' + detail if detail else ''}")
    return result.stdout


def sign_from_opendkim_table(raw: bytes, domain: str) -> bytes:
    """Sign from the root-maintained table without panel-DB privileges.

    The Postfix submission filter runs as the unprivileged OpenDKIM account;
    SMTP sender-login enforcement has already authenticated the mailbox.
    """
    table = Path("/etc/opendkim/boron/KeyTable")
    try:
        if table.stat().st_size > 1024 * 1024:
            raise MailSubmissionError("DKIM key table exceeds its safety limit")
        lines = table.read_text().splitlines()
    except OSError as exc:
        raise MailSubmissionError("DKIM signing table is unavailable") from exc
    suffix = f"._domainkey.{domain}"
    for line in lines:
        fields = line.split()
        if len(fields) != 2 or not fields[0].endswith(suffix):
            continue
        parts = fields[1].split(":", 2)
        if len(parts) != 3 or parts[0].lower() != domain:
            continue
        selector, raw_path = parts[1], parts[2]
        key_path = Path(raw_path)
        expected = (Path(settings.dkim_base_dir) / domain / f"{selector}.private").resolve()
        if key_path.resolve() != expected:
            raise MailSubmissionError("DKIM key path failed validation")
        return _sign(raw, domain, selector, expected)
    return raw


def submit_via_postfix(raw: bytes, envelope_sender: str, recipients: list[str]) -> None:
    if not recipients or len(recipients) > MAX_RECIPIENTS:
        raise MailSubmissionError("message must have between 1 and 100 recipients")
    clean_recipients = [validate_email_address(item) for item in recipients]
    try:
        result = subprocess.run(
            ["/usr/sbin/sendmail", "-G", "-i", "-f", envelope_sender, "--", *clean_recipients],
            input=raw, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MailSubmissionError("local mail queue is unavailable") from exc
    if result.returncode != 0:
        raise MailSubmissionError("local mail queue rejected the message")


def prepare_account_message(uid: int, raw: bytes, envelope_sender: str | None) -> tuple[bytes, str]:
    _account_id, username, primary, domains = account_for_uid(uid)
    normalized, envelope, domain = authorize_message(
        raw, envelope_sender, username=username, primary_domain=primary,
        owned_domains=domains, allow_local_placeholder=True,
    )
    return sign_if_configured(normalized, domain), envelope


def prepare_mailbox_message(mailbox: str, raw: bytes, envelope_sender: str) -> tuple[bytes, str]:
    _account_id, username, primary, domains = account_for_mailbox(mailbox)
    normalized, envelope, domain = authorize_message(
        raw, envelope_sender, username=username, primary_domain=primary,
        owned_domains=domains, allow_local_placeholder=False,
    )
    if envelope.lower() != mailbox.lower():
        raise MailSubmissionError("authenticated mailbox must match the envelope sender")
    return sign_if_configured(normalized, domain), envelope


def prepare_submission_filter(raw: bytes, envelope_sender: str) -> tuple[bytes, str]:
    """Validate a sender-login-authorized submission, then sign its domain."""
    mailbox = _address(envelope_sender, "Envelope sender")
    username, domain = mailbox.rsplit("@", 1)
    normalized, envelope, signed_domain = authorize_message(
        raw, mailbox, username=username, primary_domain=domain,
        owned_domains={domain}, allow_local_placeholder=False,
    )
    return sign_from_opendkim_table(normalized, signed_domain), envelope
