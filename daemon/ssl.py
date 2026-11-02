"""SSL automation via certbot (Phase f).

ARCHITECTURE.md SS8: DNS-01 (via the `certbot-dns-powerdns` plugin) when
Boron manages the domain's zone, HTTP-01 with an unprivileged file hook otherwise -- every vhost declares a
`/.well-known/acme-challenge/` context pointed at the account's docroot, no
extra vhost config needed here. The Certbot process retains root-only key
access; only challenge-file creation/cleanup runs as the owning account. `--deploy-hook` points at
`scripts/ssl_deploy_hook.py`, a standalone script (not a daemon method)
because certbot's own renewal timer invokes it outside borond's
process -- it reuses the same shared/daemon modules directly rather than
going through the Unix-socket RPC (it already runs as root, same as
borond, so there's no privilege boundary to cross).
"""
from __future__ import annotations

import datetime as dt
import http.client
import ipaddress
import logging
import os
import socket
import ssl as ssl_library
import secrets
from pathlib import Path

from cryptography import x509
from sqlalchemy import delete, select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, DnsZone, SslExpiryNotice, WordPressSiteState
from shared.validation import ValidationError, validate_domain, validate_username

from daemon import audit, cloudflare_accounts, dnsprovider, events, safeio
from daemon.ols import letsencrypt_cert_paths
from daemon.procutil import run

DEPLOY_HOOK_SCRIPT = "/opt/boron/scripts/ssl_deploy_hook.py"
_VENV_PYTHON = str(Path(settings.certbot_bin).parent / "python")
# What the rest of the internet (and Let's Encrypt) resolves: never this
# server's own PowerDNS copy of a zone, which may not be delegated at all.
PUBLIC_RESOLVERS = ("1.1.1.1", "8.8.8.8")

logger = logging.getLogger("borond.ssl")


class SslError(Exception):
    pass


def _service_hostname(domain: str) -> bool:
    return domain in {value for value in (
        settings.panel_hostname, settings.webmail_hostname, settings.pma_hostname,
        settings.mail_hostname,
    ) if value}


def _resolved_addresses(hostname: str) -> list[str]:
    try:
        return sorted({item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)})
    except socket.gaierror:
        return []


def _http_challenge_preflight(hostname: str, addresses: list[str]) -> dict:
    if hostname != settings.webmail_hostname:
        return {"checked": False, "ok": True, "detail": "DNS-01 or non-webmail service"}
    token = "boron-preflight-" + secrets.token_hex(8)
    directory = Path(settings.webmail_docroot) / ".well-known" / "acme-challenge"
    directory.mkdir(parents=True, exist_ok=True)
    # The daemon runs with UMask=0027; the web server (nobody) must be able
    # to traverse the directories and read the probe, as it reads certbot's.
    for part in (directory.parent, directory):
        os.chmod(part, 0o755)
    probe = directory / token
    probe.write_text(token)
    os.chmod(probe, 0o644)
    try:
        for address in addresses:
            if ":" in address:
                continue
            try:
                connection = http.client.HTTPConnection(address, 80, timeout=5)
                connection.request("GET", f"/.well-known/acme-challenge/{token}", headers={"Host": hostname})
                response = connection.getresponse()
                payload = response.read(1024).decode(errors="replace")
                connection.close()
                if response.status == 200 and payload.strip() == token:
                    return {"checked": True, "ok": True, "address": address}
            except OSError:
                continue
        return {"checked": True, "ok": False, "detail": "ACME challenge file was not served by the webmail vhost"}
    finally:
        probe.unlink(missing_ok=True)


def _sni_probe(hostname: str, addresses: list[str], port: int = 443) -> dict:
    context = ssl_library.create_default_context()
    errors = []
    for address in addresses:
        try:
            with socket.create_connection((address, port), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname=hostname) as secure:
                    cert = secure.getpeercert()
                    ssl_library.match_hostname(cert, hostname)
                    return {"ok": True, "address": address, "protocol": secure.version()}
        except (OSError, ssl_library.SSLError, ssl_library.CertificateError) as exc:
            errors.append(f"{address}: {type(exc).__name__}")
    return {"ok": False, "detail": "; ".join(errors) or "No address available"}


