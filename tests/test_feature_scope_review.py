"""Reviewed Git, custom-page, notes and onboarding authorization surfaces."""
import inspect

import pytest
from fastapi import HTTPException

from api.routers import errorpages, git, notes, onboarding
from api.security import Identity
from daemon.rpc_authority import AuthorizationError, Principal, authorize
from shared.db import write_session
from shared.models import Account, Domain


@pytest.fixture
def owners(isolated_db):
    with write_session() as db:
        a, b = Account(username='scopea'), Account(username='scopeb')
        db.add_all([a, b])
        db.flush()
        db.add_all([Domain(account_id=a.id, domain='a.example', docroot='/home/scopea/public_html'),
                    Domain(account_id=b.id, domain='b.example', docroot='/home/scopeb/public_html')])
        return a.id, b.id


ROUTES = [(module, route.endpoint) for module in (git, errorpages, notes, onboarding)
          for router in (module.api_router, getattr(module, 'ui_router', None)) if router
          for route in router.routes]


@pytest.mark.parametrize('module,endpoint', ROUTES, ids=[f.__name__ for _, f in ROUTES])
def test_foreign_account_route_denies_before_rpc(owners, monkeypatch, module, endpoint):
    def forbidden(*args, **kwargs):
        pytest.fail('unauthorized route reached privileged RPC')
    monkeypatch.setattr(module, 'call_daemon', forbidden)
    values = dict(username='scopeb', domain='b.example', code=404, name='repo',
                  request=None, body=None, deploy_target='public_html',
                  identity=Identity(1, 'scopea-login', 'customer', owners[0], 'session'))
    args = {name: values[name] for name in inspect.signature(endpoint).parameters}
    with pytest.raises(HTTPException) as exc:
        endpoint(**args)
    assert exc.value.status_code == 403


OPS = ['git.repo.create', 'git.repo.delete', 'git.repo.list', 'git.repo.push_log',
       'git.repo.set_deploy_target', 'onboarding.get', 'onboarding.set',
       'errorpages.list', 'errorpages.get', 'errorpages.set', 'errorpages.delete']


@pytest.mark.parametrize('op', OPS)
def test_root_policy_allows_owner_denies_peer_and_anonymous(owners, op):
    principal = Principal('customer', 'scopea-login', owners[0], 1, 'session')
    own = {'domain': 'a.example'} if op.startswith('errorpages.') else {'username': 'scopea'}
    peer = {'domain': 'b.example'} if op.startswith('errorpages.') else {'username': 'scopeb'}
    authorize(op, own, principal)
    with pytest.raises(AuthorizationError):
        authorize(op, peer, principal)
    with pytest.raises(PermissionError):
        authorize(op, own, None)


@pytest.mark.parametrize('op', ['notes.add', 'notes.list'])
def test_notes_remain_administrator_only(owners, op):
    for role in ('customer', 'reseller'):
        with pytest.raises(AuthorizationError):
            authorize(op, {'username': 'scopea'}, Principal(role, 'caller', owners[0], 1, 'session'))
    authorize(op, {'username': 'scopea'}, Principal('admin', 'administrator', None, 1, 'session'))
