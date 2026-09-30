"""Validated advanced DNS edits with stale-state checks and provider-native writes."""
from copy import deepcopy
import hashlib
import json
import secrets

import dns.exception
import dns.name
import dns.rdataclass
import dns.rdatatype
import dns.tokenizer
import dns.zone
import dns.zonefile
from dns.rdtypes.ANY.TXT import TXT
from sqlalchemy import select

from daemon import dns_operations, dnsprovider, handlers_dns, mail_dns
from shared.db import write_session
from shared.models import Account, DnsZone, Domain
from shared.validation import ValidationError, validate_domain

EDITABLE = frozenset(handlers_dns.RECORD_VALUE_VALIDATORS)


def _binding(zone):
    with write_session() as session:
        row = session.scalar(select(DnsZone).where(DnsZone.zone == zone))
        domain = session.scalar(select(Domain).where(Domain.domain == zone))
        account = session.get(Account, row.account_id) if row else None
        if not row or not domain or domain.account_id != row.account_id or not account or account.status not in ('active', 'suspended'):
            raise ValidationError('Select a DNS zone owned by an existing hosting account')
        cf = dnsprovider.cloudflare_zone_row(zone)
        if cf and cf.account_id != account.id:
            raise ValidationError('The DNS provider belongs to another account')
        return {'account_id': account.id, 'provider': dnsprovider.provider_for_zone(zone),
                'cf_zone_id': cf.cf_zone_id if cf and cf.status == 'active' else None,
                'cf_account_id': getattr(cf, 'cf_account_id', None) if cf else None}


def _txt_bytes(value):
    tokenizer = dns.tokenizer.Tokenizer(value)
    chunks = []
    while True:
        token = tokenizer.get()
        if token.is_eof():
            break
        if not token.is_quoted_string():
            raise ValidationError('TXT values must contain quoted character strings')
        chunks.append(token.unescape_to_bytes().value)
    return b''.join(chunks)


def _canonical_values(rtype, values):
    return sorted(_txt_bytes(v).hex() if rtype == 'TXT' else v for v in values)


def _records(zone, binding, native=None):
    if binding['provider'] == 'cloudflare':
        from daemon import cloudflare, cloudflare_accounts
        if native is None:
            with cloudflare.use_token(cloudflare_accounts.token_for_id(binding['cf_account_id'])):
                native = cloudflare.export_record_documents(zone, zone_id=binding['cf_zone_id'])
        raw = native
        grouped = {}
        for native in raw:
            key = (native['name'].rstrip('.').lower(), native['type'])
            meta = native.get('meta') or {}
            protected = bool(native.get('locked') or any(meta.get(k) for k in ('auto_added', 'managed_by_apps', 'managed_by_argo_tunnel')))
            row = grouped.setdefault(key, {'name': key[0], 'type': key[1], 'ttl': native.get('ttl', 3600) if native.get('ttl') != 1 else 3600,
                                          'values': [], 'proxied': False, 'editor_protected': False})
            if native['type'] in EDITABLE:
                row['values'].append(cloudflare._from_cf_record(native))
            else:
                row['editor_protected'] = True
            row['editor_protected'] |= protected
            row['proxied'] |= bool(native.get('proxied'))
        return list(grouped.values())
    from daemon import powerdns
    return [{'name': row['name'].rstrip('.').lower(), 'type': row['type'], 'ttl': row.get('ttl', 3600),
             'values': [r['content'] for r in row.get('records', [])],
             'editor_protected': bool(row.get('comments') or any(r.get('disabled') for r in row.get('records', [])))}
            for row in (native if native is not None else powerdns.get_zone(zone)).get('rrsets', [])]


def _fingerprint(binding, records):
    canonical = sorted(json.dumps({**row, 'values': _canonical_values(row['type'], row['values']) if row['type'] in EDITABLE else row['values']}, sort_keys=True) for row in records)
    return hashlib.sha256(json.dumps([binding, canonical], sort_keys=True).encode()).hexdigest()


