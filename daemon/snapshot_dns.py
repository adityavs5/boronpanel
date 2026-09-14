"""Account-bound DNS provider documents for encrypted configuration recovery."""
from daemon import dns_operations
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


@dns_operations.serialized_worker
def capture(account, selected_zones=None):
    """Retain native records and reject ownership/provider changes during reads.

    This captures data only; it cannot change providers, delegation or records.
    The caller holds the account backup lock. Cloudflare credentials are used
    only as transport context and are never included in the returned payload.
    """
    bindings = _bindings(account)
    if selected_zones is not None:
        if (not isinstance(selected_zones, list) or any(not isinstance(name, str) for name in selected_zones)
                or len(set(selected_zones)) != len(selected_zones)
                or not set(selected_zones) <= {binding['zone'] for binding in bindings}):
            raise ValidationError('Invalid DNS capture zone selection')
        bindings = [binding for binding in bindings if binding['zone'] in selected_zones]
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
    after = _bindings(account)
    if selected_zones is not None:
        after = [binding for binding in after if binding['zone'] in selected_zones]
    if after != bindings:
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


def validate_for_restore(account, payload, selected_zones=None):
    """Validate selected local zones; never use saved provider IDs as authority.

    Server-managed SOA, apex nameservers and DNSSEC records are excluded from
    customer recovery. Cloudflare application requires its own native validator.
    """
    import dns.name
    import dns.rdata
    import dns.rdatatype
    if (not isinstance(payload, dict) or type(payload.get('format')) is not int or payload['format'] != 1
            or type(payload.get('account_id')) is not int or payload['account_id'] != account.id
            or payload.get('username') != account.username or not isinstance(payload.get('zones'), list)):
        raise ValidationError('DNS recovery metadata belongs to another account or format')
    saved = {}
    for zone in payload['zones']:
        if not isinstance(zone, dict) or not isinstance(zone.get('zone'), str) or zone['zone'] in saved:
            raise ValidationError('Invalid or duplicate DNS recovery zone')
        saved[zone['zone']] = zone
    if selected_zones is None:
        selected_zones = list(saved)
    if (not isinstance(selected_zones, list) or any(not isinstance(name, str) for name in selected_zones)
            or len(set(selected_zones)) != len(selected_zones) or any(name not in saved for name in selected_zones)):
        raise ValidationError('Invalid DNS recovery zone selection')
    current = {binding['zone']: binding for binding in _bindings(account)}
    zones = []
    protected = {'SOA', 'DNSKEY', 'CDNSKEY', 'CDS', 'RRSIG', 'NSEC', 'NSEC3', 'NSEC3PARAM'}
    for name in selected_zones:
        zone = saved[name]
        binding = zone.get('binding')
        actual = current.get(name)
        if (actual is None or not isinstance(binding, dict) or binding != actual
                or any(type(binding[key]) is not type(actual[key]) for key in actual)):
            raise ValidationError('A selected DNS zone changed ownership or provider registration')
        provider = 'cloudflare' if actual['cloudflare_status'] == 'active' else 'local'
        if zone.get('provider') != provider:
            raise ValidationError('DNS recovery provider does not match the current zone')
        if provider != 'local':
            raise ValidationError('Cloudflare DNS recovery is not available yet')
        raw = zone.get('records')
        if not isinstance(raw, list) or len(raw) > 100000:
            raise ValidationError('Invalid DNS recovery record collection')
        origin = dns.name.from_text(name + '.')
        records = []
        seen = set()
        for rrset in raw:
            if (not isinstance(rrset, dict) or not isinstance(rrset.get('name'), str)
                    or not isinstance(rrset.get('type'), str)):
                raise ValidationError('Invalid saved DNS record set')
            try:
                owner = dns.name.from_text(rrset['name'], origin=dns.name.root)
                if not owner.is_subdomain(origin):
                    raise ValueError('outside zone')
                rtype = dns.rdatatype.from_text(rrset['type'])
                type_name = dns.rdatatype.to_text(rtype)
            except Exception:
                raise ValidationError('Invalid DNS record name or type in recovery point') from None
            key = (owner.canonicalize().to_text(), type_name)
            if key in seen:
                raise ValidationError('Duplicate DNS record set in recovery point')
            seen.add(key)
            if type_name in protected or (type_name == 'NS' and owner == origin):
                continue
            if type_name == 'CNAME' and owner == origin:
                raise ValidationError('A zone apex cannot be restored as a CNAME')
            if rtype in (0, 41, 249, 250, 251, 252, 253, 254, 255):
                raise ValidationError('Unsupported DNS meta-record type')
            ttl = rrset.get('ttl')
            values = rrset.get('records')
            if type(ttl) is not int or not 0 <= ttl <= 2147483647 or not isinstance(values, list) or not values:
                raise ValidationError('Invalid saved DNS TTL or values')
            normalized = []
            for value in values:
                if (not isinstance(value, dict) or not isinstance(value.get('content'), str)
                        or type(value.get('disabled')) is not bool or len(value['content']) > 65535
                        or '\x00' in value['content']):
                    raise ValidationError('Invalid saved DNS record content or disabled flag')
                try:
                    parsed = dns.rdata.from_text('IN', rtype, value['content'], origin=origin, relativize=False)
                except Exception:
                    raise ValidationError('Saved DNS record content is not valid for its type') from None
                normalized.append(dict(content=parsed.to_text(origin=origin, relativize=False), disabled=value['disabled']))
            comments = rrset.get('comments', [])
            if not isinstance(comments, list):
                raise ValidationError('Invalid saved DNS comments')
            normalized_comments = []
            for comment in comments:
                if (not isinstance(comment, dict) or not isinstance(comment.get('content'), str)
                        or not isinstance(comment.get('account'), str)
                        or type(comment.get('modified_at')) is not int or comment['modified_at'] < 0):
                    raise ValidationError('Invalid saved DNS comment')
                normalized_comments.append({field: comment[field] for field in ('content', 'account', 'modified_at')})
            records.append(dict(name=owner.to_text(), type=type_name, ttl=ttl, records=normalized, comments=normalized_comments))
        cnames = {row['name'].lower() for row in records if row['type'] == 'CNAME'}
        if any(row['name'].lower() in cnames and (row['type'] != 'CNAME' or len(row['records']) != 1) for row in records):
            raise ValidationError('Saved CNAME conflicts with other DNS records')
        zones.append(dict(zone=name, provider=provider, binding=deepcopy(actual), records=records))
    return dict(format=1, account_id=account.id, username=account.username, zones=zones)