def service_certificate_diagnostics(params: dict) -> dict:
    hostname = validate_domain(params["domain"])
    if not _service_hostname(hostname):
        raise SslError("Diagnostics are available only for configured service hostnames")
    addresses = _resolved_addresses(hostname)
    expected = [settings.server_public_ip] if settings.server_public_ip else []
    mismatched = [address for address in addresses if expected and address not in expected]
    dns_ok = bool(addresses) and (not expected or settings.server_public_ip in addresses) and not mismatched
    details = _cert_file_details(hostname)
    challenge = _http_challenge_preflight(hostname, addresses)
    probe_port = 993 if hostname == settings.mail_hostname and hostname != settings.webmail_hostname else 443
    sni = _sni_probe(hostname, addresses, probe_port) if details else {"ok": False, "detail": "No certificate installed"}
    return {
        "domain": hostname,
        "dns": {"ok": dns_ok, "expected": expected, "resolved": addresses, "mismatched": mismatched},
        "http_challenge": challenge,
        "certificate": details,
        "renewal": {"enabled": _auto_renew_enabled(hostname)},
        "sni": sni,
        "probe_port": probe_port,
        "ols_deployed": details is not None and sni.get("ok", False),
    }


def _dns01_args() -> list[str]:
    """certbot-dns-powerdns plugin args -- shared by the auto-detected
    DNS-01 path (_challenge_plan, when a domain's zone happens to be
    Boron-managed) and the explicit wildcard-issuance path
    (issue_wildcard_certificate), which REQUIRES DNS-01 unconditionally
    (Let's Encrypt has no HTTP-01 path for wildcard names at all -- this
    isn't a Boron design choice, it's an ACME protocol constraint)."""
    return [
        "--authenticator", "dns-powerdns",
        "--dns-powerdns-credentials", settings.powerdns_credentials_file,
        "--dns-powerdns-propagation-seconds", "30",
    ]


def _cf_credentials_path(cf_account_pk: int | None) -> str:
    """One certbot-dns-cloudflare credentials file per pool account (or the
    configured single file for a legacy/None account), so multi-account
    issuance uses the right token. certbot stores this path in the renewal
    conf, so `certbot renew` keeps using the correct per-domain
    authenticator + creds automatically."""
    if cf_account_pk is not None:
        base = settings.cloudflare_credentials_file
        return base[:-4] + f"-{cf_account_pk}.ini" if base.endswith(".ini") else f"{base}-{cf_account_pk}"
    return settings.cloudflare_credentials_file


def _ensure_cf_credentials(row) -> str:
    """Write the certbot-dns-cloudflare credentials INI (0600) for the pool
    account serving `row`'s zone, and return its path."""
    token = cloudflare_accounts.token_for_id(getattr(row, "cf_account_id", None)) or settings.cloudflare_api_token
    if not token:
        raise SslError(f"no Cloudflare API token available for zone '{row.zone}' -- cannot issue via DNS-01")
    path = Path(_cf_credentials_path(getattr(row, "cf_account_id", None)))
    path.parent.mkdir(parents=True, exist_ok=True)
    safeio.secure_replace_file(str(path.parent), path.name,
                              f"dns_cloudflare_api_token = {token}\n".encode(), os.geteuid(), os.getegid())
    return str(path)


def _cf_dns01_args(row) -> list[str]:
    return [
        "--authenticator", "dns-cloudflare",
        "--dns-cloudflare-credentials", _ensure_cf_credentials(row),
        "--dns-cloudflare-propagation-seconds", "30",
    ]


def _local_dns_is_publicly_authoritative(zone: str) -> bool:
    """A local zone copy does not establish public DNS delegation.

    Query public recursive resolvers so the server's own PowerDNS zone cannot
    make an undelegated copy appear authoritative. Ordinary sites fall back
    to HTTP validation; wildcard requests must still have working DNS control.
    """
    import dns.exception
    import dns.resolver
    import httpx
    try:
        records = dnsprovider.list_records(zone)
        expected = {value.lower().rstrip('.') for row in records
                    if row.get('type') == 'NS' and row.get('name', '').lower().rstrip('.') == zone
                    for value in row.get('values', [])}
        if not expected:
            return False
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = list(PUBLIC_RESOLVERS)
        resolver.timeout = 1.5
        answer = resolver.resolve(zone, 'NS', lifetime=3)
        actual = {str(row.target).lower().rstrip('.') for row in answer}
        return bool(actual) and actual <= expected
    except (dns.exception.DNSException, OSError, httpx.HTTPError, dnsprovider.PowerDnsError):
        return False


