"""Validate native Cloudflare recovery records without flattening their fields."""
from copy import deepcopy
import re
import math
import base64

import dns.name
import dns.rdata

from daemon import cloudflare
from shared.validation import ValidationError

SCALAR_TYPES = {'A', 'AAAA', 'CNAME', 'MX', 'NS', 'PTR', 'TXT', 'OPENPGPKEY'}
STRUCTURED_TYPES = {'CAA', 'SRV', 'HTTPS', 'SVCB', 'TLSA', 'DS', 'SMIMEA', 'SSHFP', 'CERT', 'NAPTR', 'URI', 'LOC'}
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


def _structured_text(rtype, data, priority=None):
    if rtype in {'HTTPS', 'SVCB'}:
        target = data['target'].rstrip('.') + '.'
        return f"{data['priority']} {target} {data['value']}"
    if rtype in {'TLSA', 'SMIMEA'}:
        return f"{data['usage']} {data['selector']} {data['matching_type']} {data['certificate']}"
    if rtype == 'DS':
        return f"{data['key_tag']} {data['algorithm']} {data['digest_type']} {data['digest']}"
    if rtype == 'SSHFP':
        return f"{data['algorithm']} {data['type']} {data['fingerprint']}"
    if rtype == 'CERT':
        return f"{data['type']} {data['key_tag']} {data['algorithm']} {data['certificate']}"
    if rtype == 'URI':
        return f"{priority} {data['weight']} {cloudflare._quote_txt(data['target'])}"
    if rtype == 'NAPTR':
        quoted = ' '.join(cloudflare._quote_txt(data[key]) for key in ('flags', 'service', 'regex'))
        return f"{data['order']} {data['preference']} {quoted} {data['replacement'].rstrip('.')}."
    if rtype == 'LOC':
        coordinates = ' '.join(str(data[key]) for key in ('lat_degrees','lat_minutes','lat_seconds','lat_direction',
                                                          'long_degrees','long_minutes','long_seconds','long_direction'))
        return coordinates + ' ' + ' '.join(f'{data[key]}m' for key in ('altitude','size','precision_horz','precision_vert'))
    return cloudflare._from_cf_record({'type': rtype, 'data': data})


