"""Account-bound DNS provider documents for encrypted configuration recovery."""
from copy import deepcopy
from sqlalchemy import select, text

from shared.db import write_session
from shared.models import Account, DnsZone, CloudflareZone
from shared.validation import ValidationError
from daemon import cloudflare, cloudflare_accounts, powerdns


def _bindings(account):
    with write_session() as session:
        connection = session.connection()
        if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
            session.execute(text('BEGIN'))
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer available for DNS backup')
        zones = session.scalars(select(DnsZone).where(DnsZone.account_id == account.id).order_by(DnsZone.zone)).all()
        result = []
        for zone in zones:
            cf = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == zone.zone))
            if cf is not None and cf.account_id != account.id:
                raise ValidationError('DNS provider registration belongs to another account')
            result.append(dict(zone_id=zone.id, zone=zone.zone,
                cloudflare_id=cf.id if cf else None,
                cloudflare_status=cf.status if cf else None,
                cloudflare_zone_id=cf.cf_zone_id if cf else None,
                cloudflare_account_id=cf.cf_account_id if cf else None))
        return result


def capture(account):
    """Retain native records and reject ownership/provider changes during reads.

    This captures data only; it cannot change providers, delegation or records.
    The caller holds the account backup lock. Cloudflare credentials are used
    only as transport context and are never included in the returned payload.
    """
    bindings = _bindings(account)
    zones = []
    for binding in bindings:
        name = binding['zone']
        if binding['cloudflare_status'] == 'active':
            with cloudflare.use_token(cloudflare_accounts.token_for_id(binding['cloudflare_account_id'])):
                records = cloudflare.export_record_documents(name, zone_id=binding['cloudflare_zone_id'])
            if not isinstance(records, list):
                raise ValidationError('Invalid Cloudflare DNS backup response')
            provider = 'cloudflare'
        else:
            data = powerdns.get_zone(name)
            if (not isinstance(data, dict) or data.get('name', '').rstrip('.') != name
                    or not isinstance(data.get('rrsets'), list)):
                raise ValidationError('Invalid PowerDNS zone backup response')
            records = data['rrsets']
            provider = 'local'
        zones.append(dict(zone=name, provider=provider, binding=binding, records=deepcopy(records)))
    if _bindings(account) != bindings:
        raise ValidationError('DNS ownership or provider changed during backup; try again')
    return dict(format=1, account_id=account.id, username=account.username, zones=zones)


def legacy_zones(configuration):
    """Derive the older manifest view from the same capture, without re-reading."""
    result = []
    for zone in configuration['zones']:
        records = []
        if zone['provider'] == 'local':
            for rrset in zone['records']:
                if rrset['type'] == 'SOA':
                    continue
                records.append(dict(name=rrset['name'].rstrip('.'), type=rrset['type'],
                                    ttl=rrset['ttl'], values=[row['content'] for row in rrset['records']]))
        else:
            grouped = {}
            for record in zone['records']:
                if record.get('type') == 'SOA':
                    continue
                key = (record.get('name', '').rstrip('.'), record.get('type', ''))
                ttl = record.get('ttl', cloudflare.DEFAULT_TTL)
                entry = grouped.setdefault(key, dict(name=key[0], type=key[1],
                    ttl=cloudflare.DEFAULT_TTL if ttl == cloudflare.AUTO_TTL else ttl, values=[], proxied=False))
                entry['values'].append(cloudflare._from_cf_record(record))
                entry['proxied'] = entry['proxied'] or bool(record.get('proxied'))
            records = list(grouped.values())
        result.append(dict(zone=zone['zone'], records=records))
    return result