def _dns01_plan(domain: str) -> list[str] | None:
    """Phase 2+3 feature 5: pick the DNS-01 authenticator by the zone's live
    provider. A Cloudflare-ACTIVE zone MUST use dns-cloudflare (Cloudflare is
    authoritative, so the _acme-challenge TXT has to be created there, not in
    the stale local PowerDNS zone Boron keeps as the revert target). A
    local Boron-managed zone uses dns-powerdns. Anything else -> None
    (no DNS-01 hook available)."""
    from daemon.dns_zone_lookup import find_managed_zone
    zone = find_managed_zone(domain) or domain
    row = dnsprovider.cloudflare_zone_row(zone)
    if row is not None and row.status == "active":
        return _cf_dns01_args(row)
    with write_session() as session:
        if session.scalar(select(DnsZone).where(DnsZone.zone == zone)) is not None:
            if _local_dns_is_publicly_authoritative(zone):
                return _dns01_args()
    return None


def _challenge_plan(domain: str) -> tuple[str, list[str]]:
    """Returns (mode, certbot_args). mode is "dns-01" or "http-01", purely
    for logging/the RPC response -- the actual behavior is in the args."""
    # Phase 2 feature 3: Roundcube's static webmail hostname isn't a
    # per-account Domain row -- it's server infrastructure, same category
    # as the panel's own TLS cert. Its webroot is settings.webmail_docroot,
    # not something looked up from the accounts/domains tables.
    if domain == settings.webmail_hostname:
        from daemon.acme_http import challenge_args
        return "http-01", challenge_args()

    if domain == settings.mail_hostname:
        # A mail hostname of its own has no vhost: acme_http and the OLS
        # mapping answer ACME requests only for the webmail hostname, so
        # HTTP-01 cannot succeed. DNS-01 works when its zone is here and
        # publicly authoritative, or on Cloudflare.
        dns01 = _dns01_plan(domain)
        if dns01 is not None:
            return "dns-01", dns01
        raise SslError(
            f"The mail hostname {domain} is not served over HTTP by this server, so its certificate "
            "needs a DNS-01 check: host its DNS zone on this server (delegated to it publicly) or on "
            "Cloudflare, or use the webmail hostname as the mail hostname."
        )

    if domain == settings.pma_hostname:
        return "http-01", ["--webroot", "-w", settings.pma_docroot]

    dns01 = _dns01_plan(domain)  # CF-active -> dns-cloudflare, local zone -> dns-powerdns
    if dns01 is not None:
        return "dns-01", dns01

    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain))
    if domain_row is None:
        raise SslError(f"domain '{domain}' is not provisioned in Boron")
    from daemon.acme_http import challenge_args
    return "http-01", challenge_args()


def _server_addresses() -> set[str]:
    """Every address this server answers on (ipmanager's inventory, which
    includes the configured primary address of a NAT install)."""
    from daemon import ipmanager

    return {item["address"] for item in ipmanager.detected_addresses()}


