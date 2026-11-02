"""Keep retained mail/DNS resources from changing tenants through a web name.

Every path that lets an account claim a hostname (domain.add, parked.add,
the primary-domain rename, account creation and archive imports) calls
require_available() before it writes anything. Three classes of name are
refused:

  * names this server needs for itself: the panel/webmail/phpMyAdmin/mail
    hostnames and the configured local nameservers, plus anything beneath
    them;
  * public suffixes (``co.uk``, ``github.io``) and a curated list of large
    public mailbox providers (``gmail.com``, ``outlook.com``...). Claiming
    one of those would make Postfix treat the provider as a local mail
    domain and capture mail other tenants and the panel send to it. The
    operator can extend the list with ``reserved_domains`` in boron.toml;
  * names that overlap a name another account already holds. Overlap means
    the same name, an ancestor or a descendant: PowerDNS answers from the
    most specific zone and OpenLiteSpeed routes ``www.`` to the most
    specific vhost, so a pre-claimed ``www.victim.example`` would take over
    traffic when the real owner later adds ``victim.example``.

Proving that the claimant controls an arbitrary external domain is not done
here. Mail acceptance for a newly provisioned domain is instead gated on its
public MX in daemon/mail_local_delivery.py.
"""
from __future__ import annotations

import functools

from sqlalchemy import select

from shared.config import settings
from shared.models import DnsServerSettings, DnsZone, Domain, MailDomain
from shared.validation import ValidationError, validate_domain

# Large public mailbox providers. A hosted account never legitimately owns
# these, and accepting one as a local mail domain would capture outbound
# mail from every other tenant. Each entry also covers its subdomains.
PUBLIC_MAIL_PROVIDER_DOMAINS = frozenset({
    # Google
    "gmail.com", "googlemail.com", "google.com",
    # Microsoft
    "outlook.com", "hotmail.com", "hotmail.co.uk", "hotmail.fr", "hotmail.de", "hotmail.it",
    "hotmail.es", "live.com", "live.co.uk", "live.fr", "msn.com", "passport.com",
    "microsoft.com", "office365.com", "onmicrosoft.com",
    # Yahoo / AOL
    "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "yahoo.co.jp", "yahoo.fr", "yahoo.de",
    "yahoo.es", "yahoo.it", "yahoo.ca", "yahoo.com.au", "yahoo.com.br", "ymail.com",
    "rocketmail.com", "aol.com", "aim.com",
    # Apple
    "icloud.com", "me.com", "mac.com", "apple.com",
    # Privacy and independent providers
    "proton.me", "protonmail.com", "protonmail.ch", "pm.me", "tutanota.com", "tutanota.de",
    "tuta.io", "tuta.com", "fastmail.com", "fastmail.fm", "hey.com", "mailbox.org",
    "posteo.de", "hushmail.com", "zoho.com", "zohomail.com", "zoho.eu",
    # European and Russian providers
    "gmx.com", "gmx.net", "gmx.de", "gmx.at", "gmx.ch", "web.de", "mail.com", "t-online.de",
    "freenet.de", "orange.fr", "wanadoo.fr", "free.fr", "laposte.net", "sfr.fr", "libero.it",
    "virgilio.it", "btinternet.com", "sky.com", "mail.ru", "inbox.ru", "list.ru", "bk.ru",
    "yandex.com", "yandex.ru", "ya.ru",
    # Asian providers
    "qq.com", "foxmail.com", "163.com", "126.com", "yeah.net", "sina.com", "sohu.com",
    "naver.com", "daum.net", "hanmail.net", "rediffmail.com",
    # North American ISPs
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "bellsouth.net", "cox.net",
    "charter.net",
})


def _within(name: str, base: str) -> bool:
    return name == base or name.endswith("." + base)


@functools.lru_cache(maxsize=1)
def _suffix_extractor():
    import tldextract

    # The bundled Public Suffix List snapshot only: no network fetch and no
    # cache directory written from the root daemon.
    return tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)