def _legacy_data(rtype, content):
    """Translate content-only native records through the DNS parser."""
    try:
        parsed = dns.rdata.from_text('IN', rtype, _string(content, 'record content'), origin=dns.name.root, relativize=False)
        def decoded(value):
            if isinstance(value, bytes): return value.decode('utf-8')
            if isinstance(value, int): return int(value)
            return value
        mappings = {
            'CAA': {'flags':'flags','tag':'tag','value':'value'},
            'SRV': {'priority':'priority','weight':'weight','port':'port','target':'target'},
            'NAPTR': {'order':'order','preference':'preference','flags':'flags','service':'service','regex':'regexp','replacement':'replacement'},
            'CERT': {'type':'certificate_type','key_tag':'key_tag','algorithm':'algorithm'},
            'URI': {'weight':'weight','target':'target'},
        }
        if rtype in mappings:
            data = {key: decoded(getattr(parsed, attr)) for key, attr in mappings[rtype].items()}
            for key,value in list(data.items()):
                if isinstance(value,dns.name.Name): data[key]=value.to_text()
            if rtype == 'CERT': data['certificate']=base64.b64encode(parsed.certificate).decode('ascii')
            return data, getattr(parsed,'priority',None)
        if rtype in {'TLSA','SMIMEA'}:
            return dict(usage=parsed.usage,selector=parsed.selector,matching_type=parsed.mtype,certificate=parsed.cert.hex()),None
        if rtype == 'SSHFP': return dict(algorithm=parsed.algorithm,type=parsed.fp_type,fingerprint=parsed.fingerprint.hex()),None
        if rtype == 'DS': return dict(key_tag=int(parsed.key_tag),algorithm=int(parsed.algorithm),digest_type=int(parsed.digest_type),digest=parsed.digest.hex()),None
        if rtype in {'HTTPS','SVCB'}:
            parts=parsed.to_text(relativize=False).split(None,2)
            return dict(priority=parsed.priority,target=parsed.target.to_text(),value=parts[2] if len(parts)>2 else ''),None
        if rtype == 'LOC':
            data=dict(altitude=parsed.altitude/100,size=parsed.size/100,
                      precision_horz=parsed.horizontal_precision/100,precision_vert=parsed.vertical_precision/100)
            for prefix,coordinates,directions in (('lat',parsed.latitude,('S','N')),('long',parsed.longitude,('W','E'))):
                data.update({prefix+'_degrees':coordinates[0],prefix+'_minutes':coordinates[1],
                             prefix+'_seconds':coordinates[2]+coordinates[3]/1000,prefix+'_direction':directions[coordinates[4]>0]})
            return data,None
    except Exception:
        raise ValidationError('Invalid legacy Cloudflare structured content') from None
    raise ValidationError('Unsupported legacy Cloudflare structured record')


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
        legacy_priority = None
        if data is None or data == {}:
            data, legacy_priority = _legacy_data(rtype, raw.get('content'))
        fields = {
            'CAA': {'flags', 'tag', 'value'}, 'SRV': {'priority', 'weight', 'port', 'target'},
            'HTTPS': {'priority', 'target', 'value'}, 'SVCB': {'priority', 'target', 'value'},
            'TLSA': {'usage', 'selector', 'matching_type', 'certificate'},
            'DS': {'key_tag', 'algorithm', 'digest_type', 'digest'},
            'SMIMEA': {'usage', 'selector', 'matching_type', 'certificate'},
            'SSHFP': {'algorithm', 'type', 'fingerprint'},
            'CERT': {'type', 'key_tag', 'algorithm', 'certificate'},
            'NAPTR': {'order', 'preference', 'flags', 'service', 'regex', 'replacement'},
            'URI': {'weight', 'target'},
            'LOC': {'lat_degrees','lat_minutes','lat_seconds','lat_direction','long_degrees','long_minutes',
                    'long_seconds','long_direction','altitude','size','precision_horz','precision_vert'},
        }[rtype]
        if not isinstance(data, dict) or set(data) != fields:
            raise ValidationError('Incomplete or unsupported structured Cloudflare record')
        data = deepcopy(data)
        if rtype == 'CAA':
            _integer(data['flags'], 'CAA flags', 255)
            _string(data['tag'], 'CAA tag', 255)
            _string(data['value'], 'CAA value', empty=True)
        elif rtype == 'SRV':
            for key in ('priority', 'weight', 'port'): _integer(data[key], 'SRV ' + key)
            data['target'] = _name(data['target']).to_text().rstrip('.') or '.'
        elif rtype in {'HTTPS', 'SVCB'}:
            _integer(data['priority'], rtype + ' priority')
            data['target'] = _name(data['target']).to_text().rstrip('.') or '.'
            _string(data['value'], rtype + ' parameters', empty=True)
        elif rtype == 'URI':
            result['priority'] = _integer(raw.get('priority', legacy_priority), 'URI priority')
            _integer(data['weight'], 'URI weight')
            _string(data['target'], 'URI target')
        elif rtype == 'NAPTR':
            for key in ('order', 'preference'): _integer(data[key], 'NAPTR ' + key)
            for key in ('flags','service','regex'): _string(data[key], 'NAPTR ' + key, 255, empty=True)
            data['replacement'] = _name(data['replacement']).to_text().rstrip('.') or '.'
        elif rtype == 'CERT':
            for key in ('type', 'key_tag', 'algorithm'): _integer(data[key], 'CERT ' + key, 255 if key == 'algorithm' else 65535)
            try:
                encoded = _string(data['certificate'], 'CERT certificate')
                data['certificate'] = base64.b64encode(base64.b64decode(encoded, validate=True)).decode('ascii')
            except (ValueError, UnicodeError):
                raise ValidationError('Invalid saved Cloudflare certificate encoding') from None
        elif rtype == 'LOC':
            for key, maximum in (('lat_degrees',90),('long_degrees',180),('lat_minutes',59),('long_minutes',59)):
                _integer(data[key], 'LOC ' + key, maximum)
            if data['lat_direction'] not in ('N','S') or data['long_direction'] not in ('E','W'):
                raise ValidationError('Invalid saved Cloudflare LOC direction')
            for key in ('lat_seconds','long_seconds','altitude','size','precision_horz','precision_vert'):
                minimum, maximum = (-100000,42849672.95) if key == 'altitude' else (0,59.999) if key.endswith('seconds') else (0,90000000)
                if type(data[key]) not in (int,float) or not math.isfinite(data[key]) or not minimum <= data[key] <= maximum:
                    raise ValidationError('Invalid saved Cloudflare LOC measurement')
            for prefix, maximum in (('lat',90),('long',180)):
                if data[prefix+'_degrees'] == maximum and (data[prefix+'_minutes'] or data[prefix+'_seconds']):
                    raise ValidationError('Saved Cloudflare LOC coordinates exceed geographic bounds')
        else:
            integers = ('usage', 'selector', 'matching_type') if rtype in {'TLSA','SMIMEA'} else ('algorithm','type') if rtype == 'SSHFP' else ('key_tag', 'algorithm', 'digest_type')
            for key in integers: _integer(data[key], rtype + ' ' + key, 65535 if key == 'key_tag' else 255)
            field = 'certificate' if rtype in {'TLSA','SMIMEA'} else 'fingerprint' if rtype == 'SSHFP' else 'digest'
            value = _string(data[field], rtype + ' ' + field)
            if not re.fullmatch(r'(?:[a-fA-F0-9]{2})+', value):
                raise ValidationError('Invalid saved Cloudflare hexadecimal record data')
            data[field] = value.lower()
        result['data'] = data
    # The API returns redundant content for structured records. Validate against
    # the structured fields, which are the writable authority for those types.
    try:
        text = _structured_text(rtype, result['data'], result.get('priority')) if rtype in STRUCTURED_TYPES else cloudflare._from_cf_record(result)
        parsed = dns.rdata.from_text('IN', rtype, text, origin=origin, relativize=False)
        if rtype in {'A', 'AAAA'}:
            result['content'] = parsed.address
        if rtype in {'HTTPS', 'SVCB'}:
            parts = parsed.to_text(origin=origin, relativize=False).split(None, 2)
            result['data']['value'] = parts[2] if len(parts) > 2 else ''
        if rtype == 'LOC':
            result['data'], _ = _legacy_data(rtype, parsed.to_text())
        if rtype in STRUCTURED_TYPES and raw.get('content') is not None:
            redundant = dns.rdata.from_text('IN', rtype, _string(raw['content'], 'record content'), origin=dns.name.root, relativize=False)
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
