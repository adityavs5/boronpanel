"""Validate routing recovery separately from mailbox passwords and messages."""
from sqlalchemy import select
from shared.db import write_session
from shared.models import Account, MailDomain
from shared.validation import (ValidationError, validate_domain, validate_mailbox_local_part,
                               validate_email_address, validate_iso_date)
from daemon import mail


def _active(value):
    if type(value) is bool or type(value) is int and value in (0, 1):
        return bool(value)
    raise ValidationError('Invalid saved mail-routing status')


def _local(value):
    if not isinstance(value, str):
        raise ValidationError('Invalid saved mail-routing address')
    return validate_mailbox_local_part(value)


def _destination(value):
    if not isinstance(value, str):
        raise ValidationError('Invalid saved mail-routing destination')
    return validate_email_address(value)


def validate_for_restore(account, payload, selected_domains=None):
    """Return routing only; require current domain ownership and responder users.

    The coordinator must verify encrypted snapshot ownership before loading the
    original format-1 mail metadata, which binds the account by username.
    No mailbox passwords, quotas or account statuses are returned or changed.
    """
    if (not isinstance(payload, dict) or type(payload.get('format')) is not int or payload['format'] != 1
            or payload.get('username') != account.username or not isinstance(payload.get('domains'), list)):
        raise ValidationError('Mail routing metadata belongs to another account or format')
    saved = {}
    for entry in payload['domains']:
        if not isinstance(entry, dict) or not isinstance(entry.get('domain'), str):
            raise ValidationError('Invalid saved mail-routing domain')
        name = validate_domain(entry['domain'])
        if name in saved:
            raise ValidationError('Duplicate saved mail-routing domain')
        saved[name] = entry
    if selected_domains is None:
        selected_domains = list(saved)
    if (not isinstance(selected_domains, list) or any(not isinstance(name, str) for name in selected_domains)
            or len(set(selected_domains)) != len(selected_domains) or not set(selected_domains) <= saved.keys()):
        raise ValidationError('Invalid mail-routing domain selection')
    with write_session() as session:
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer available for mail-routing recovery')
        owned = set(session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all())
    result = []
    for name in selected_domains:
        if name not in owned:
            raise ValidationError('A selected mail domain is no longer owned by this account')
        entry = saved[name]
        forwards = entry.get('forwards')
        responders = entry.get('autoresponders')
        if (not isinstance(forwards, list) or not isinstance(responders, list) or 'catchall' not in entry
                or len(forwards) > 100000 or len(responders) > 100000):
            raise ValidationError('Mail-routing recovery settings are incomplete')
        seen = set()
        normalized_forwards = []
        for forward in forwards:
            if not isinstance(forward, dict):
                raise ValidationError('Invalid saved forwarder')
            local = _local(forward.get('source_local_part'))
            destination = _destination(forward.get('destination'))
            key = (local, destination)
            if key in seen:
                raise ValidationError('Duplicate saved forwarder')
            seen.add(key)
            normalized_forwards.append(dict(source_local_part=local, destination=destination, active=_active(forward.get('active'))))
        catchall = entry['catchall']
        if catchall is not None:
            if not isinstance(catchall, dict):
                raise ValidationError('Invalid saved catch-all')
            catchall = dict(destination=_destination(catchall.get('destination')), active=_active(catchall.get('active')))
        existing = {row['local_part'] for row in mail.list_mailboxes(name)} if responders else set()
        normalized_responders = []
        seen = set()
        for responder in responders:
            if not isinstance(responder, dict):
                raise ValidationError('Invalid saved automatic reply')
            local = _local(responder.get('local_part'))
            if local not in existing:
                raise ValidationError('Restore the missing mailbox before restoring its automatic reply')
            if local in seen:
                raise ValidationError('Duplicate saved automatic reply')
            seen.add(local)
            subject, body = responder.get('subject'), responder.get('body')
            if (not isinstance(subject, str) or not subject.strip() or len(subject) > 255
                    or any(character in subject for character in ('\x00', '\r', '\n'))
                    or not isinstance(body, str) or not body or len(body) > 10000 or '\x00' in body):
                raise ValidationError('Invalid saved automatic reply subject or body')
            dates = {}
            for key in ('start_date', 'end_date'):
                if key not in responder:
                    raise ValidationError('Saved automatic reply dates are incomplete')
                dates[key] = validate_iso_date(responder[key]) if responder[key] is not None else None
            if dates['start_date'] and dates['end_date'] and dates['end_date'] < dates['start_date']:
                raise ValidationError('Automatic reply end date precedes its start date')
            normalized_responders.append(dict(local_part=local, subject=subject, body=body,
                                              active=_active(responder.get('active')), **dates))
        result.append(dict(domain=name, forwards=normalized_forwards, catchall=catchall, autoresponders=normalized_responders))
    return dict(format=1, username=account.username, domains=result)


def read_routing(path, account, selected_domains=None):
    """Read a private decrypted mail manifest after snapshot ownership checks."""
    import json
    import os
    import stat
    from pathlib import Path
    path = Path(path)
    if path.resolve() != path:
        raise ValidationError('Invalid private mail-routing metadata path')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size > 8 * 1024 * 1024):
                raise ValidationError('Invalid private mail-routing metadata file')
            payload = json.loads(handle.read(8 * 1024 * 1024 + 1))
    except (OSError, ValueError, UnicodeError):
        raise ValidationError('Could not read private mail-routing metadata') from None
    return validate_for_restore(account, payload, selected_domains)


