"""Decide when Postfix may treat a hosted mail domain as local.

Postfix's virtual_mailbox_domains map (``mail_domain WHERE active=1``) makes a
domain local for *every* sender on this server: other tenants' PHP mail(),
forwarders, cron mail and the panel's own notifications are all delivered to
the local mailboxes instead of the domain's real MX. Adding a domain to an
account proves nothing about owning it, so a customer who added someone
else's domain would capture that mail.

The rule here is the one cPanel calls "automatically detect" mail routing:
a domain in ``local`` routing mode is accepted locally only once its public
MX (as seen by public recursive resolvers, never this server's own PowerDNS
copy) resolves to an address of this server. Until then ``mail_domain.active``
stays 0, so Postfix routes mail for the domain to the MX that the rest of the
internet uses. Nothing external reaches this server for the domain before
its MX points here anyway, so a legitimate owner loses nothing: the cron
sweep in scripts/mail_local_delivery_reconcile.py turns local delivery on
within minutes of the MX change, and Postfix's default
``defer_unauth_destination`` makes senders retry in the meantime.

Operators can list domains in ``mail_local_delivery_trusted_domains`` (for
example a mailbox migration before the DNS cut-over) or turn the check off
with ``mail_local_delivery_requires_mx = false``.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import EmailRouting, MailDomain, ServerIp
from shared.validation import ValidationError, validate_domain

logger = logging.getLogger("borond.mail_local_delivery")

HERE = "here"
ELSEWHERE = "elsewhere"
UNKNOWN = "unknown"


def server_addresses() -> set[str]:
    """Public addresses this server receives mail on."""
    candidates: set[str] = set()
    try:
        from daemon import ipmanager

        candidates.update(item["address"] for item in ipmanager.detected_addresses())
    except Exception:
        logger.exception("could not list host addresses for the MX check")
    try:
        with write_session() as session:
            candidates.update(session.scalars(select(ServerIp.address).where(ServerIp.active.is_(True))).all())
    except Exception:
        logger.exception("could not read the server IP inventory for the MX check")
    result = set()
    for value in candidates:
        try:
            address = ipaddress.ip_address(str(value).split("%", 1)[0])
        except ValueError:
            continue
        # Loopback/private addresses are shared with countless other hosts;
        # a public MX pointing at 10.0.0.5 says nothing about this server.
        if address.is_global:
            result.add(address.compressed)
    if settings.server_public_ip:
        try:
            result.add(ipaddress.ip_address(settings.server_public_ip).compressed)
        except ValueError:
            pass
    return result


def _resolver():
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = list(settings.mail_mx_check_resolvers)
    resolver.timeout = 2
    resolver.lifetime = 5
    return resolver


# Interactive readiness checks (daemon/mail_dns.py) query public DNS with a
# short timeout and reuse answers briefly: a page that reloads, or several
# administrators looking at one domain, cost no new queries.
PUBLIC_ANSWER_TTL = 30.0
PUBLIC_ERROR_TTL = 10.0
_PUBLIC_CACHE_LIMIT = 512
_public_cache: dict[tuple[str, str], tuple[float, tuple[str, list[str]]]] = {}
_public_cache_lock = threading.Lock()


def public_lookup(name: str, rtype: str, deadline: float | None = None) -> tuple[str, list[str]]:
    """One record set as public resolvers return it.

    ("ok", values in presentation format), ("nxdomain", []), ("noanswer", [])
    or ("error", []) when the resolvers did not answer in time or the
    caller's monotonic ``deadline`` has passed.
    """
    key = (name.lower().rstrip("."), rtype.upper())
    now = time.monotonic()
    with _public_cache_lock:
        cached = _public_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    if deadline is not None and now >= deadline:
        return ("error", [])
    result = _public_query(*key)
    with _public_cache_lock:
        if len(_public_cache) >= _PUBLIC_CACHE_LIMIT:
            _public_cache.clear()
        _public_cache[key] = (now + (PUBLIC_ERROR_TTL if result[0] == "error" else PUBLIC_ANSWER_TTL), result)
    return result


def _public_query(name: str, rtype: str) -> tuple[str, list[str]]:
    import dns.exception
    import dns.resolver

    resolver = _resolver()
    resolver.timeout = 1.5
    resolver.lifetime = 3
    try:
        answer = resolver.resolve(name, rtype)
    except dns.resolver.NXDOMAIN:
        return ("nxdomain", [])
    except dns.resolver.NoAnswer:
        return ("noanswer", [])
    except dns.exception.DNSException:
        return ("error", [])
    return ("ok", sorted(row.to_text() for row in answer))


def public_mx_status(domain: str, local_addresses: set[str] | None = None) -> str:
    """HERE when the domain's most-preferred public MX resolves to this server,
    ELSEWHERE when DNS definitively says otherwise, UNKNOWN on lookup errors."""
    import dns.exception
    import dns.resolver

    domain = validate_domain(domain)
    local = server_addresses() if local_addresses is None else local_addresses
    if not local:
        return UNKNOWN
    resolver = _resolver()
    try:
        answer = resolver.resolve(domain, "MX")
    except dns.resolver.NXDOMAIN:
        return ELSEWHERE
    except dns.resolver.NoAnswer:
        hosts = [domain]  # RFC 5321 implicit MX: the domain's own address
    except dns.exception.DNSException:
        return UNKNOWN
    else:
        records = [(int(row.preference), str(row.exchange).rstrip(".").lower()) for row in answer]
        if not records or any(host == "" for _, host in records):
            return ELSEWHERE  # RFC 7505 null MX: the domain accepts no mail
        best = min(preference for preference, _ in records)
        hosts = sorted({host for preference, host in records if preference == best})
    found: set[str] = set()
    uncertain = False
    for host in hosts:
        for record_type in ("A", "AAAA"):
            try:
                rows = resolver.resolve(host, record_type)
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                continue
            except dns.exception.DNSException:
                uncertain = True
                continue
            for row in rows:
                try:
                    found.add(ipaddress.ip_address(row.to_text()).compressed)
                except ValueError:
                    continue
    if found & local:
        return HERE
    return UNKNOWN if uncertain else ELSEWHERE


def _trusted(domain: str) -> bool:
    for value in settings.mail_local_delivery_trusted_domains:
        try:
            if validate_domain(value) == domain:
                return True
        except ValidationError:
            continue
    return False


def local_delivery_allowed(domain: str) -> bool:
    """May Postfix accept ``domain`` as local right now?"""
    from daemon import domain_ownership

    domain = validate_domain(domain)
    if domain_ownership.reserved_reason(domain, include_infrastructure=False):
        return False
    if not settings.mail_local_delivery_requires_mx or _trusted(domain):
        return True
    return public_mx_status(domain) == HERE


def routing_mode(domain: str) -> str:
    with write_session() as session:
        row = session.scalar(select(EmailRouting).where(EmailRouting.domain == domain))
        return row.mode if row is not None else "local"


def reconcile(recheck_active: bool = False) -> dict:
    """Bring ``mail_domain.active`` in line with the rule above.

    Pending local-mode domains are switched on once their MX points here.
    Domains on the public-provider deny-list are always switched off. With
    ``recheck_active`` (an operator's one-off audit), active domains whose
    public MX definitively points elsewhere are switched off too; lookup
    errors never change anything.
    """
    from daemon import domain_ownership, mail

    with write_session() as session:
        domains = sorted(session.scalars(select(MailDomain.domain)).all())
        modes = {row.domain: row.mode for row in session.scalars(select(EmailRouting)).all()}
    report = {"activated": [], "deactivated": [], "pending": [], "errors": []}
    if not domains:
        return report
    local = server_addresses()
    for domain in domains:
        if modes.get(domain, "local") != "local":
            continue  # remote/backup modes are already not accepted locally
        try:
            current = mail.domain_active(domain)
            if current is None:
                continue
            if domain_ownership.reserved_reason(domain, include_infrastructure=False):
                if current:
                    mail.set_domain_active(domain, False)
                    report["deactivated"].append(domain)
                continue
            exempt = not settings.mail_local_delivery_requires_mx or _trusted(domain)
            if not current:
                if exempt or public_mx_status(domain, local) == HERE:
                    mail.set_domain_active(domain, True)
                    report["activated"].append(domain)
                else:
                    report["pending"].append(domain)
            elif recheck_active and not exempt and public_mx_status(domain, local) == ELSEWHERE:
                mail.set_domain_active(domain, False)
                report["deactivated"].append(domain)
        except Exception as exc:  # one broken domain must not stop the sweep
            logger.exception("mail local-delivery reconcile failed for %s", domain)
            report["errors"].append({"domain": domain, "error": str(exc)})
    return report
