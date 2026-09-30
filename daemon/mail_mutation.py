"""Exclude ordinary mail edits while a persisted routing restore needs recovery."""
from functools import wraps
import inspect

from sqlalchemy import select

from daemon import database_operations
from shared.db import write_session
from shared.models import Account, Domain, MailDomain, SnapshotRestore
from shared.validation import ValidationError, validate_domain, validate_username


def require_accounts_available(session, account_ids):
    if not account_ids:
        return
    rows = session.scalars(select(SnapshotRestore).where(
        SnapshotRestore.account_id.in_(account_ids),
        SnapshotRestore.selection['kind'].as_string() == 'mail_routing')).all()
    for row in rows:
        if row.selection.get('kind') != 'mail_routing':
            continue
        unresolved = (row.summary.get('routing_finalized') is not True
                      and (row.safety_snapshot_id or row.summary.get('routing_safety_snapshots') or row.status == 'completed'))
        if row.status in ('pending', 'running') or unresolved:
            raise ValidationError('A mail-routing restore is pending or needs recovery. Finish its recovery before changing mail settings.')


def _owners(session, target):
    if isinstance(target, Account):
        return {target.id}
    params = target if isinstance(target, dict) else {}
    domain = params.get('domain') if params else target
    owners = set()
    if isinstance(domain, str):
        domain = validate_domain(domain)
        for model in (MailDomain, Domain):
            owners.update(value for value in session.scalars(select(model.account_id).where(model.domain == domain)).all()
                          if value is not None)
    username = params.get('username')
    if isinstance(username, str):
        value = session.scalar(select(Account.id).where(Account.username == validate_username(username)))
        if value is not None:
            owners.add(value)
    return owners


def serialized(function):
    """Serialize edits and check persisted ownership before the first side effect.

    Internal routing recovery uses its separately authorized SQL/Sieve primitives,
    never a thread-local or user-supplied bypass of this ordinary-edit guard.
    """
    signature = inspect.signature(function)
    first = next(iter(signature.parameters))
    @wraps(function)
    @database_operations.serialized
    def wrapped(*args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        target = bound.arguments.get(first)
        with write_session() as session:
            if isinstance(target, dict) and target.get('username') and target.get('domain'):
                require_domain_owner(session, target['username'], target['domain'])
            require_accounts_available(session, _owners(session, target))
        return function(*args, **kwargs)
    return wrapped


def require_domain_owner(session, username, domain, *, require_mail=False):
    """Internal callers must retain the intended destination account identity."""
    account = session.scalar(select(Account).where(Account.username == validate_username(username)))
    if account is None:
        raise ValidationError('Mail destination account does not exist')
    domain = validate_domain(domain)
    mail_row = None
    for model in (Domain, MailDomain):
        row = session.scalar(select(model).where(model.domain == domain))
        if row is not None and row.account_id != account.id:
            raise ValidationError('Imported mail domain belongs to another account')
        if model is MailDomain:
            mail_row = row
    if require_mail and mail_row is None:
        raise ValidationError('Owned mail domain provisioning must finish before restoring mail')
    require_accounts_available(session, {account.id})
    return account.id