def _public_addresses(hostname: str) -> set[str] | None:
    """A/AAAA answers for hostname from public resolvers, CNAMEs followed.
    Empty when the name has no address; None when public DNS could not be
    asked, so a resolver outage is never mistaken for "points elsewhere"."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = list(PUBLIC_RESOLVERS)
    resolver.timeout = 1.5
    found: set[str] = set()
    for record_type in ("A", "AAAA"):
        try:
            answer = resolver.resolve(hostname, record_type, lifetime=3)
        except dns.resolver.NXDOMAIN:
            return found
        except dns.resolver.NoAnswer:
            continue
        except dns.exception.DNSException:
            return None
        for row in answer:
            try:
                found.add(ipaddress.ip_address(row.to_text()).compressed)
            except ValueError:
                continue
    return found


def _cloudflare_record_points_here(hostname: str, local: set[str]) -> bool | None:
    """A Cloudflare-proxied name resolves to Cloudflare's edge, so public DNS
    cannot show that it reaches this server; for a zone Boron runs on
    Cloudflare the record content decides. None when the name is not in a
    Cloudflare-active zone or its records could not be read."""
    from daemon.dns_zone_lookup import find_managed_zone

    zone = find_managed_zone(hostname)
    row = dnsprovider.cloudflare_zone_row(zone) if zone else None
    if row is None or row.status != "active":
        return None
    try:
        records = dnsprovider.list_records(zone)
    except Exception:  # noqa: BLE001 - an unreachable API only means "unknown"
        logger.warning("could not read the Cloudflare records of %s", zone, exc_info=True)
        return None
    rrsets = {(str(item.get("name", "")).lower().rstrip("."), item.get("type")): item.get("values") or []
              for item in records}
    name = hostname
    for _ in range(4):  # a short CNAME chain inside the zone, e.g. www -> apex
        values = [*rrsets.get((name, "A"), []), *rrsets.get((name, "AAAA"), [])]
        if values:
            try:
                return {ipaddress.ip_address(str(value)).compressed for value in values} <= local
            except ValueError:
                return False
        targets = rrsets.get((name, "CNAME"), [])
        if not targets:
            return False
        name = str(targets[0]).lower().rstrip(".")
        if name != zone and not name.endswith("." + zone):
            return False
    return False


def _points_here(hostname: str, local: set[str]) -> bool | None:
    """True when every public address of hostname belongs to this server,
    False when DNS shows it does not (or it does not resolve), None when
    that could not be determined."""
    addresses = _public_addresses(hostname)
    if addresses and addresses <= local:
        return True
    proxied = _cloudflare_record_points_here(hostname, local)
    if proxied is not None:
        return proxied
    return None if addresses is None else False


def _listener_aliases(session, domain: str) -> list[str]:
    """Other names the domain's vhost answers for. daemon/ols.py
    (_all_active_vhosts) maps www.<domain> to it unless that name is a site
    of its own or a panel service hostname."""
    if domain.startswith("www."):
        return []
    alias = "www." + domain
    if alias in {settings.webmail_hostname, settings.pma_hostname, settings.panel_hostname}:
        return []
    if session.scalar(select(Domain.id).where(Domain.domain == alias)) is not None:
        return []
    return [alias]


def _certificate_san(domain: str) -> set[str]:
    """DNS names on the domain's current certificate (empty when none)."""
    _key, cert_path = letsencrypt_cert_paths(domain)
    try:
        cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, x509.ExtensionNotFound):
        return set()
    return {name.lower() for name in names.get_values_for_type(x509.DNSName)}


def _certificate_names(domain: str) -> tuple[list[str], list[dict]]:
    """Names to request for a site's certificate, and the aliases left out.

    The certificate covers the domain plus every alias its vhost answers for
    (www.<domain>) that resolves to this server, so https://www works
    wherever visitors can reach it. An alias pointing elsewhere would only
    fail the ACME validation of the whole certificate, so it is left out and
    reported. When public DNS cannot be asked, an alias already on the
    current certificate is kept: a reissue never drops a working name
    because of a resolver outage (certbot's own renewals reuse the names).
    A WordPress site whose address is https://www.<domain> always needs www.
    Service hostnames (no Domain row) get a single-name certificate.
    """
    with write_session() as session:
        site = session.scalar(select(Domain).where(Domain.domain == domain))
        if site is None:
            return [domain], []
        aliases = _listener_aliases(session, domain)
        www_required = session.scalar(select(WordPressSiteState.id).where(
            WordPressSiteState.account_id == site.account_id,
            WordPressSiteState.domain == domain,
            WordPressSiteState.site_url.like('https://www.' + domain + '%'))) is not None
    if www_required and "www." + domain not in aliases:
        raise ValidationError('The www hostname belongs to a separate site; update the WordPress address before requesting its certificate')
    names, skipped = [domain], []
    local: set[str] | None = None
    current: set[str] | None = None
    for alias in aliases:
        if www_required:
            names.append(alias)
            continue
        if local is None:
            local = _server_addresses()
        here = _points_here(alias, local)
        if here is None:
            if current is None:
                current = _certificate_san(domain)
            here = True if alias in current else None
        if here:
            names.append(alias)
        else:
            skipped.append({"name": alias, "reason": "does not point to this server" if here is False
                            else "could not be checked in public DNS"})
    return names, skipped