def is_public_suffix(domain: str) -> bool:
    try:
        parts = _suffix_extractor()(domain)
    except Exception:  # pragma: no cover - the snapshot ships with the package
        return False
    return bool(parts.suffix) and not parts.domain


def infrastructure_hostnames(session=None) -> set[str]:
    """Hostnames this server answers for on its own behalf."""
    names = {settings.panel_hostname, settings.webmail_hostname, settings.pma_hostname,
             settings.mail_hostname}
    if session is not None:
        row = session.get(DnsServerSettings, 1)
        names.update((row.local_nameservers or []) if row else [])
    result = set()
    for name in names:
        try:
            result.add(validate_domain(str(name or "").rstrip(".")))
        except ValidationError:
            continue
    return result


def reserved_reason(domain: str, session=None, include_infrastructure: bool = True) -> str | None:
    """Why ``domain`` can never belong to a hosting account, or None.

    Mail checks pass ``include_infrastructure=False``: the operator may run
    mailboxes on the panel's own hostname, which no account can claim anyway.
    """
    domain = validate_domain(domain)
    if include_infrastructure and any(_within(domain, name) for name in infrastructure_hostnames(session)):
        return "This hostname is reserved for a panel service"
    if is_public_suffix(domain):
        return "A public domain suffix cannot be added as a hosted domain"
    extra = set()
    for value in settings.reserved_domains:
        try:
            extra.add(validate_domain(value))
        except ValidationError:
            continue
    if any(_within(domain, name) for name in PUBLIC_MAIL_PROVIDER_DOMAINS | extra):
        return "This domain belongs to a public mail or service provider and cannot be hosted here"
    return None


def require_not_reserved(domain: str, session=None) -> str:
    domain = validate_domain(domain)
    reason = reserved_reason(domain, session)
    if reason:
        raise ValidationError(reason)
    return domain


def _ancestors(domain: str) -> list[str]:
    labels = domain.split(".")
    return [".".join(labels[index:]) for index in range(1, len(labels) - 1)]


def _foreign(owner_id, account_id: int | None) -> bool:
    return account_id is None or owner_id != account_id


def require_no_foreign_overlap(session, domain: str, account_id: int | None) -> None:
    """Refuse a name whose ancestor or descendant is held by another account.

    Exact matches are left to the callers' existing checks (they report the
    established "already in use" and zone-ownership errors).
    """
    domain = validate_domain(domain)
    ancestors = [name for name in _ancestors(domain) if not is_public_suffix(name)]
    for model, column in ((Domain, Domain.domain), (MailDomain, MailDomain.domain), (DnsZone, DnsZone.zone)):
        if ancestors:
            for row in session.scalars(select(model).where(column.in_(ancestors))).all():
                if _foreign(row.account_id, account_id):
                    raise ValidationError(
                        "This name is inside a domain that belongs to another account")
        descendants = session.scalars(
            select(model).where(column.endswith("." + domain, autoescape=True))).all()
        for row in descendants:
            if _foreign(row.account_id, account_id):
                raise ValidationError(
                    "Another account already uses a subdomain of this domain. Ask the server "
                    "administrator to remove or transfer it before adding this domain")


def require_available(session, domain: str, account_id: int | None) -> None:
    domain = require_not_reserved(domain, session)
    mail = session.scalar(select(MailDomain).where(MailDomain.domain == domain))
    if mail is not None and (account_id is None or mail.account_id != account_id):
        raise ValidationError("This domain has mail resources owned by another account")
    zones = [zone for zone in session.scalars(select(DnsZone)).all()
             if domain == zone.zone or domain.endswith('.' + zone.zone)]
    if zones:
        parent = max(zones, key=lambda zone: len(zone.zone))
        if account_id is None or parent.account_id != account_id:
            raise ValidationError("The matching DNS zone belongs to another account")
    require_no_foreign_overlap(session, domain, account_id)
