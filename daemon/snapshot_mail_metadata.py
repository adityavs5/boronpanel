"""Private virtual-mail recovery metadata, separate from customer-facing APIs."""
import re
import json
import os
from pathlib import Path
import stat
from daemon import mail
from daemon.database_operations import serialized_worker
from shared.validation import ValidationError, validate_domain, validate_mailbox_local_part, validate_username


def validate_mailbox(entry):
    if not isinstance(entry, dict):
        raise ValidationError('Invalid mailbox recovery metadata')
    local = validate_mailbox_local_part(entry.get('local_part'))
    password = entry.get('password_hash')
    if not isinstance(password, str) or not re.fullmatch(r'\{(?:ARGON2ID|ARGON2I|SHA512-CRYPT|SHA256-CRYPT|BLF-CRYPT)\}[^\x00-\x20\x7f]{1,230}', password):
        raise ValidationError('Unsupported mailbox authentication hash in recovery metadata')
    quota = entry.get('quota_mb')
    if isinstance(quota, bool) or not isinstance(quota, int) or not 1 <= quota <= 102400:
        raise ValidationError('Invalid mailbox recovery quota')
    if type(entry.get('active')) is not bool:
        raise ValidationError('Invalid mailbox recovery status')
    return dict(local_part=local, password_hash=password, quota_mb=quota, active=entry['active'])


def read_mailboxes(path, username):
    """Read validated mailbox credentials privately; routing rules are not returned.

    Account ownership must be checked against the snapshot before decrypting.
    The caller must not serialize this result into a public response.
    """
    validate_username(username)
    path = Path(path)
    if path.resolve() != path:
        raise ValidationError('Invalid private mail recovery metadata path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 8 * 1024 * 1024:
            raise ValidationError('Invalid private mail recovery metadata file')
        try:
            payload = json.loads(handle.read(8 * 1024 * 1024 + 1))
            if type(payload['format']) is not int or payload['format'] != 1 or payload['username'] != username:
                raise ValueError()
            if not isinstance(payload['domains'], list):
                raise ValueError()
            result = {}
            for entry in payload['domains']:
                domain = validate_domain(entry['domain'])
                if domain in result or type(entry['active']) is not bool or not isinstance(entry['mailboxes'], list):
                    raise ValueError()
                mailboxes = {}
                for saved in entry['mailboxes']:
                    mailbox = validate_mailbox(saved)
                    if mailbox['local_part'] in mailboxes:
                        raise ValueError()
                    mailboxes[mailbox['local_part']] = mailbox
                result[domain] = {'active': entry['active'], 'mailboxes': mailboxes}
            return result
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise ValidationError('Invalid private mail recovery metadata') from None


@serialized_worker
def capture(username, domains):
    """Caller supplies only current MailDomain registrations owned by this account.

    One SQL transaction captures domain/mailbox/routing records consistently.
    Database IDs are replaced with stable domain and mailbox names. Password
    hashes stay in this private payload; ordinary mail listing APIs are unchanged.
    """
    validate_username(username)
    if not domains:
        return {'format':1, 'username':username, 'domains':[]}
    result = []
    connection = mail._connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        connection.begin()
        with connection.cursor() as cursor:
            for registered in domains:
                domain = validate_domain(registered.domain)
                cursor.execute('SELECT id,active FROM mail_domain WHERE domain=%s', (domain,))
                row = cursor.fetchone()
                if row is None:
                    raise ValidationError('Registered mail domain is missing: ' + domain)
                domain_id = row['id']
                cursor.execute('SELECT id,local_part,password,quota_mb,active FROM mail_user WHERE domain_id=%s ORDER BY local_part', (domain_id,))
                users = cursor.fetchall()
                mailboxes = [validate_mailbox(dict(local_part=user['local_part'],password_hash=user['password'],quota_mb=user['quota_mb'],active=bool(user['active']))) for user in users]
                cursor.execute('SELECT source_local_part,destination,active FROM mail_forward WHERE domain_id=%s ORDER BY source_local_part,destination', (domain_id,))
                forwards = cursor.fetchall()
                cursor.execute('SELECT destination,active FROM mail_catchall WHERE domain_id=%s', (domain_id,))
                catchall = cursor.fetchone()
                cursor.execute('SELECT u.local_part,a.subject,a.body,a.start_date,a.end_date,a.active FROM mail_autoresponder a JOIN mail_user u ON a.mail_user_id=u.id WHERE u.domain_id=%s ORDER BY u.local_part', (domain_id,))
                responders = cursor.fetchall()
                for responder in responders:
                    for key in ('start_date','end_date'):
                        if responder[key] is not None:
                            responder[key] = responder[key].isoformat()
                result.append(dict(domain=domain,active=bool(row['active']),mailboxes=mailboxes,
                                   forwards=forwards,catchall=catchall,autoresponders=responders))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {'format':1, 'username':username, 'domains':result}


@serialized_worker
def recreate_mailbox(domain, entry):
    """Recreate a missing SQL mailbox only; coordinator must verify domain ownership.

    Existing mailbox uniqueness is enforced by SQL and never bypassed by an
    upsert. This does not create message directories, copy message data, activate
    forwarding or modify panel cache rows; those belong to restore coordination.
    """
    domain = validate_domain(domain)
    entry = validate_mailbox(entry)
    connection = mail._connect()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute('SELECT id FROM mail_domain WHERE domain=%s FOR UPDATE', (domain,))
            owner = cursor.fetchone()
            if owner is None:
                raise ValidationError('Mail domain is missing; restore its owned registration first')
            cursor.execute('SELECT id FROM mail_user WHERE domain_id=%s AND local_part=%s', (owner['id'],entry['local_part']))
            if cursor.fetchone():
                raise ValidationError('Mailbox already exists; its credentials were retained')
            cursor.execute('INSERT INTO mail_user (domain_id,local_part,password,quota_mb,active) VALUES (%s,%s,%s,%s,%s)',
                           (owner['id'],entry['local_part'],entry['password_hash'],entry['quota_mb'],int(entry['active'])))
            ident = cursor.lastrowid
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {'id':ident,'domain':domain,'local_part':entry['local_part'],'quota_mb':entry['quota_mb'],'active':entry['active']}