def issue_certificate(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    if not settings.letsencrypt_email:
        raise SslError("letsencrypt_email is not set in boron.toml")

    if _service_hostname(domain):
        with audit.step("SSL: verify service hostname preflight"):
            preflight = service_certificate_diagnostics({"domain": domain})
        # Both refusals are for the administrator to fix, so they answer 400.
        if not preflight["dns"]["ok"]:
            raise ValidationError(
                f"DNS for '{domain}' must point only to this server before issuance "
                f"(resolved: {', '.join(preflight['dns']['resolved']) or 'nothing'})"
            )
        if domain == settings.webmail_hostname and not preflight["http_challenge"]["ok"]:
            raise ValidationError(preflight["http_challenge"].get("detail", "Webmail ACME preflight failed"))

    with audit.step("SSL: choose certificate names"):
        names, skipped_names = _certificate_names(domain)
    with audit.step("SSL: select ACME challenge"):
        mode, challenge_args = _challenge_plan(domain)
    audit.change(
        "certificate_status",
        "present" if Path(letsencrypt_cert_paths(domain)[1]).exists() else "missing",
        "issued",
    )
    # Phase 3 feature 8: the SSL dashboard's "renew" button targets a
    # domain that may already have a perfectly valid, non-expiring-soon
    # cert -- certbot's own default behavior is to silently skip
    # reissuing in that case ("Certificate not yet due for renewal"),
    # which would make a "one-click renew" button appear to do nothing.
    # `--force-renewal` is exactly certbot's own documented escape hatch
    # for "the operator explicitly asked for this, do it regardless of
    # the normal expiry-window check" -- only ever passed when the
    # caller explicitly asks (never on the plain "issue" path, where a
    # domain has no cert yet and there's nothing to force).
    force_args = ["--force-renewal"] if params.get("force") else []

    args = [
        settings.certbot_bin, "certonly",
        "--non-interactive",
        "--agree-tos",
        "--email", settings.letsencrypt_email,
        "--cert-name", domain,
        *(["--expand"] if len(names) > 1 else []),
        *(argument for name in names for argument in ("-d", name)),
        "--deploy-hook", f"{_VENV_PYTHON} {DEPLOY_HOOK_SCRIPT}",
        *force_args,
        *challenge_args,
    ]
    with audit.step("SSL: request certificate from ACME") as action:
        result = action.capture(run(args, timeout=180))
    if not result.ok:
        raise SslError(f"certbot failed ({mode}): {result.stderr.strip() or result.stdout.strip()}")

    # Certbot skips deploy hooks when it retains an unexpired certificate.
    # Repair mail-service deployment as well as fresh issuance/renewal.
    canonical_mail = settings.mail_hostname or settings.webmail_hostname or settings.panel_hostname
    if domain == canonical_mail:
        from scripts.ssl_deploy_hook import _deploy_mail_service_certificate
        with audit.step("SSL: deploy mail service certificate"):
            _deploy_mail_service_certificate(domain)

    response = {"domain": domain, "challenge": mode, "status": "issued", "names": names}
    if skipped_names:
        response["skipped_names"] = skipped_names
    if _service_hostname(domain):
        with audit.step("SSL: verify served certificate"):
            response["diagnostics"] = service_certificate_diagnostics({"domain": domain})
        if not response["diagnostics"]["sni"]["ok"]:
            raise SslError("Certificate was issued but the service did not present it over HTTPS; review diagnostics")
    return response


def issue_wildcard_certificate(params: dict) -> dict:
    """Phase 7a feature 5: `*.<domain>` + `<domain>` in one cert, via
    DNS-01 + the PowerDNS API hook -- the same certbot-dns-powerdns plugin
    ARCHITECTURE.md SS8 already wires for the auto-detected DNS-01 path,
    just unconditional here (a wildcard SAN has no HTTP-01 option at all,
    so this never falls back to webroot the way issue_certificate does).
    Requires the domain's own zone to be Boron-managed (a real
    PowerDNS zone this server can create/delete the `_acme-challenge` TXT
    record in) -- an externally-DNS-managed domain has no hook for
    Boron to create that record in, so wildcard issuance for it is
    rejected with a clear reason rather than silently trying and failing
    deep inside certbot."""
    raw_domain = params["domain"]
    if isinstance(raw_domain, str) and raw_domain.strip().startswith("*."):
        raise SslError("pass the base domain (e.g. 'example.com'), not '*.example.com' -- the wildcard SAN is added automatically")
    domain = validate_domain(raw_domain)
    if not settings.letsencrypt_email:
        raise SslError("letsencrypt_email is not set in boron.toml")

    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain))
    if domain_row is None:
        raise SslError(f"domain '{domain}' is not provisioned in Boron")
    # Phase 2+3 feature 5: wildcards work on either DNS-01 provider now
    # (dns-cloudflare for a CF-active zone, dns-powerdns for a local managed
    # zone). Still requires a Boron-managed zone -- an externally-DNS
    # domain has no hook to create the _acme-challenge TXT in.
    dns01 = _dns01_plan(domain)
    if dns01 is None:
        raise SslError(
            f"wildcard SSL for '{domain}' requires its DNS zone to be managed by Boron "
            "(local PowerDNS or a Cloudflare-active zone) -- DNS-01 is the only ACME challenge type that "
            "supports wildcard names, and it needs the _acme-challenge TXT to be creatable through the "
            "zone's own DNS API. Configure public nameserver delegation to the local DNS server, or activate the authoritative Cloudflare zone in Boron first."
        )
    audit.change(
        "certificate_status",
        "present" if Path(letsencrypt_cert_paths(domain)[1]).exists() else "missing",
        "issued_wildcard",
    )

    force_args = ["--force-renewal"] if params.get("force") else []
    args = [
        settings.certbot_bin, "certonly",
        "--non-interactive",
        "--agree-tos",
        "--email", settings.letsencrypt_email,
        "--cert-name", domain,
        "-d", domain,
        "-d", f"*.{domain}",
        "--deploy-hook", f"{_VENV_PYTHON} {DEPLOY_HOOK_SCRIPT}",
        *force_args,
        *dns01,
    ]
    with audit.step("SSL: request wildcard certificate from ACME") as action:
        result = action.capture(run(args, timeout=180))
    if not result.ok:
        raise SslError(f"certbot failed (wildcard dns-01): {result.stderr.strip() or result.stdout.strip()}")

    return {"domain": domain, "wildcard_domain": f"*.{domain}", "challenge": "dns-01", "status": "issued"}


