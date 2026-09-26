"""Validated in-place recovery of account domain/vhost metadata."""
from pathlib import Path

from sqlalchemy import select

from daemon import ols
from shared.config import settings
from shared.db import write_session
from shared.models import Domain
from shared.validation import ValidationError, validate_domain, validate_php_version


def capture(account):
    with write_session() as session:
        rows=session.scalars(select(Domain).where(Domain.account_id==account.id).order_by(Domain.domain)).all()
        return {'format':1,'domains':[{'domain':row.domain,'kind':row.kind,'docroot':row.docroot,
            'php_version':row.php_version,'suspended':bool(row.suspended),
            'suspension_reason':row.suspension_reason} for row in rows]}


def validate_for_restore(account,payload):
    if not isinstance(payload,dict) or payload.get('format')!=1 or not isinstance(payload.get('domains'),list) or len(payload['domains'])>1000:
        raise ValidationError('Recovery point contains invalid domain metadata')
    with write_session() as session:
        current={row.domain:row for row in session.scalars(select(Domain).where(Domain.account_id==account.id)).all()}
    home=(Path(settings.home_base)/account.username).resolve();normalized=[];seen=set()
    for item in payload['domains']:
        if not isinstance(item,dict):raise ValidationError('Recovery point contains invalid domain metadata')
        name=validate_domain(item.get('domain'))
        if name in seen:raise ValidationError('Recovery point contains duplicate domain metadata')
        seen.add(name);existing=current.get(name)
        if existing is None:raise ValidationError(f'Recreate domain {name} before restoring its saved settings')
        kind=item.get('kind')
        if kind not in ('primary','addon','subdomain','parked') or kind!=existing.kind:
            raise ValidationError(f'Domain type for {name} no longer matches this recovery point')
        docroot=Path(str(item.get('docroot') or ''))
        if not docroot.is_absolute() or not docroot.resolve().is_relative_to(home):
            raise ValidationError(f'Recovery point contains an unsafe document root for {name}')
        php=item.get('php_version')
        if php is not None:php=validate_php_version(php,settings.php_versions)
        suspended=item.get('suspended')
        if not isinstance(suspended,bool):raise ValidationError('Recovery point contains an invalid domain state')
        reason=item.get('suspension_reason')
        if reason is not None:
            if not isinstance(reason,str) or len(reason)>500 or any(char in reason for char in '\0\r\n'):
                raise ValidationError('Recovery point contains an invalid suspension reason')
            reason=reason.strip() or None
        normalized.append({'domain':name,'kind':kind,'docroot':str(docroot),'php_version':php,
            'suspended':suspended,'suspension_reason':reason if suspended else None})
    return {'format':1,'domains':normalized}


def apply_configuration(account,payload,save_previous):
    selected=validate_for_restore(account,payload);previous=capture(account);save_previous(previous)
    try:
        with write_session() as session:
            rows={row.domain:row for row in session.scalars(select(Domain).where(Domain.account_id==account.id)).all()}
            for item in selected['domains']:
                row=rows[item['domain']];row.docroot=item['docroot'];row.php_version=item['php_version']
                row.suspended=item['suspended'];row.suspension_reason=item['suspension_reason']
        ols.refresh_vhost(account)
    except Exception:
        with write_session() as session:
            rows={row.domain:row for row in session.scalars(select(Domain).where(Domain.account_id==account.id)).all()}
            for item in previous['domains']:
                row=rows.get(item['domain'])
                if row:
                    row.docroot=item['docroot'];row.php_version=item['php_version'];row.suspended=item['suspended']
                    row.suspension_reason=item['suspension_reason']
        try:ols.refresh_vhost(account)
        except Exception:pass
        raise
    return {'domains':len(selected['domains'])}
