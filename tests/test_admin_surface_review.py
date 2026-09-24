"""Reviewed administrator-only route families must reject customer/reseller calls."""
import asyncio
import importlib
import inspect

from fastapi import APIRouter, HTTPException
import pytest

from api.security import Identity

MODULES = ['services', 'firewall', 'fail2ban', 'ipban', 'ipwhitelist',
           'slowquery', 'dbmonitor', 'auditlog', 'adminlogs', 'health',
           'plans', 'site_templates', 'webhooks', 'mailqueue', 'waf',
           'olsadmin', 'ipmanager', 'monitoring']
CASES = []
for name in MODULES:
    module = importlib.import_module('api.routers.' + name)
    seen = set()
    for router in vars(module).values():
        if not isinstance(router, APIRouter):
            continue
        for route in router.routes:
            if route.endpoint not in seen:
                seen.add(route.endpoint)
                CASES.append((module, route.endpoint))


@pytest.mark.parametrize('role', ['customer', 'reseller'])
@pytest.mark.parametrize('module,endpoint', CASES, ids=[m.__name__ + '.' + f.__name__ for m, f in CASES])
def test_admin_route_denies_before_side_effects(monkeypatch, role, module, endpoint):
    def forbidden(*args, **kwargs):
        pytest.fail('Non-administrator reached privileged data or RPC')
    for attr in ('call_daemon', 'read_session', '_query_rows'):
        if hasattr(module, attr):
            monkeypatch.setattr(module, attr, forbidden)
    params = {name: None for name in inspect.signature(endpoint).parameters}
    params['identity'] = Identity(1, 'customer-login', role, 1, 'session')
    with pytest.raises(HTTPException) as exc:
        result = endpoint(**params)
        if inspect.isawaitable(result):
            asyncio.run(result)
    assert exc.value.status_code == 403
