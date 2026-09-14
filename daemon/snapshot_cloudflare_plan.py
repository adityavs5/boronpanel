"""Pure current-ID planning for Cloudflare DNS recovery; never sends requests."""
from collections import defaultdict, deque
from copy import deepcopy
import json

from daemon.snapshot_cloudflare_records import normalize
from shared.validation import ValidationError


def key(record):
    """Ignore transport IDs and explicitly disabled optional settings."""
    value = {name: deepcopy(content) for name, content in record.items() if name != 'id'}
    value['settings'] = {name: enabled for name, enabled in value.get('settings', {}).items() if enabled}
    if value.get('private_routing') is False:
        value.pop('private_routing')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def identity(record):
    return json.dumps({name: record[name] for name in ('name', 'type', 'content', 'data', 'priority') if name in record}, sort_keys=True)


def collection(zone, records, *, current=False):
    if not isinstance(records, list) or len(records) > 100000:
        raise ValidationError('Invalid Cloudflare recovery record collection')
    rows = [normalize(zone, row, require_id=current) for row in records]
    if current and len({row['id'] for row in rows}) != len(rows):
        raise ValidationError('Duplicate current Cloudflare record identifiers')
    by_name = defaultdict(list)
    for row in rows: by_name[row['name']].append(row)
    for group in by_name.values():
        cnames = [row for row in group if row['type'] == 'CNAME']
        # Cloudflare permits apex flattening alongside non-address records, but
        # never multiple CNAMEs or a CNAME alongside explicit address records.
        if len(cnames) > 1 or (cnames and any(row['type'] in ('A', 'AAAA') for row in group)):
            raise ValidationError('Conflicting saved Cloudflare CNAME records')
        if any(row['type'] == 'NS' for row in group) and any(row['type'] not in {'NS', 'DS'} for row in group):
            raise ValidationError('Cloudflare nameserver records conflict with other records')
    return rows


def state(zone, records):
    """Compare complete writable values, preserving duplicate-record counts."""
    return sorted(key(row) for row in collection(zone, records))


def plan(zone, current, desired):
    """Build deterministic batches without trusting a saved record ID.

    The caller must first partition provider-managed/authority records and prove
    ownership. It must save previous state, recheck it and durably checkpoint
    each batch. This function only computes the changes and cannot authorize them.
    """
    before = collection(zone, current, current=True)
    after = collection(zone, desired)
    available = defaultdict(deque)
    for row in sorted(before, key=lambda row: row['id']): available[key(row)].append(row)
    remaining = []
    unchanged = 0
    for row in sorted(after, key=key):
        matches = available[key(row)]
        if matches:
            matches.popleft(); unchanged += 1
        else:
            remaining.append(row)
    old_groups, new_groups = defaultdict(list), defaultdict(list)
    for matches in available.values():
        for row in matches: old_groups[(row['name'], row['type'])].append(row)
    for row in remaining: new_groups[(row['name'], row['type'])].append(row)
    deletes, puts, posts = [], [], []
    for group in sorted(old_groups.keys() | new_groups.keys()):
        old = sorted(old_groups[group], key=lambda row: row['id'])
        new = sorted(new_groups[group], key=key)
        # Match the same DNS value first when only TTL/proxy/comment/settings
        # changed. Arbitrary pairing could temporarily duplicate another value.
        candidates = defaultdict(deque)
        for previous in old: candidates[identity(previous)].append(previous)
        pairs, unmatched, used = [], [], set()
        for row in new:
            matches = candidates[identity(row)]
            if matches:
                match = matches.popleft(); pairs.append((match, row)); used.add(match['id'])
            else:
                unmatched.append(row)
        old = [previous for previous in old if previous['id'] not in used]
        new = unmatched
        puts.extend(dict(**row, id=previous['id']) for previous, row in pairs)
        paired = min(len(old), len(new))
        puts.extend(dict(**new[index], id=old[index]['id']) for index in range(paired))
        deletes.extend({'id': row['id']} for row in old[paired:])
        posts.extend(new[paired:])
    # Deletions precede every update/create, including across batch boundaries,
    # so replacing an address RRset with a CNAME cannot race its old records.
    ordered = [('deletes', row) for row in deletes] + [('puts', row) for row in puts] + [('posts', row) for row in posts]
    batches = []
    for offset in range(0, len(ordered), 200):
        batch = defaultdict(list)
        for kind, row in ordered[offset:offset + 200]: batch[kind].append(deepcopy(row))
        batches.append(dict(batch))
    return {'batches': batches, 'unchanged': unchanged,
            'deletes': len(deletes), 'updates': len(puts), 'creates': len(posts)}
