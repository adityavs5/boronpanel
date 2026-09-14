"""Validate native Cloudflare recovery records without flattening their fields."""
from copy import deepcopy
import re

import dns.name
import dns.rdata

from daemon import cloudflare
from shared.validation import ValidationError

SCALAR_TYPES = {'A', 'AAAA', 'CNAME', 'MX', 'NS', 'PTR', 'TXT', 'OPENPGPKEY'}
STRUCTURED_TYPES = {'CAA', 'SRV'}
READ_ONLY = {'id', 'zone_id', 'zone_name', 'proxiable', 'locked', 'created_on',
             'modified_on', 'comment_modified_on', 'tags_modified_on', 'meta'}
WRITABLE = {'name', 'type', 'ttl', 'content', 'priority', 'proxied', 'comment',
            'tags', 'settings', 'private_routing', 'data'}


def _string(value, label, maximum=65535, empty=False):
    if not isinstance(value, str) or (not value and not empty) or len(value) > maximum or '\x00' in value:
        raise ValidationError('Invalid saved Cloudflare ' + label)
    return value


def _integer(value, label, maximum=65535):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValidationError('Invalid saved Cloudflare ' + label)
    return value


def _name(value):
    value = _string(value, 'record name', 255)
    try:
        return dns.name.from_text(value, origin=dns.name.root).canonicalize()
    except Exception:
        raise ValidationError('Invalid saved Cloudflare record name') from None


def normalize(zone, raw, *, require_id=False):
    """Return only writable fields and, if requested, a validated current ID.

    Unsupported record data makes recovery unavailable before any mutation.
    The caller separately validates account/provider ownership and protects
    authority records; this function never makes a Cloudflare request.
    """
    if not isinstance(raw, dict) or set(raw) - READ_ONLY - WRITABLE:
        raise ValidationError('Unsupported fields in saved Cloudflare record')
    origin, owner = _name(zone), _name(raw.get('name'))
    if not owner.is_subdomain(origin):
        raise ValidationError('Saved Cloudflare record is outside the selected zone')
    rtype = raw.get('type')
    if not isinstance(rtype, str) or rtype not in SCALAR_TYPES | STRUCTURED_TYPES:
        raise ValidationError('Unsupported saved Cloudflare DNS record type')
    meta = raw.get('meta', {})
    if not isinstance(meta, dict) or type(raw.get('locked', False)) is not bool:
        raise ValidationError('Invalid Cloudflare managed-record metadata')
    if raw.get('locked') or any(meta.get(key) for key in ('auto_added', 'managed_by_apps', 'managed_by_argo_tunnel')):
        raise ValidationError('Provider-managed Cloudflare records require separate recovery handling')
    ttl = raw.get('ttl')
    if type(ttl) is not int or not (ttl == 1 or 30 <= ttl <= 86400):
        raise ValidationError('Invalid saved Cloudflare TTL')
    proxied = raw.get('proxied', False)
    if type(proxied) is not bool or (proxied and rtype not in cloudflare.PROXYABLE_TYPES):
        raise ValidationError('Invalid saved Cloudflare proxy status')
    if proxied and ttl != 1:
        raise ValidationError('Proxied Cloudflare records require automatic TTL')
    result = dict(name=owner.to_text().rstrip('.'), type=rtype, ttl=ttl, proxied=proxied)
    if require_id:
        ident = raw.get('id')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', ident):
            raise ValidationError('Invalid current Cloudflare record identifier')
        result['id'] = ident
    if rtype in SCALAR_TYPES:
        result['content'] = _string(raw.get('content'), 'record content', empty=rtype == 'TXT')
        if raw.get('data') not in (None, {}):
            raise ValidationError('Unsupported structured Cloudflare record data')
        if rtype == 'MX':
            result['priority'] = _integer(raw.get('priority'), 'MX priority')
        if rtype in {'CNAME', 'MX', 'NS', 'PTR'}:
            result['content'] = _name(result['content']).to_text().rstrip('.') or '.'
            if rtype == 'CNAME' and _name(result['content']) == owner:
                raise ValidationError('Saved CNAME points to itself')
    else:
        data = raw.get('data')
        fields = {'flags', 'tag', 'value'} if rtype == 'CAA' else {'priority', 'weight', 'port', 'target'}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValidationError('Incomplete or unsupported structured Cloudflare record')
        data = deepcopy(data)
        if rtype == 'CAA':
            _integer(data['flags'], 'CAA flags', 255)
            _string(data['tag'], 'CAA tag', 255)
            _string(data['value'], 'CAA value', empty=True)
        else:
            for key in ('priority', 'weight', 'port'): _integer(data[key], 'SRV ' + key)
            data['target'] = _name(data['target']).to_text().rstrip('.') or '.'
        result['data'] = data
    # The API returns redundant content for structured records. Validate against
    # the structured fields, which are the writable authority for those types.
    try:
        parsed = dns.rdata.from_text('IN', rtype, cloudflare._from_cf_record(result), origin=origin, relativize=False)
        if rtype in STRUCTURED_TYPES and raw.get('content') is not None:
            redundant = dns.rdata.from_text('IN', rtype, _string(raw['content'], 'record content'), origin=origin, relativize=False)
            if parsed != redundant:
                raise ValueError('Conflicting native representations')
    except Exception:
        raise ValidationError('Saved Cloudflare content is invalid for its DNS type') from None
    comment = raw.get('comment')
    result['comment'] = _string('' if comment is None else comment, 'comment', empty=True)
    tags = raw.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 1000:
        raise ValidationError('Invalid saved Cloudflare tags')
    result['tags'] = sorted(set(_string(tag, 'tag', 1024) for tag in tags))
    options = raw.get('settings', {})
    allowed = {'ipv4_only', 'ipv6_only'} | ({'flatten_cname'} if rtype == 'CNAME' else set())
    if not isinstance(options, dict) or set(options) - allowed or any(type(value) is not bool for value in options.values()):
        raise ValidationError('Unsupported saved Cloudflare record settings')
    result['settings'] = deepcopy(options)
    if 'private_routing' in raw:
        if rtype not in {'A', 'AAAA'} or type(raw['private_routing']) is not bool:
            raise ValidationError('Invalid saved Cloudflare private routing setting')
        result['private_routing'] = raw['private_routing']
    return result
