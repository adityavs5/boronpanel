"""HTTP-01 challenge writes execute with the hosted site's Unix privileges."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import pwd
import re
import shlex

from configobj import ConfigObj
from sqlalchemy import select

from daemon import safeio
from daemon.procutil import run
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import validate_domain, validate_username

HOOK_SCRIPT = '/opt/boron/scripts/acme_http_hook.py'
FILE_SCRIPT = '/opt/boron/scripts/acme_http_file.py'
RENEWAL_DIR = Path('/etc/letsencrypt/renewal')


def hook_command(action: str) -> str:
    if action not in {'auth', 'cleanup'}:
        raise ValueError('Invalid ACME action')
    python = str(Path(settings.certbot_bin).parent / 'python')
    return f'{shlex.quote(python)} -I {shlex.quote(HOOK_SCRIPT)} {action}'


def challenge_args() -> list[str]:
    return ['--authenticator', 'manual', '--preferred-challenges', 'http',
            '--manual-auth-hook', hook_command('auth'),
            '--manual-cleanup-hook', hook_command('cleanup')]


def _site(domain: str) -> tuple[str, str, int, int]:
    domain = validate_domain(domain)
    if domain == settings.webmail_hostname:
        owner = pwd.getpwnam('www-data')
        if owner.pw_uid <= 0 or owner.pw_gid <= 0:
            raise ValueError('Webmail identity must be unprivileged')
        root = Path(settings.webmail_docroot)
        if not root.is_absolute() or '..' in root.parts:
            raise ValueError('Invalid webmail document root')
        return str(root), str(root), owner.pw_uid, owner.pw_gid
    with write_session() as session:
        site = session.scalar(select(Domain).where(Domain.domain == domain))
        if site is None and domain.startswith('www.'):
            site = session.scalar(select(Domain).where(Domain.domain == domain[4:]))
        account = session.get(Account, site.account_id) if site is not None else None
        if account is None or account.status not in {'active', 'suspended'}:
            raise ValueError('ACME hostname has no provisioned hosting account')
        username = validate_username(account.username)
        docroot = site.docroot
        expected_uid, expected_gid = account.uid, account.gid
    owner = pwd.getpwnam(username)
    if owner.pw_uid <= 0 or owner.pw_gid <= 0 or (owner.pw_uid, owner.pw_gid) != (expected_uid, expected_gid):
        raise ValueError('ACME account Unix identity does not match')
    home = Path(settings.home_base) / username
    if not Path(docroot).is_absolute() or '..' in Path(docroot).parts:
        raise ValueError('Invalid ACME document root')
    Path(docroot).relative_to(home)  # lexical boundary; helper rejects symlinks
    return str(home), docroot, owner.pw_uid, owner.pw_gid


def perform(action: str, domain: str, token: str, validation: str) -> None:
    if action not in {'auth', 'cleanup'} or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', token):
        raise ValueError('Invalid ACME challenge')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,256}\.[A-Za-z0-9_-]{1,256}', validation):
        raise ValueError('Invalid ACME validation')
    home, docroot, uid, gid = _site(domain)
    result = run(['/usr/bin/python3', '-I', FILE_SCRIPT],
                 input_text=json.dumps(dict(action=action, home=home, docroot=docroot,
                                            token=token, validation=validation)),
                 uid=uid, gid=gid, cwd='/', timeout=15)
    result.raise_if_failed('HTTP certificate challenge could not be prepared safely')


def _migrate_renewals_locked() -> int:
    """Replace hosted webroot renewal writers without issuing a certificate.

    Only Boron-managed lineages are changed; infrastructure/foreign lineages
    retain their configured plugin. A protected original accompanies each edit.
    """
    with write_session() as session:
        domains = list(session.scalars(select(Domain.domain)).all())
    if settings.webmail_hostname:
        domains.append(settings.webmail_hostname)
    changed = 0
    for domain in set(domains):
        domain = validate_domain(domain)
        path = RENEWAL_DIR / f'{domain}.conf'
        original = safeio.secure_read_text(str(RENEWAL_DIR), path.name)
        if original is None:
            continue
        config = ConfigObj(original.splitlines(), encoding='utf-8')
        params = config.get('renewalparams', {})
        if params.get('authenticator') != 'webroot':
            continue
        params['authenticator'] = 'manual'
        params['pref_challs'] = ['http-01']
        params['manual_auth_hook'] = hook_command('auth')
        params['manual_cleanup_hook'] = hook_command('cleanup')
        params.pop('webroot_map', None)
        params.pop('webroot_path', None)
        backup = path.name + '.before-boron-safe-http'
        if not (RENEWAL_DIR / backup).exists():
            safeio.secure_replace_file(str(RENEWAL_DIR), backup, original.encode(), os.geteuid(), os.getegid())
        output = io.BytesIO()
        config.write(output)
        safeio.secure_replace_file(str(RENEWAL_DIR), path.name, output.getvalue(), os.geteuid(), os.getegid())
        changed += 1
    return changed


def migrate_renewals() -> int:
    if not RENEWAL_DIR.is_dir():
        return 0
    # Match Certbot's own config-directory lock: never rewrite a renewal
    # configuration while issuance/renewal is persisting its options.
    from certbot._internal.lock import lock_dir
    lock = lock_dir(str(RENEWAL_DIR.parent))
    try:
        return _migrate_renewals_locked()
    finally:
        lock.release()