def certificate_status(params: dict) -> dict:
    domain = validate_domain(params["domain"])
    if _service_hostname(domain):
        if domain == settings.panel_hostname:
            exists = Path("/etc/boron/ssl/api/panel.key").exists() and Path("/etc/boron/ssl/api/panel.crt").exists()
        else:
            key, cert = letsencrypt_cert_paths(domain)
            exists = Path(key).exists() and Path(cert).exists()
        return {"domain": domain, "ssl_status": "active" if exists else "none"}
    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain))
        if domain_row is None:
            raise SslError(f"domain '{domain}' is not provisioned in Boron")
        return {"domain": domain, "ssl_status": domain_row.ssl_status}


def terminate_account_certs(account) -> None:
    """TERMINATE_HOOKS entry: revoke and delete certbot's local copy of any
    certificate issued for this account's domains. Without this, terminated
    accounts leave orphaned cert/key material under /etc/letsencrypt and
    certbot's renewal timer keeps trying (and failing, since the vhost/
    webroot is gone) to renew a cert nobody needs anymore -- exactly the
    "no orphaned configs/processes" requirement on termination. Idempotent:
    certbot delete on a cert-name that doesn't exist is a safe no-op
    (checked via `certbot certificates` first rather than assumed)."""
    with write_session() as session:
        domains = session.scalars(
            select(Domain.domain).where(Domain.account_id == account.id, Domain.ssl_status == "active")
        ).all()
    for domain_name in domains:
        if not Path(f"/etc/letsencrypt/live/{domain_name}").exists():
            continue
        run([settings.certbot_bin, "delete", "--cert-name", domain_name, "--non-interactive"], timeout=30)


def delete_certificate(domain_name: str) -> None:
    """Name-keyed cleanup (handlers_domain.purge_domain_settings): delete the
    certificate lineage and expiry notices of a name no site uses any more.
    Otherwise certbot keeps renewing it, and a later owner of the name sees
    the previous owner's certificate in their SSL dashboard."""
    domain_name = validate_domain(domain_name)
    if _service_hostname(domain_name):
        return  # never a site's lineage; Server Setup manages these
    with write_session() as session:
        session.execute(delete(SslExpiryNotice).where(SslExpiryNotice.domain == domain_name))
    if not (Path(f"/etc/letsencrypt/live/{domain_name}").exists()
            or (RENEWAL_CONF_DIR / f"{domain_name}.conf").exists()):
        return
    result = run([settings.certbot_bin, "delete", "--cert-name", domain_name, "--non-interactive"], timeout=60)
    if not result.ok:
        raise SslError(f"could not delete the certificate of '{domain_name}'")


def https_ready(domain: str, host: str | None = None) -> bool:
    """Whether https://<host> is served with a valid certificate by the
    domain's vhost, which uses its own lineage once ssl_status is active
    (daemon/ols.py _ssl_paths_for_domain). host defaults to the domain."""
    domain = validate_domain(domain)
    host = validate_domain(host or domain)
    with write_session() as session:
        row = session.scalar(select(Domain).where(Domain.domain == domain))
        if row is None or row.ssl_status != "active":
            return False
    details = _cert_file_details(domain)
    if details is None or details["cert_status"] not in ("valid", "expiring"):
        return False
    return host in _certificate_san(domain)