def _protected(row, zone):
    return row.get('editor_protected') or row['type'] not in EDITABLE or (row['type'] == 'NS' and row['name'].rstrip('.').lower() == zone)


class _StrictReader(dns.zonefile.Reader):
    def _rr_line(self):
        # dnspython intentionally ignores outside-zone owners. An editor must
        # reject them, otherwise a typo could silently delete the intended RRset.
        token = self.tok.get(want_leading=True)
        if not token.is_whitespace():
            name = self.tok.as_name(token, self.current_origin)
            if not name.is_subdomain(self.zone_origin):
                raise ValidationError('A record owner is outside the selected DNS zone')
        self.tok.unget(token)
        super()._rr_line()


def _parse(zone, text):
    if not isinstance(text, str) or len(text.encode()) > 256 * 1024:
        raise ValidationError('Zone text must be at most 256 KB')
    parsed = dns.zone.Zone(zone + '.', relativize=False)
    try:
        with parsed.writer() as transaction:
            _StrictReader(dns.tokenizer.Tokenizer(text), dns.rdataclass.IN, transaction,
                          allow_include=False, allow_directives={'$ORIGIN', '$TTL'}, default_ttl=3600).read()
    except ValidationError:
        raise
    except (dns.exception.DNSException, ValueError) as exc:
        raise ValidationError('Invalid DNS zone syntax: ' + str(exc)[:240]) from None
    records = []
    count = 0
    for name, node in parsed.nodes.items():
        owner = name.to_text().lower().rstrip('.')
        label = handlers_dns._record_label(zone, owner + '.')
        for rrset in node.rdatasets:
            rtype = dns.rdatatype.to_text(rrset.rdtype)
            if rtype not in EDITABLE or (rtype == 'NS' and owner == zone):
                raise ValidationError('SOA, apex NS, DNSSEC and unsupported records are preserved automatically; remove them from editable text')
            if not 60 <= rrset.ttl <= 2147483647:
                raise ValidationError('DNS TTL must be between 60 and 2147483647 seconds')
            values = [handlers_dns.RECORD_VALUE_VALIDATORS[rtype](r.to_text()) for r in rrset]
            count += len(values)
            if count > 2000:
                raise ValidationError('At most 2000 editable DNS record values are supported')
            if rtype == 'CNAME' and (label == '@' or len(values) != 1):
                raise ValidationError('A CNAME needs one target and cannot replace the zone apex')
            records.append({'name': owner, 'type': rtype, 'ttl': rrset.ttl, 'values': sorted(set(values))})
    return records


def _text(zone, records):
    lines = [f'$ORIGIN {zone}.', '$TTL 3600', '; SOA, apex NS, DNSSEC and provider-managed records are preserved.']
    for row in sorted(records, key=lambda r: (r['name'], r['type'])):
        if _protected(row, zone):
            continue
        for value in row['values']:
            if row['type'] == 'TXT':
                raw = _txt_bytes(value)
                chunks = [raw[i:i+255] for i in range(0, len(raw), 255)] or [b'']
                value = TXT(dns.rdataclass.IN, dns.rdatatype.TXT, chunks).to_text()
            lines.append(f"{row['name'].rstrip('.')}. {row['ttl']} IN {row['type']} {value}")
    return '\n'.join(lines) + '\n'


@dns_operations.serialized
def get(params):
    zone = validate_domain(params['domain'])
    binding = _binding(zone)
    records = _records(zone, binding)
    return {'zone': zone, 'text': _text(zone, records), 'fingerprint': _fingerprint(binding, records),
            'templates': [{'id': key, 'name': label} for key, label in
                          [('web', 'Website records'), ('mail', 'Email and FTP records'), ('hosting', 'Website, email and FTP records')]]}