def _domain_bindings(account):
    from sqlalchemy import text
    with write_session() as session:
        connection = session.connection()
        if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
            session.execute(text('BEGIN'))
        current = session.get(Account, account.id)
        if current is None or current.username != account.username or current.status != 'active':
            raise ValidationError('Account is no longer available for mail-routing capture')
        return [(row.id, row.domain) for row in session.scalars(select(MailDomain).where(
            MailDomain.account_id == account.id).order_by(MailDomain.domain)).all()]


def capture(account, selected_domains=None):
    """Capture routing in one repeatable-read SQL transaction, without passwords.

    Caller must coordinate mail/domain mutations and encrypt this private payload
    before applying recovery. Ownership is checked again after provider reads.
    """
    bindings = _domain_bindings(account)
    if selected_domains is not None:
        if (not isinstance(selected_domains, list) or any(not isinstance(name, str) for name in selected_domains)
                or len(set(selected_domains)) != len(selected_domains)
                or not set(selected_domains) <= {name for _, name in bindings}):
            raise ValidationError('Invalid mail-routing capture selection')
        bindings = [(ident, name) for ident, name in bindings if name in selected_domains]
    domains = []
    if bindings:
        connection = mail._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            connection.begin()
            with connection.cursor() as cursor:
                for _, domain in bindings:
                    cursor.execute('SELECT id FROM mail_domain WHERE domain=%s', (domain,))
                    registered = cursor.fetchone()
                    if registered is None:
                        raise ValidationError('A selected mail domain is not provisioned')
                    ident = registered['id']
                    cursor.execute('SELECT source_local_part,destination,active FROM mail_forward WHERE domain_id=%s ORDER BY source_local_part,destination', (ident,))
                    forwards = cursor.fetchall()
                    cursor.execute('SELECT destination,active FROM mail_catchall WHERE domain_id=%s', (ident,))
                    catchall = cursor.fetchone()
                    cursor.execute('SELECT u.local_part,a.subject,a.body,a.start_date,a.end_date,a.active FROM mail_autoresponder a JOIN mail_user u ON a.mail_user_id=u.id WHERE u.domain_id=%s ORDER BY u.local_part', (ident,))
                    responders = cursor.fetchall()
                    for rule in [*forwards, *responders, *([catchall] if catchall else [])]:
                        rule['active'] = _active(rule['active'])
                    for responder in responders:
                        for key in ('start_date', 'end_date'):
                            if responder[key] is not None:
                                responder[key] = responder[key].isoformat()
                    domains.append(dict(domain=domain, forwards=list(forwards), catchall=catchall, autoresponders=list(responders)))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    after = _domain_bindings(account)
    if selected_domains is not None:
        after = [(ident, name) for ident, name in after if name in selected_domains]
    if after != bindings:
        raise ValidationError('Mail-domain ownership changed during routing capture')
    return dict(format=1, username=account.username, domains=domains)


def _replace_sql(account, payload):
    """Replace selected routing rows atomically; internal coordinator primitive.

    Caller must hold account/mail mutation locks, save encrypted previous state,
    and coordinate Sieve installation before exposing this as a restore action.
    This function does not change mailbox records or install Sieve scripts.
    """
    selected = validate_for_restore(account, payload)
    if not selected['domains']:
        raise ValidationError('Select at least one mail domain for routing recovery')
    bindings = _domain_bindings(account)
    connection = mail._connect()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            resolved = []
            for entry in selected['domains']:
                cursor.execute('SELECT id FROM mail_domain WHERE domain=%s FOR UPDATE', (entry['domain'],))
                row = cursor.fetchone()
                if row is None:
                    raise ValidationError('A selected mail domain is no longer provisioned')
                domain_id = row['id']
                cursor.execute('SELECT id,local_part FROM mail_user WHERE domain_id=%s FOR UPDATE', (domain_id,))
                users = {user['local_part']: user['id'] for user in cursor.fetchall()}
                if any(responder['local_part'] not in users for responder in entry['autoresponders']):
                    raise ValidationError('A required automatic-reply mailbox no longer exists')
                resolved.append((entry, domain_id, users))
            if _domain_bindings(account) != bindings:
                raise ValidationError('Mail-domain ownership changed before routing replacement')
            for entry, domain_id, users in resolved:
                cursor.execute('DELETE FROM mail_forward WHERE domain_id=%s', (domain_id,))
                for forward in entry['forwards']:
                    cursor.execute('INSERT INTO mail_forward (domain_id,source_local_part,destination,active) VALUES (%s,%s,%s,%s)',
                                   (domain_id, forward['source_local_part'], forward['destination'], int(forward['active'])))
                cursor.execute('DELETE FROM mail_catchall WHERE domain_id=%s', (domain_id,))
                if entry['catchall'] is not None:
                    cursor.execute('INSERT INTO mail_catchall (domain_id,destination,active) VALUES (%s,%s,%s)',
                                   (domain_id, entry['catchall']['destination'], int(entry['catchall']['active'])))
                cursor.execute('DELETE a FROM mail_autoresponder a JOIN mail_user u ON a.mail_user_id=u.id WHERE u.domain_id=%s', (domain_id,))
                for responder in entry['autoresponders']:
                    cursor.execute('INSERT INTO mail_autoresponder (mail_user_id,subject,body,start_date,end_date,active) VALUES (%s,%s,%s,%s,%s,%s)',
                                   (users[responder['local_part']], responder['subject'], responder['body'],
                                    responder['start_date'], responder['end_date'], int(responder['active'])))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return [entry['domain'] for entry in selected['domains']]