def prepare_https(domain: str, host: str | None = None) -> dict:
    """Make https://<host> usable before a site is given that address
    (WordPress clone, staging), so the site never redirects visitors to a
    name without a certificate. Uses the existing certificate when it covers
    host, otherwise issues one when host resolves to this server.

    Returns {"https": bool, "certificate": "existing" | "issued" | None,
    "detail": why https cannot be used, or None}.
    """
    domain = validate_domain(domain)
    host = validate_domain(host or domain)
    if https_ready(domain, host):
        return {"https": True, "certificate": "existing", "detail": None}
    here = _points_here(host, _server_addresses())
    if here is not True:
        detail = (f"{host} does not point to this server yet" if here is False
                  else f"public DNS for {host} could not be checked")
        return {"https": False, "certificate": None, "detail": detail}
    try:
        issue_certificate({"domain": domain})
    except Exception:  # noqa: BLE001 - the site still works over http; the reason is in the log
        logger.exception("could not issue a certificate for %s", domain)
        return {"https": False, "certificate": None, "detail": f"a certificate for {host} could not be issued"}
    if https_ready(domain, host):
        return {"https": True, "certificate": "issued", "detail": None}
    return {"https": False, "certificate": None, "detail": f"the certificate does not cover {host}"}


# --- SSL dashboard (Phase 3 feature 8) ---------------------------------

EXPIRING_SOON_DAYS = 30
RENEWAL_CONF_DIR = Path("/etc/letsencrypt/renewal")


def _cert_file_details(domain: str) -> dict | None:
    """Real, on-disk certificate inspection via the `cryptography` library
    (not `openssl x509` text-parsing, which this project avoids shelling
    out for anything a proper library can parse instead) -- returns None
    if no certificate file exists for this domain at all ("missing" per
    the goal's own status vocabulary), otherwise the actual expiry date
    and issuer straight from the X.509 certificate itself, not from
    Boron's own DB (Domain.ssl_status only records "did Boron's
    own issue flow succeed", not the certificate's real, independently-
    verifiable expiry -- the dashboard's whole point is showing the
    latter)."""
    if domain == settings.panel_hostname:
        cert_path = "/etc/boron/ssl/api/panel.crt"
    else:
        _key_path, cert_path = letsencrypt_cert_paths(domain)
    if not Path(cert_path).exists():
        return None
    try:
        cert_bytes = Path(cert_path).read_bytes()
        cert = x509.load_pem_x509_certificate(cert_bytes)
    except (OSError, ValueError):
        return None

    not_after = cert.not_valid_after_utc
    days_remaining = (not_after - dt.datetime.now(dt.timezone.utc)).days
    issuer = cert.issuer.rfc4514_string()

    if days_remaining < 0:
        status = "expired"
    elif days_remaining <= EXPIRING_SOON_DAYS:
        status = "expiring"
    else:
        status = "valid"

    return {
        "cert_status": status,
        "expiry_date": not_after.date().isoformat(),
        "days_remaining": days_remaining,
        "issuer": issuer,
    }


def _auto_renew_enabled(domain: str) -> bool:
    """certbot's own renewal timer (`certbot.timer`, stock package unit --
    Boron does not reimplement a renewal scheduler, ARCHITECTURE.md
    SS8) handles every certificate with a renewal config under
    /etc/letsencrypt/renewal/ automatically; this just reports whether
    that's true for this specific domain, not whether the timer itself
    is enabled (a per-domain "will this actually renew" answer -- the
    timer being globally active is a server-wide fact checked once at
    dashboard-render time by the caller, not per domain)."""
    return (RENEWAL_CONF_DIR / f"{domain}.conf").exists()


