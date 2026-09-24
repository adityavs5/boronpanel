"""Account suspension immediately gates existing HTTP and root RPC proofs."""
import datetime as dt
import hashlib

import pytest

from api import security
from daemon.rpc_authority import AuthenticationError, resolve_principal
from shared.db import write_session
from shared.models import Account, ApiToken, PanelUser, Session, utcnow
from shared.session_ids import session_digest


@pytest.mark.parametrize('kind', ['session', 'token'])
@pytest.mark.parametrize('status', ['suspended', 'terminated'])
def test_account_state_gates_credentials_and_reactivation(isolated_db, kind, status):
    raw = 'synthetic-account-state-proof'
    with write_session() as db:
        account = Account(username='statecheck')
        db.add(account)
        db.flush()
        account_id = account.id
        if kind == 'session':
            user = PanelUser(username='state-login', password_hash='unused',
                             role='customer', account_id=account.id)
            db.add(user)
            db.flush()
            db.add(Session(session_id=session_digest(raw), panel_user_id=user.id,
                           expires_at=utcnow() + dt.timedelta(hours=1)))
        else:
            db.add(ApiToken(token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                            label='state-test', role='customer', account_id=account.id))

    def http_identity():
        if kind == 'session':
            return security._identity_from_session_cookie(security.sign_session_id(raw))
        return security._identity_from_bearer_token(raw)

    proof = {'type': kind, 'value': raw}
    assert http_identity().account_id == account_id
    assert resolve_principal(proof).account_id == account_id
    with write_session() as db:
        db.get(Account, account_id).status = status
    assert http_identity() is None
    with pytest.raises(AuthenticationError):
        resolve_principal(proof)
    with write_session() as db:
        db.get(Account, account_id).status = 'active'
    assert http_identity().account_id == account_id
    assert resolve_principal(proof).account_id == account_id
