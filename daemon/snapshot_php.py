"""Capture complete PHP control-plane settings inside encrypted backups.

This payload is private recovery metadata. Administrator function restrictions
are recorded separately and must never be applied by customer self-service.
"""
from sqlalchemy import select, text

from shared.db import write_session
from shared.models import (Account, Domain, PhpIniOverride, PhpIniDirective,
                           PhpExtensionSet, PhpFunctionOverride)
from shared.validation import ValidationError

INI_FIELDS = ('memory_limit', 'upload_max_filesize', 'post_max_size',
              'max_execution_time', 'display_errors', 'error_reporting')


def capture(account):
    with write_session() as session:
        # SQLite's legacy transaction mode otherwise does not start a database
        # read transaction for SELECTs. Bind all tables to one consistent view.
        connection = session.connection()
        if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
            session.execute(text('BEGIN'))
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer available for PHP configuration backup')
        domains = session.scalars(select(Domain).where(Domain.account_id == current.id).order_by(Domain.domain)).all()
        ini = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == current.id))
        directives = session.scalars(select(PhpIniDirective).where(
            PhpIniDirective.account_id == current.id).order_by(PhpIniDirective.name)).all()
        extensions = session.scalar(select(PhpExtensionSet).where(PhpExtensionSet.account_id == current.id))
        functions = session.scalars(select(PhpFunctionOverride).where(
            PhpFunctionOverride.account_id == current.id).order_by(PhpFunctionOverride.domain)).all()
        owned = {domain.domain for domain in domains}
        if any(row.domain is not None and row.domain not in owned for row in functions):
            raise ValidationError('PHP function policy refers to an unowned domain')
        return dict(format=1, account_id=current.id, username=current.username,
                    default_version=current.php_version,
                    sites=[{'domain': domain.domain, 'version_override': domain.php_version} for domain in domains],
                    ini_override={name: getattr(ini, name) for name in INI_FIELDS} if ini else None,
                    extra_directives={row.name: row.value for row in directives},
                    enabled_extensions=list(extensions.enabled) if extensions else None,
                    administrator_function_policy=[{'domain': row.domain, 'disable_functions': row.disable_functions}
                                                   for row in functions])


def validate_for_restore(account, payload):
    """Normalize only customer-controlled PHP settings; never restore policy."""
    from daemon import phpdirectives, phpext
    from shared.config import settings
    from shared.validation import (validate_php_version, validate_php_memory_limit,
        validate_php_size, validate_php_max_execution_time, validate_php_error_reporting, _php_size_to_mb)
    if (not isinstance(payload, dict) or type(payload.get('format')) is not int or payload['format'] != 1
            or type(payload.get('account_id')) is not int or payload['account_id'] != account.id
            or payload.get('username') != account.username):
        raise ValidationError('PHP recovery settings belong to another account or format')
    required = {'default_version', 'sites', 'ini_override', 'extra_directives', 'enabled_extensions'}
    if not required <= payload.keys():
        raise ValidationError('PHP recovery settings are incomplete')
    if not isinstance(payload['default_version'], str):
        raise ValidationError('Invalid saved PHP version')
    version = validate_php_version(payload['default_version'], settings.php_versions)
    sites = payload['sites']
    if not isinstance(sites, list) or len(sites) > 10000:
        raise ValidationError('Invalid PHP site selection')
    with write_session() as session:
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer available for PHP recovery')
        owned = set(session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all())
    normalized_sites = []
    seen = set()
    for site in sites:
        if (not isinstance(site, dict) or set(site) != {'domain', 'version_override'}
                or not isinstance(site['domain'], str) or site['domain'] not in owned or site['domain'] in seen):
            raise ValidationError('A saved PHP site is missing, duplicated or no longer owned by this account')
        seen.add(site['domain'])
        override = site['version_override']
        if override is not None and not isinstance(override, str):
            raise ValidationError('Invalid saved site PHP version')
        normalized_sites.append(dict(domain=site['domain'],
            version_override=validate_php_version(override, settings.php_versions) if override is not None else None))
    ini = payload['ini_override']
    if ini is not None:
        if (not isinstance(ini, dict) or set(ini) != set(INI_FIELDS)
                or type(ini['display_errors']) is not bool or type(ini['max_execution_time']) is not int
                or any(not isinstance(ini[name], str) for name in ('memory_limit', 'upload_max_filesize', 'post_max_size', 'error_reporting'))):
            raise ValidationError('Invalid PHP limits in recovery point')
        ini = dict(memory_limit=validate_php_memory_limit(ini['memory_limit']),
                   upload_max_filesize=validate_php_size(ini['upload_max_filesize'], 'upload_max_filesize'),
                   post_max_size=validate_php_size(ini['post_max_size'], 'post_max_size'),
                   max_execution_time=validate_php_max_execution_time(ini['max_execution_time']),
                   display_errors=ini['display_errors'], error_reporting=validate_php_error_reporting(ini['error_reporting']))
        if _php_size_to_mb(ini['post_max_size']) < _php_size_to_mb(ini['upload_max_filesize']):
            raise ValidationError('Saved post_max_size must be at least upload_max_filesize')
    extras = payload['extra_directives']
    if not isinstance(extras, dict) or not set(extras) <= phpdirectives.EXTRA_DIRECTIVES.keys():
        raise ValidationError('Recovery point contains unsupported PHP directives')
    normalized_extras = {}
    for name, value in extras.items():
        spec = phpdirectives.EXTRA_DIRECTIVES[name]
        if value is None or (spec['type'] != 'bool' and isinstance(value, bool)):
            raise ValidationError('Invalid saved PHP directive value')
        if spec['type'] == 'bool' and not (type(value) is bool or isinstance(value, str) and value in ('On', 'Off')):
            raise ValidationError('Invalid saved PHP boolean directive')
        if spec['type'] == 'int' and type(value) not in (str, int):
            raise ValidationError('Invalid saved PHP integer directive')
        if spec['type'] == 'timezone' and not isinstance(value, str):
            raise ValidationError('Invalid saved PHP timezone directive')
        normalized_extras[name] = phpdirectives.validate(name, value)
    extensions = payload['enabled_extensions']
    if extensions is not None:
        inventory = phpext.inventory()
        if not isinstance(extensions, list) or any(not isinstance(name, str) or name not in inventory for name in extensions):
            raise ValidationError('Recovery point selects a PHP extension that is not installed')
        extensions = sorted(set(extensions))
        for name in extensions:
            if any(dependency not in extensions for dependency in phpext.EXTENSION_DEPS.get(name, ())):
                raise ValidationError('Saved PHP extensions lack a required dependency')
    return dict(format=1, account_id=account.id, username=account.username, default_version=version,
                sites=normalized_sites, ini_override=ini, extra_directives=normalized_extras,
                enabled_extensions=extensions)


