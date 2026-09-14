"""Native Cloudflare recovery with protected records and observed batch outcomes."""
from copy import deepcopy
import json
import re

from daemon import cloudflare
from daemon.snapshot_cloudflare_records import _name
from daemon.snapshot_cloudflare_plan import collection, key, plan, state
from shared.validation import ValidationError

AUTHORITY_TYPES = {'SOA', 'DNSKEY', 'CDNSKEY', 'CDS', 'RRSIG', 'NSEC', 'NSEC3', 'NSEC3PARAM'}


def partition(zone, records, *, current=False):
    if not isinstance(records, list) or len(records) > 100000:
        raise ValidationError('Invalid Cloudflare recovery record collection')
    origin = _name(zone)
    writable, protected, ids = [], [], set()
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('type'), str):
            raise ValidationError('Invalid Cloudflare recovery record')
        owner = _name(row.get('name'))
        if not owner.is_subdomain(origin):
            raise ValidationError('Cloudflare record is outside the selected zone')
        if current:
            ident = row.get('id')
            if not isinstance(ident, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', ident) or ident in ids:
                raise ValidationError('Invalid or duplicate current Cloudflare record ID')
            ids.add(ident)
        meta = row.get('meta', {})
        if not isinstance(meta, dict) or type(row.get('locked', False)) is not bool:
            raise ValidationError('Invalid Cloudflare managed-record metadata')
        managed = row.get('locked') or any(meta.get(key) for key in ('auto_added', 'managed_by_apps', 'managed_by_argo_tunnel'))
        authority = row['type'] in AUTHORITY_TYPES or (row['type'] == 'NS' and owner == origin)
        (protected if managed or authority else writable).append(deepcopy(row))
    return writable, protected


def validate(zone, records):
    writable, _ = partition(zone, records)
    return collection(zone, writable)


def _protected_state(records):
    return sorted(json.dumps({key: value for key, value in row.items()
                              if key not in {'created_on', 'modified_on', 'comment_modified_on', 'tags_modified_on'}},
                             sort_keys=True) for row in records)


def apply(zone, zone_id, desired, expected_before, validate_binding, on_batch=None):
    """Caller holds DNS/account/repository locks and encrypted prior state.

    Every batch is submitted once. A failed response may still be accepted only
    when a fresh read proves its entire expected result. Any other outcome stops
    with the encrypted previous configuration available for explicit undo.
    """
    validate_binding()
    raw = cloudflare.export_record_documents(zone, zone_id=zone_id)
    current, protected = partition(zone, raw, current=True)
    if state(zone, current) != state(zone, expected_before):
        raise ValidationError('Cloudflare records changed while preparing recovery')
    selected = validate(zone, desired)
    # A managed owner must not be indirectly replaced through a conflicting
    # customer record. Authority metadata (including apex NS) is kept separately.
    managed_owners = {_name(row['name']) for row in protected
                      if row['type'] not in AUTHORITY_TYPES and not (row['type'] == 'NS' and _name(row['name']) == _name(zone))}
    if any(_name(row['name']) in managed_owners for row in selected):
        raise ValidationError('Selected DNS records overlap provider-managed records')
    changes = plan(zone, current, selected)
    protected_before = _protected_state(protected)
    for index, batch in enumerate(changes['batches']):
        validate_binding()
        fresh, guarded = partition(zone, cloudflare.export_record_documents(zone, zone_id=zone_id), current=True)
        if state(zone, fresh) != state(zone, current) or _protected_state(guarded) != protected_before:
            raise ValidationError('Cloudflare records changed before a recovery batch')
        # IDs are part of the write authority even when record values are equal.
        if ({row['id']: key(row) for row in collection(zone, fresh, current=True)}
                != {row['id']: key(row) for row in collection(zone, current, current=True)}):
            raise ValidationError('Cloudflare record identities changed before recovery')
        expected = {row['id']: row for row in current}
        for row in batch.get('deletes', []): expected.pop(row['id'])
        for row in batch.get('puts', []): expected[row['id']] = row
        after = list(expected.values()) + batch.get('posts', [])
        try:
            cloudflare.apply_record_batch(zone, batch, zone_id=zone_id)
        except cloudflare.CloudflareError:
            # Do not repeat. The provider may have committed before an edge or
            # transport failure; the following read decides the outcome.
            pass
        validate_binding()
        actual, guarded = partition(zone, cloudflare.export_record_documents(zone, zone_id=zone_id), current=True)
        if state(zone, actual) != state(zone, after) or _protected_state(guarded) != protected_before:
            raise ValidationError('Cloudflare recovery batch could not be confirmed; previous configuration is retained')
        current = actual
        if on_batch: on_batch(index + 1, len(changes['batches']))
    validate_binding()
    if state(zone, current) != state(zone, selected):
        raise ValidationError('Cloudflare recovery final verification failed')
    return changes
