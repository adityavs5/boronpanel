"""DKIM keypair generation + SPF/DMARC sane-default publishing (Phase 3
feature 1).

Scope, deliberately conservative (matches the goal's own DONE WHEN bar --
"add DKIM record, verify public key resolves in DNS", not "verify outgoing
mail is actually signed"): this module generates a real RSA keypair, stores
the private key on disk, and publishes the public key as a DNS TXT record.
It does **not** install/configure a signing milter (e.g. OpenDKIM) in
Postfix's outbound path -- wiring an actual milter is a separate, larger
infra change (a new service, Postfix main.cf smtpd_milters/milter_protocol
changes, its own validate/reload path) that nothing in the goal's DONE WHEN
criteria requires, so it's flagged here and in CHECKPOINT-phase3-1.md as
explicitly out of scope for this pass rather than silently half-built.

Because outgoing mail isn't actually DKIM-signed yet, DMARC defaults to
p=none (monitor-only) -- publishing p=quarantine/reject before signing is
live would risk real mail from this domain being rejected by receivers
that enforce DMARC, since it would fail DKIM alignment. p=none is the
same conservative default HestiaCP/ISPConfig-style panels use for a
freshly-generated DKIM key (RESEARCH.md SS6 already found neither
incumbent panel does SPF automation at all -- this is deliberately more
cautious than either).
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import DkimKey

from daemon import powerdns
from daemon.dns_zone_lookup import find_managed_zone, label_within_zone
from daemon.procutil import run

logger = logging.getLogger("forgehostd.dkim")

DEFAULT_SELECTOR = "default"
KEY_BITS = "2048"


class DkimError(Exception):
    pass


def _domain_dir(domain: str) -> Path:
    return Path(settings.dkim_base_dir) / domain


def _private_key_path(domain: str, selector: str = DEFAULT_SELECTOR) -> Path:
    return _domain_dir(domain) / f"{selector}.private"


def _public_key_b64(private_key_path: Path) -> str:
    """Extract the base64 body of the RSA public key (no PEM header/
    footer/newlines -- that's the exact format a DKIM TXT record's `p=`
    tag expects, per RFC 6376)."""
    result = run(["openssl", "rsa", "-in", str(private_key_path), "-pubout"], timeout=15, check=True)
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip() and "PUBLIC KEY" not in ln]
    return "".join(lines)


def generate_keypair(domain: str, selector: str = DEFAULT_SELECTOR) -> Path:
    """Idempotent: reuses an existing on-disk private key rather than
    silently rotating it if called again for the same domain (a repeat
    mail-domain-creation attempt, e.g. after a prior partial failure,
    must not invalidate a DKIM key a receiver may already have cached/
    trusted)."""
    key_path = _private_key_path(domain, selector)
    if key_path.exists():
        return key_path

    domain_dir = _domain_dir(domain)
    domain_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = run(["openssl", "genrsa", "-out", str(key_path), KEY_BITS], timeout=20)
    if not result.ok:
        raise DkimError(f"openssl genrsa failed for '{domain}': {result.stderr.strip()}")
    key_path.chmod(0o600)
    return key_path


def dkim_record_name(domain: str, selector: str = DEFAULT_SELECTOR) -> str:
    return f"{selector}._domainkey.{domain}"


def dkim_txt_value(domain: str, selector: str = DEFAULT_SELECTOR) -> str:
    pubkey = _public_key_b64(_private_key_path(domain, selector))
    return f"v=DKIM1; k=rsa; p={pubkey}"


def setup_dns_signing(domain: str, selector: str = DEFAULT_SELECTOR) -> dict:
    """Called once when a mail domain is created (handlers_mail.py).
    Generates the keypair (idempotent), then -- only if `domain` falls
    under a Forgehost-managed DNS zone -- publishes SPF/DKIM/DMARC TXT
    records via the PowerDNS REST API (never raw SQL, same as every other
    DNS write in this project). If the zone isn't Forgehost-managed,
    the keypair is still generated/stored (so it exists for the operator
    to publish by hand, or for Forgehost to publish later if the zone
    is imported), and the response says so explicitly rather than
    pretending records were published."""
    existing = None
    with write_session() as session:
        existing = session.scalar(select(DkimKey).where(DkimKey.domain == domain))
        if existing is not None:
            selector = existing.selector

    generate_keypair(domain, selector)
    dkim_name = dkim_record_name(domain, selector)
    dkim_value = dkim_txt_value(domain, selector)
    spf_value = "v=spf1 mx a ~all"
    dmarc_value = f"v=DMARC1; p=none; rua=mailto:postmaster@{domain}"

    zone = find_managed_zone(domain)
    dns_published = False
    if zone:
        label = label_within_zone(domain, zone)
        dkim_label = f"{selector}._domainkey" if label == "@" else f"{selector}._domainkey.{label}"
        dmarc_label = "_dmarc" if label == "@" else f"_dmarc.{label}"
        try:
            powerdns.upsert_record(zone, label, "TXT", [_quote(spf_value)])
            powerdns.upsert_record(zone, dkim_label, "TXT", [_quote(dkim_value)])
            powerdns.upsert_record(zone, dmarc_label, "TXT", [_quote(dmarc_value)])
            dns_published = True
        except powerdns.PowerDnsError:
            logger.exception("failed to publish SPF/DKIM/DMARC records for '%s'", domain)

    with write_session() as session:
        row = session.scalar(select(DkimKey).where(DkimKey.domain == domain))
        if row is None:
            row = DkimKey(domain=domain, selector=selector, dns_published=dns_published)
            session.add(row)
        else:
            row.dns_published = dns_published

    return {
        "selector": selector,
        "dkim_record_name": dkim_name,
        "dkim_record_value": dkim_value,
        "spf_record_value": spf_value,
        "dmarc_record_name": f"_dmarc.{domain}",
        "dmarc_record_value": dmarc_value,
        "dns_published": dns_published,
    }


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def teardown_dns_signing(domain: str) -> None:
    """Called when a mail domain is deleted (handlers_mail.py) --
    idempotent: safe even if setup_dns_signing was never called or the
    zone was never managed here."""
    with write_session() as session:
        row = session.scalar(select(DkimKey).where(DkimKey.domain == domain))
        selector = row.selector if row is not None else DEFAULT_SELECTOR
        if row is not None:
            session.delete(row)

    zone = find_managed_zone(domain)
    if zone:
        label = label_within_zone(domain, zone)
        dkim_label = f"{selector}._domainkey" if label == "@" else f"{selector}._domainkey.{label}"
        dmarc_label = "_dmarc" if label == "@" else f"_dmarc.{label}"
        for rec_label in (label, dkim_label, dmarc_label):
            try:
                powerdns.delete_record(zone, rec_label, "TXT")
            except powerdns.PowerDnsError:
                pass  # best-effort cleanup; already gone or zone unreachable

    import shutil

    shutil.rmtree(_domain_dir(domain), ignore_errors=True)
