"""Coordinate direct account settings changes with backup and recovery workers."""
from functools import wraps

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account
from shared.validation import ValidationError, validate_username


def locked(function):
    @wraps(function)
    def wrapped(params):
        from daemon.snapshot_jobs import lock
        username = validate_username(params['username'])
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                raise RuntimeError(f"account '{username}' not found")
            account_id = account.id
        try:
            with lock(f'account-{account_id}', blocking=False):
                # Recheck identity after acquisition rather than applying to a
                # newly created account that reused the original username.
                with write_session() as session:
                    current = session.get(Account, account_id)
                    if current is None or current.username != username:
                        raise ValidationError('Account changed while waiting to update settings')
                return function(params)
        except BlockingIOError:
            raise ValidationError('An account backup, restore or settings change is in progress. Try again shortly.') from None
    return wrapped
