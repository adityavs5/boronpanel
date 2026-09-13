"""Shared "which Boron-managed zone (if any) covers this domain name"
lookup -- used by handlers_domain.py (subdomain auto-A-record, Phase 2
feature 4) and handlers_mail.py (SPF/DKIM/DMARC auto-publish, Phase 3
feature 1). A domain doesn't get its own zone just for being a subdomain
or a mail domain -- it's a name *within* whatever zone already covers it,
same reasoning both callers already relied on independently before this
was factored out into one place.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import DnsZone


def find_managed_zone(domain_name: str) -> str | None:
    with write_session() as session:
        zones = session.scalars(select(DnsZone.zone)).all()
    return next((z for z in sorted(zones,key=len,reverse=True) if domain_name == z or domain_name.endswith(f".{z}")), None)


def label_within_zone(domain_name: str, zone: str) -> str:
    if domain_name == zone:
        return "@"
    return domain_name[: -(len(zone) + 1)]
