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
