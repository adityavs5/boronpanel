"""Keep retained mail/DNS resources from changing tenants through a web name."""
from sqlalchemy import select

from shared.config import settings
from shared.models import DnsZone, MailDomain
from shared.validation import ValidationError, validate_domain


def require_available(session, domain: str, account_id: int | None) -> None:
    domain = validate_domain(domain)
    if domain in (settings.panel_hostname, settings.webmail_hostname, settings.pma_hostname):
        raise ValidationError("This hostname is reserved for a panel service")
    mail = session.scalar(select(MailDomain).where(MailDomain.domain == domain))
    if mail is not None and (account_id is None or mail.account_id != account_id):
        raise ValidationError("This domain has mail resources owned by another account")
    zones = [zone for zone in session.scalars(select(DnsZone)).all()
             if domain == zone.zone or domain.endswith('.' + zone.zone)]
    if zones:
        parent = max(zones, key=lambda zone: len(zone.zone))
        if account_id is None or parent.account_id != account_id:
            raise ValidationError("The matching DNS zone belongs to another account")