def _record_state(records):
    """Compare DNS meaning, including disabled records, TTLs and comments."""
    import dns.name
    import dns.rdata
    result = {}
    for row in records:
        values = sorted((dns.rdata.from_text('IN', row['type'], value['content'],
                         relativize=False).to_digestable().hex(), value['disabled']) for value in row['records'])
        comments = sorted((comment['content'], comment['account'], comment['modified_at']) for comment in row['comments'])
        result[(dns.name.from_text(row['name']).canonicalize().to_text(), row['type'])] = (row['ttl'], values, comments)
    return result


@dns_operations.serialized_worker
def apply_configuration(account, payload, save_previous, on_zone=None):
    """Apply local DNS after durable encrypted capture; coordinator holds locks.

    No provider switch or authority-record replacement is performed. Failures
    retain the complete previous copy for explicit undo, including partial
    multi-zone restores. Caller must coordinate concurrent DNS provider edits.
    """
    selected = validate_for_restore(account, payload)
    names = [zone['zone'] for zone in selected['zones']]
    if not names:
        raise ValidationError('Select at least one DNS zone to restore')
    previous = validate_for_restore(account, capture(account, names))
    save_previous(previous)
    before = {zone['zone']: zone for zone in previous['zones']}
    completed = []
    for zone in selected['zones']:
        name = zone['zone']
        # Revalidate ownership/provider immediately before each write and verify
        # no edits occurred while encrypting the previous configuration.
        validate_for_restore(account, selected, [name])
        current = validate_for_restore(account, capture(account, [name]))['zones'][0]
        if _record_state(current['records']) != _record_state(before[name]['records']):
            raise ValidationError('DNS records changed while preparing recovery. The previous copy is retained.')
        wanted = {(row['name'].lower(), row['type']) for row in zone['records']}
        changes = [dict(name=row['name'], type=row['type'], changetype='DELETE')
                   for row in current['records'] if (row['name'].lower(), row['type']) not in wanted]
        changes.extend(dict(**row, changetype='REPLACE') for row in zone['records'])
        try:
            powerdns.apply_rrset_changes(name, changes)
            actual = validate_for_restore(account, capture(account, [name]))['zones'][0]
            if _record_state(actual['records']) != _record_state(zone['records']):
                raise ValidationError('Verification mismatch')
        except Exception:
            raise ValidationError('DNS recovery could not be confirmed. Some records may have changed; '
                                  'the encrypted previous configuration is retained for undo.') from None
        completed.append(name)
        if on_zone:
            on_zone(list(completed))
    return completed
