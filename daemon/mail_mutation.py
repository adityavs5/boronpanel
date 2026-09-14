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
            require_accounts_available(session, _owners(session, target))
        return function(*args, **kwargs)
    return wrapped