def _replace_settings(account, payload):
    """Replace customer PHP rows in one transaction; caller holds account lock."""
    from sqlalchemy import delete
    with write_session() as session:
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer available for PHP recovery')
        domains = {row.domain: row for row in session.scalars(
            select(Domain).where(Domain.account_id == account.id)).all()}
        if any(site['domain'] not in domains for site in payload['sites']):
            raise ValidationError('A saved PHP site is no longer owned by this account')
        current.php_version = payload['default_version']
        for site in payload['sites']:
            domains[site['domain']].php_version = site['version_override']
        for model in (PhpIniOverride, PhpIniDirective, PhpExtensionSet):
            session.execute(delete(model).where(model.account_id == account.id))
        if payload['ini_override'] is not None:
            session.add(PhpIniOverride(account_id=account.id, **payload['ini_override']))
        for name, value in payload['extra_directives'].items():
            session.add(PhpIniDirective(account_id=account.id, name=name, value=value))
        if payload['enabled_extensions'] is not None:
            session.add(PhpExtensionSet(account_id=account.id, enabled=payload['enabled_extensions']))
        # PhpFunctionOverride is administrator policy and is never changed.


def _refresh_runtime(account, payload):
    from daemon import ols, phpext, sysops
    if payload['enabled_extensions'] is not None:
        phpext._materialize(account.username)
    with write_session() as session:
        current = session.get(Account, account.id)
    ols.refresh_vhost(current)
    sysops.recycle_php_workers(account.username)
    # Keep unused scan directories when reverting to defaults. The refreshed
    # vhost no longer references them; a later explicit selection rebuilds them.
    # Avoid deleting runtime files during recovery or before a possible rollback.


def apply_configuration(account, payload, save_previous):
    """Apply PHP settings with encrypted safety capture and runtime rollback.

    The restore coordinator must hold account and repository locks. Its
    save_previous callback must encrypt the supplied payload and persist the
    recovery snapshot ID before returning. No settings change if it fails.
    """
    selected = validate_for_restore(account, payload)
    previous = validate_for_restore(account, capture(account))
    save_previous(previous)
    try:
        _replace_settings(account, selected)
        _refresh_runtime(account, selected)
    except Exception:
        try:
            _replace_settings(account, previous)
            _refresh_runtime(account, previous)
        except Exception:
            raise ValidationError('PHP recovery failed and runtime rollback could not be confirmed. '
                                  'Use the encrypted previous configuration to recover.') from None
        raise ValidationError('PHP recovery failed; the previous settings were reapplied.') from None


def reconcile_interrupted(row, update):
    """Rebuild runtime from committed settings without replaying a restore.

    Called during startup while holding the account lock. Database replacement
    is transactional; a lost process can leave old runtime with new DB settings.
    The encrypted pre-restore copy stays available for an explicit undo.
    """
    from shared.models import utcnow
    message = 'PHP restore was interrupted before settings were changed.'
    if row.safety_snapshot_id:
        try:
            with write_session() as session:
                account = session.get(Account, row.account_id)
            if account is None:
                raise ValidationError('Account no longer exists')
            current = validate_for_restore(account, capture(account))
            _refresh_runtime(account, current)
            message = ('PHP restore was interrupted. Runtime now matches the committed settings; '
                       'use the encrypted previous configuration to undo this restore.')
        except Exception:
            message = ('PHP restore was interrupted and runtime reconciliation could not be confirmed. '
                       'The encrypted previous configuration is retained for recovery.')
    update(row.id, status='failed', progress_message='Interrupted PHP restore',
           error=message, completed_at=utcnow())