def get_ssl_dashboard(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise SslError(f"account '{username}' not found")
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
        domain_data = [(d.domain, d.ssl_status, d.ssl_is_wildcard) for d in domains]

    timer_active = run(["systemctl", "is-active", "certbot.timer"], timeout=10).stdout.strip() == "active"

    entries = []
    for domain_name, ssl_status, ssl_is_wildcard in domain_data:
        details = _cert_file_details(domain_name)
        entries.append(
            {
                "domain": domain_name,
                "ssl_status": ssl_status,
                "is_wildcard": ssl_is_wildcard,
                "cert_status": details["cert_status"] if details else "missing",
                "expiry_date": details["expiry_date"] if details else None,
                "days_remaining": details["days_remaining"] if details else None,
                "issuer": details["issuer"] if details else None,
                "auto_renew": bool(details) and timer_active and _auto_renew_enabled(domain_name),
            }
        )

    return {"username": username, "certbot_timer_active": timer_active, "domains": entries}


def get_admin_ssl_dashboard(params: dict) -> dict:
    """Fleet certificate inventory for the administrator SSL workspace."""
    with write_session() as session:
        rows = session.execute(
            select(Domain.domain, Domain.ssl_status, Domain.ssl_is_wildcard, Account.username, Account.status)
            .join(Account, Account.id == Domain.account_id)
            .where(Account.status != "terminated")
            .order_by(Account.username, Domain.domain)
        ).all()
    domain_data = [
        {"domain": domain, "ssl_status": ssl_status, "is_wildcard": wildcard,
         "username": username, "account_status": account_status, "system": False}
        for domain, ssl_status, wildcard, username, account_status in rows
    ]
    hosted = {item["domain"] for item in domain_data}
    seen_services: set[str] = set()
    for hostname, label, service in (
        (settings.panel_hostname, "Panel", "panel"),
        (settings.webmail_hostname, "Webmail", "webmail"),
        (settings.mail_hostname, "Mail", "mail"),
        (settings.pma_hostname, "phpMyAdmin", "phpmyadmin"),
    ):
        if hostname and hostname not in hosted and hostname not in seen_services:
            seen_services.add(hostname)
            status = certificate_status({"domain": hostname})["ssl_status"]
            domain_data.append({
                "domain": hostname, "ssl_status": status, "is_wildcard": False,
                "username": label, "account_status": "system", "system": True,
                "service": service,
            })
    timer_active = run(["systemctl", "is-active", "certbot.timer"], timeout=10).stdout.strip() == "active"
    entries = []
    for item in domain_data:
        details = _cert_file_details(item["domain"])
        entries.append({
            **item,
            "cert_status": details["cert_status"] if details else "missing",
            "expiry_date": details["expiry_date"] if details else None,
            "days_remaining": details["days_remaining"] if details else None,
            "issuer": details["issuer"] if details else None,
            "auto_renew": bool(details) and timer_active and _auto_renew_enabled(item["domain"]),
            "deployment_status": "active" if item.get("system") and details else ("pending" if item.get("system") else None),
        })
    return {"certbot_timer_active": timer_active, "domains": entries}


# --- SSL expiry notifications (Phase 7b feature 3) --------------------------

SSL_EXPIRY_WARNING_DAYS = 14


def check_expiring_certificates() -> int:
    """Entry point for scripts/ssl_expiry_check.py (system cron, daily).
    Scans every domain with an active cert, and for one within
    SSL_EXPIRY_WARNING_DAYS of expiry, fires "ssl.expiring" through
    daemon/events.py exactly once per (domain, expiry-date) pair --
    SslExpiryNotice is the dedup marker, keyed on the cert's own not-after
    date so a *renewed* cert (new expiry date) is correctly treated as a
    fresh notice-worthy event rather than never notifying again. Returns
    the number of notices actually sent."""
    with write_session() as session:
        domains = session.scalars(select(Domain).where(Domain.ssl_status == "active")).all()
        domain_data = [(d.domain, d.account_id) for d in domains]

    sent = 0
    for domain_name, account_id in domain_data:
        details = _cert_file_details(domain_name)
        # Deliberately NOT details["cert_status"] == "expiring" -- that
        # flag uses the SSL *dashboard's* own EXPIRING_SOON_DAYS (30, a
        # different, UI-only concern), while this feature's own goal text
        # specifies a 14-day warning window. A cert already "expired" is
        # also still notice-worthy (renewal likely failed silently).
        if details is None or details["days_remaining"] > SSL_EXPIRY_WARNING_DAYS:
            continue
        expiry_date = details["expiry_date"]
        with write_session() as session:
            already_notified = session.scalar(
                select(SslExpiryNotice).where(SslExpiryNotice.domain == domain_name, SslExpiryNotice.expiry_date == expiry_date)
            )
            if already_notified is not None:
                continue
            account = session.get(Account, account_id)
            if account is None:
                continue
            session.add(SslExpiryNotice(domain=domain_name, expiry_date=expiry_date))
            account_snapshot = account

        events.emit(
            "ssl.expiring", account_snapshot,
            domain=domain_name, days_remaining=details["days_remaining"], expiry_date=expiry_date,
        )
        sent += 1
    return sent
