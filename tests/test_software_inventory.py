from daemon import appunits, nodeapps, pythonapps
from shared.db import write_session
from shared.models import Account, NodeApp, PythonApp


def test_admin_software_inventory_spans_accounts_without_secrets(isolated_db, monkeypatch):
    with write_session() as db:
        first = Account(username="alpha", status="active")
        second = Account(username="bravo", status="active")
        db.add_all([first, second]); db.flush()
        db.add(NodeApp(account_id=first.id, domain="node.example.test", name="web", entry_point="index.js",
                       port=31001, node_version="22", env_vars="encrypted-node-secret"))
        db.add(PythonApp(account_id=second.id, domain="py.example.test", name="api", entry_point="app:app",
                         app_type="asgi", port=31002, env_vars="encrypted-python-secret"))
    monkeypatch.setattr(appunits, "status", lambda unit: {"active": unit.startswith("boron-node-")})
    node = nodeapps.list_all_apps({})["apps"]
    python = pythonapps.list_all_apps({})["apps"]
    assert node[0]["username"] == "alpha" and node[0]["active"] is True
    assert python[0]["username"] == "bravo" and python[0]["active"] is False
    assert "env_vars" not in node[0] and "env_vars" not in python[0]
    assert "secret" not in repr(node + python)
