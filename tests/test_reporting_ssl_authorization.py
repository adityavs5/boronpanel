import inspect
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
import pytest

from api.routers import bandwidth, sitestats, ssl_router, usage
from api.security import Identity
from daemon.rpc_authority import authorize, AuthorizationError, Principal
from shared.db import write_session
from shared.models import Account, Domain


@pytest.fixture
def owners(isolated_db):
    with write_session() as db:
        a, b = Account(username='reporta'), Account(username='reportb')
        db.add_all([a, b]); db.flush()
        db.add_all([Domain(account_id=a.id, domain='a.example', docroot='/home/reporta/public_html'),
                    Domain(account_id=b.id, domain='b.example', docroot='/home/reportb/public_html')])
        return a.id, b.id


ROUTES = []
for module in (bandwidth, sitestats, ssl_router, usage):
    for router in vars(module).values():
        if isinstance(router, APIRouter):
            ROUTES.extend((module, route.endpoint) for route in router.routes)


@pytest.mark.parametrize('module,endpoint', ROUTES, ids=[f.__name__ for _, f in ROUTES])
def test_foreign_reporting_ssl_denies_before_rpc(owners, monkeypatch, module, endpoint):
    monkeypatch.setattr(module, 'call_daemon', lambda *args, **kwargs: pytest.fail('Foreign resource reached RPC'))
    values = dict(username='reportb', domain='b.example', period='daily', request=None,
                  body=SimpleNamespace(domain='b.example', force=False, license_key='synthetic'),
                  identity=Identity(1, 'reporta-login', 'customer', owners[0], 'session'))
    with pytest.raises(HTTPException) as exc:
        endpoint(**{name: values.get(name) for name in inspect.signature(endpoint).parameters})
    assert exc.value.status_code == 403


@pytest.mark.parametrize('op', ['ssl.issue', 'ssl.issue_wildcard', 'ssl.status', 'sitestats.get'])
def test_root_ssl_stats_scope(owners, op):
    owner = Principal('customer', 'reporta-login', owners[0], 1, 'session')
    authorize(op, {'domain': 'a.example'}, owner)
    with pytest.raises(AuthorizationError):
        authorize(op, {'domain': 'b.example'}, owner)
    with pytest.raises(PermissionError):
        authorize(op, {'domain': 'a.example'}, None)
