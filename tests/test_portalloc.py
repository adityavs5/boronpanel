from daemon import portalloc
from shared.db import write_session
from shared.models import Account, Domain, NodeApp, PythonApp


def _make_account_and_domain(session, username, domain):
    account = Account(username=username, uid=5001, gid=5001, status="active")
    session.add(account)
    session.flush()
    session.add(Domain(account_id=account.id, domain=domain, kind="primary", docroot=f"/home/{username}/public_html"))
    session.flush()
    return account


def test_allocate_port_starts_at_range_start(isolated_db):
    with write_session() as session:
        port = portalloc.allocate_port(session)
    assert port == portalloc.settings.app_port_range_start


def test_allocate_port_skips_ports_used_by_node_apps(isolated_db):
    with write_session() as session:
        account = _make_account_and_domain(session, "demo1", "demo1.example")
        session.add(NodeApp(
            account_id=account.id, domain="demo1.example", name="app1", entry_point="a.js",
            port=portalloc.settings.app_port_range_start, node_version="20", env_vars="",
        ))

    with write_session() as session:
        port = portalloc.allocate_port(session)
    assert port == portalloc.settings.app_port_range_start + 1


def test_allocate_port_skips_ports_used_by_python_apps(isolated_db):
    with write_session() as session:
        account = _make_account_and_domain(session, "demo1", "demo1.example")
        session.add(PythonApp(
            account_id=account.id, domain="demo1.example", name="app1", entry_point="app:app",
            app_type="asgi", port=portalloc.settings.app_port_range_start, env_vars="",
        ))

    with write_session() as session:
        port = portalloc.allocate_port(session)
    assert port == portalloc.settings.app_port_range_start + 1


def test_allocate_port_raises_when_range_exhausted(isolated_db, monkeypatch):
    monkeypatch.setattr(portalloc.settings, "app_port_range_start", 40000)
    monkeypatch.setattr(portalloc.settings, "app_port_range_end", 40000)
    with write_session() as session:
        account = _make_account_and_domain(session, "demo1", "demo1.example")
        session.add(NodeApp(
            account_id=account.id, domain="demo1.example", name="app1", entry_point="a.js",
            port=40000, node_version="20", env_vars="",
        ))

    with write_session() as session:
        import pytest
        with pytest.raises(portalloc.PortAllocationError):
            portalloc.allocate_port(session)