def _plan(zone, text, binding, before):
    desired = _parse(zone, text)
    # Validate CNAME boundaries against records that cannot be removed here.
    preserved = [r for r in before if _protected(r, zone)]
    if any(r['name'] == d['name'] and r['type'] == d['type'] for r in preserved for d in desired):
        raise ValidationError('This edit overlaps a preserved or provider-managed record')
    combined = desired + preserved
    for cname in (r for r in combined if r['type'] == 'CNAME'):
        if any(r['name'] == cname['name'] and r['type'] != 'CNAME' for r in combined):
            raise ValidationError('A CNAME conflicts with a preserved record at the same name')
    old = {(r['name'].rstrip('.').lower(), r['type']): r for r in before if not _protected(r, zone)}
    new = {(r['name'], r['type']): r for r in desired}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        prior, after = old.get(key), new.get(key)
        if prior and after and prior['ttl'] == after['ttl'] and _canonical_values(prior['type'], prior['values']) == _canonical_values(after['type'], after['values']):
            continue
        changes.append({'name': key[0], 'type': key[1], 'action': 'remove' if after is None else 'add' if prior is None else 'replace',
                        'before': prior['values'] if prior else [], 'after': after['values'] if after else [],
                        'ttl': after['ttl'] if after else None})
    return desired, changes


@dns_operations.serialized
def preview(params):
    zone = validate_domain(params['domain'])
    binding = _binding(zone)
    records = _records(zone, binding)
    if params.get('fingerprint') != _fingerprint(binding, records):
        raise ValidationError('DNS records changed. Reload the zone before reviewing this edit.')
    _, changes = _plan(zone, params['text'], binding, records)
    return {'zone': zone, 'changes': changes, 'fingerprint': params['fingerprint']}


@dns_operations.serialized
def template(params):
    zone = validate_domain(params['domain'])
    binding = _binding(zone)
    records = _records(zone, binding)
    choice = params.get('template')
    if choice not in ('web', 'mail', 'hosting'):
        raise ValidationError('Choose a supported DNS template')
    desired = []
    if choice in ('web', 'hosting'):
        from daemon.ipmanager import address_for_account
        address = address_for_account(binding['account_id'])
        if address:
            desired.extend([{'name': zone, 'type': 'AAAA' if ':' in address else 'A', 'ttl': 3600, 'values': [address]},
                            {'name': 'www.' + zone, 'type': 'CNAME', 'ttl': 3600, 'values': [zone + '.']}])
    if choice in ('mail', 'hosting'):
        for record in mail_dns.default_records(zone):
            desired.append({'name': zone if record.label == '@' else record.label + '.' + zone,
                            'type': record.rtype, 'ttl': 3600, 'values': list(record.values), 'template_key': record.key})
    merged = deepcopy(records)
    conflicts = []
    for row in desired:
        existing = next((r for r in merged if r['name'] == row['name'] and r['type'] == row['type']), None)
        if existing:
            if row.get('template_key') == 'spf' and not _protected(existing, zone) and not any(_txt_bytes(v).lower().startswith(b'v=spf1') for v in existing['values']):
                existing['values'].extend(row['values'])
                continue
            if not set(_canonical_values(row['type'], row['values'])) <= set(_canonical_values(existing['type'], existing['values'])):
                conflicts.append({'name': row['name'], 'type': row['type'], 'reason': 'Existing values preserved; edit explicitly if replacement is intended'})
        elif any(r['name'] == row['name'] and (r['type'] == 'CNAME' or row['type'] == 'CNAME') for r in merged):
            conflicts.append({'name': row['name'], 'type': row['type'], 'reason': 'Conflicting record preserved'})
        else:
            merged.append(row)
    return {'text': _text(zone, merged), 'fingerprint': _fingerprint(binding, records), 'conflicts': conflicts}


