"""Dedicated panel HTTP-01 routing, independent of hosted customer sites."""
import os
import pwd
import datetime as dt
from pathlib import Path
from cryptography import x509
from sqlalchemy import select
from shared.config import settings
from shared.db import write_session
from shared.models import Domain
from shared.validation import ValidationError,validate_domain
from daemon import ols
from daemon.configtx import ConfigWriterMulti



def _challenge_owner():
    # OLS validates docroot ownership even for static, script-disabled vhosts.
    # A dedicated non-login identity avoids root-UID validation warnings without
    # giving its worker or any hosting account ownership of the challenge tree.
    from daemon.procutil import run
    try:
        owner = pwd.getpwnam('boron-acme')
    except KeyError:
        run(['useradd', '--system', '--user-group', '--no-create-home',
             '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin',
             'boron-acme'], check=True)
        owner = pwd.getpwnam('boron-acme')
    if owner.pw_uid < 11 or owner.pw_gid < 10 or owner.pw_shell != '/usr/sbin/nologin':
        raise ValidationError('The boron-acme service identity must be an unprivileged non-login account')
    return owner


def bootstrap_challenge():
    hostname=validate_domain(settings.panel_hostname)
    if hostname in (settings.webmail_hostname,settings.pma_hostname):
        raise ValidationError('Panel hostname conflicts with another infrastructure service')
    webroot=Path(settings.panel_acme_webroot)
    if not webroot.is_absolute() or webroot.resolve()!=webroot:
        raise ValidationError('Panel certificate webroot must be an absolute path without symlinks')
    with write_session() as session:
        if session.scalar(select(Domain.id).where(Domain.domain==hostname)):
            raise ValidationError('Panel hostname is already registered as a customer site')
        vhosts,processes=ols._all_active_vhosts(session)
        waf=ols.waf_template_context(session)
    challenge=webroot/'.well-known/acme-challenge'
    if challenge.resolve()!=challenge:raise ValidationError('Panel challenge directory contains a symbolic link')
    challenge.mkdir(parents=True,exist_ok=True,mode=0o755)
    owner = _challenge_owner()
    os.chown(webroot, owner.pw_uid, owner.pw_gid)
    webroot.chmod(0o555)
    for directory in (webroot/'.well-known', challenge):
        os.chown(directory, 0, 0)
        directory.chmod(0o755)
    content=ols._env.get_template('panel_acme_vhost.conf.j2').render(webroot=str(webroot))
    writer=ConfigWriterMulti(targets={'main':ols.HTTPD_CONFIG_PATH,'panel':str(Path(settings.vhost_conf_dir)/'boron-panel-acme/vhconf.conf')},
        validate=ols._validate_multi,reload=ols._reload,verify=ols._verify,backup_dir=settings.backup_dir,subsystem='ols')
    result=writer.apply({'main':ols.render_httpd_config(vhosts,processes,waf=waf),'panel':content})
    if not result.ok:raise RuntimeError('Panel challenge routing failed: '+result.summary())
    return {'hostname':hostname,'webroot':str(webroot)}


def issue_certificate(email):
    """Issue/renew through the dedicated root; certbot persists the deploy hook."""
    import shlex
    from shared.validation import validate_email_address
    from daemon.procutil import run
    email=validate_email_address(email)
    route=bootstrap_challenge()
    hook=shlex.join([str(Path(settings.certbot_bin).parent/'python'),
        '/opt/boron/scripts/panel_ssl_deploy.py','--hostname',route['hostname']])
    result=run([settings.certbot_bin,'certonly','--non-interactive','--agree-tos',
        '--email',email,'--cert-name',route['hostname'],'-d',route['hostname'],
        '--webroot','-w',route['webroot'],'--keep-until-expiring','--deploy-hook',hook],timeout=1200)
    result.raise_if_failed('Issue panel certificate')
    return {'hostname':route['hostname'],'status':'issued'}


def certificate_status(params=None):
    path = Path('/etc/boron/ssl/api/panel.crt')
    result = {'hostname': settings.panel_hostname, 'configured': bool(settings.panel_hostname),
              'certificate_present': path.is_file(), 'valid_for_hostname': False,
              'issuer': None, 'expires_at': None, 'days_remaining': None}
    if not path.is_file():
        return result
    try:
        cert = x509.load_pem_x509_certificate(path.read_bytes())
        expiry = cert.not_valid_after_utc
        names = set()
        try:
            names.update(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
        except x509.ExtensionNotFound:
            pass
        result.update({
            'valid_for_hostname': settings.panel_hostname in names,
            'issuer': cert.issuer.rfc4514_string(),
            'expires_at': expiry.isoformat(),
            'days_remaining': max(0, int((expiry - dt.datetime.now(dt.timezone.utc)).total_seconds() // 86400)),
        })
    except (ValueError, OSError):
        result['error'] = 'Installed certificate could not be parsed'
    return result


def issue_from_rpc(params):
    return issue_certificate(params['email'])