@dns_operations.serialized
def apply(params):
    zone = validate_domain(params['domain'])
    if params.get('confirmation') != zone:
        raise ValidationError('Type the full zone name to confirm the DNS edit')
    binding = _binding(zone)
    before = _records(zone, binding)
    if params.get('fingerprint') != _fingerprint(binding, before):
        raise ValidationError('DNS records changed. Reload and preview this edit again.')
    desired, changes = _plan(zone, params['text'], binding, before)
    if not changes:
        return {'zone': zone, 'changed': 0}
    from daemon.snapshot_jobs import private_directory
    directory = private_directory('dns-editor', f"account-{binding['account_id']}")
    backup = directory / (secrets.token_hex(16) + '.json')
    if binding['provider'] == 'cloudflare':
        from daemon import cloudflare, cloudflare_accounts, snapshot_cloudflare_recovery as recovery
        from daemon.snapshot_cloudflare_plan import plan
        with cloudflare.use_token(cloudflare_accounts.token_for_id(binding['cf_account_id'])):
            native = cloudflare.export_record_documents(zone, zone_id=binding['cf_zone_id'])
            if _fingerprint(binding, _records(zone, binding, native)) != params['fingerprint']:
                raise ValidationError('DNS records changed. Reload and preview this edit again.')
            writable, _ = recovery.partition(zone, native, current=True)
            selected = [deepcopy(r) for r in writable if _protected(r, zone)]
            for row in desired:
                originals = [r for r in writable if r['name'].rstrip('.') == row['name'] and r['type'] == row['type']]
                for value in row['values']:
                    original = next((r for r in originals if cloudflare._canonical_value(row['type'], cloudflare._from_cf_record(r)) == cloudflare._canonical_value(row['type'], value)), originals[0] if originals else None)
                    document = deepcopy(original) if original else {}
                    if row['type'] == 'TXT':
                        try:
                            value = cloudflare._quote_txt(_txt_bytes(value).decode('utf-8'))
                        except UnicodeDecodeError:
                            raise ValidationError('Cloudflare TXT records must contain UTF-8 text') from None
                    original_ttl = original.get('ttl') if original else None
                    displayed_ttl = 3600 if original_ttl == 1 else original_ttl
                    # BIND text cannot express Cloudflare's special Auto TTL.
                    # Retain Auto when its displayed value was not edited.
                    ttl = original_ttl if original and row['ttl'] == displayed_ttl else row['ttl']
                    document.update(cloudflare._to_cf_payload(row['type'], row['name'], value, ttl, bool(original and original.get('proxied'))))
                    if ttl == 1:
                        document['ttl'] = 1
                    document.pop('id', None)
                    selected.append(document)
            change_plan = plan(zone, writable, selected)
            if len(change_plan['batches']) > 1:
                raise ValidationError('Limit a Cloudflare raw edit to 200 changes at once; split larger edits into smaller reviewed batches')
            with backup.open('x') as file:
                json.dump({'zone': zone, 'binding': binding, 'records': native}, file)
            backup.chmod(0o600)
            def validate_binding():
                if _binding(zone) != binding:
                    raise ValidationError('DNS ownership or provider changed during the edit')
            recovery.apply(zone, binding['cf_zone_id'], selected, writable, validate_binding)
    else:
        from daemon import powerdns
        native = powerdns.get_zone(zone)
        if _fingerprint(binding, _records(zone, binding, native)) != params['fingerprint']:
            raise ValidationError('DNS records changed. Reload and preview this edit again.')
        with backup.open('x') as file:
            json.dump({'zone': zone, 'binding': binding, 'records': native['rrsets']}, file)
        backup.chmod(0o600)
        updates = [{'name': r['name'] + '.', 'type': r['type'], 'changetype': 'DELETE' if r['action'] == 'remove' else 'REPLACE',
                    **({'ttl': r['ttl'], 'records': [{'content': value, 'disabled': False} for value in r['after']]} if r['action'] != 'remove' else {})} for r in changes]
        powerdns.apply_rrset_changes(zone, updates)
        dnsprovider._cluster_notify(zone)
    # Limit recovery history to 20 private states per account. Zone records
    # and API credentials are never saved under a customer's writable home.
    states = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in states[20:]:
        stale.unlink()
    return {'zone': zone, 'changed': len(changes), 'previous_state_id': backup.stem}
