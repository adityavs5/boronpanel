import pytest

from daemon import handlers_maintenance as hm
from shared.validation import ValidationError


@pytest.fixture()
def domain_row(isolated_db, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, Domain

    monkeypatch.setattr(hm.ols, "refresh_vhost", lambda account: None)
    monkeypatch.setattr(hm.custom_pages, "set_maintenance_page", lambda username, domain, content: None)
    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        row = Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html")
        session.add(row)
        session.flush()
    return row


def test_get_maintenance_defaults_disabled(domain_row):
    result = hm.get_maintenance({"domain": "demo1.example"})
    assert result["enabled"] is False
    assert result["bypass_token"] is None


def test_set_maintenance_enables_and_generates_token(domain_row):
    result = hm.set_maintenance({"domain": "demo1.example", "enabled": True, "title": "Down for maintenance"})
    assert result["enabled"] is True
    assert result["title"] == "Down for maintenance"
    assert result["bypass_token"]
    assert len(result["bypass_token"]) > 20


def test_set_maintenance_calls_refresh_vhost(domain_row, monkeypatch):
    calls = []
    monkeypatch.setattr(hm.ols, "refresh_vhost", lambda account: calls.append(account.username))
    hm.set_maintenance({"domain": "demo1.example", "enabled": True})
    assert calls == ["demo1"]


def test_set_maintenance_auto_disable_minutes_computes_deadline(domain_row):
    result = hm.set_maintenance({"domain": "demo1.example", "enabled": True, "auto_disable_minutes": 60})
    assert result["auto_disable_minutes"] == 60
    assert result["auto_disable_at"] is not None


def test_set_maintenance_manual_mode_has_no_deadline(domain_row):
    result = hm.set_maintenance({"domain": "demo1.example", "enabled": True, "auto_disable_minutes": None})
    assert result["auto_disable_at"] is None


def test_set_maintenance_rejects_bad_auto_disable(domain_row):
    with pytest.raises(ValidationError):
        hm.set_maintenance({"domain": "demo1.example", "enabled": True, "auto_disable_minutes": 45})


def test_bypass_token_persists_across_updates(domain_row):
    first = hm.set_maintenance({"domain": "demo1.example", "enabled": True})
    second = hm.set_maintenance({"domain": "demo1.example", "enabled": True, "title": "Still down"})
    assert first["bypass_token"] == second["bypass_token"]


def test_regenerate_token_changes_it(domain_row):
    first = hm.set_maintenance({"domain": "demo1.example", "enabled": True})
    second = hm.set_maintenance({"domain": "demo1.example", "enabled": True, "regenerate_token": True})
    assert first["bypass_token"] != second["bypass_token"]


def test_list_active_maintenance_only_shows_enabled(domain_row, isolated_db):
    from shared.db import write_session
    from shared.models import Account, Domain

    with write_session() as session:
        account2 = Account(username="demo2", uid=5002, gid=5002, status="active")
        session.add(account2)
        session.flush()
        session.add(Domain(account_id=account2.id, domain="demo2.example", kind="primary", docroot="/home/demo2/public_html"))

    hm.set_maintenance({"domain": "demo1.example", "enabled": True})
    hm.set_maintenance({"domain": "demo2.example", "enabled": False})

    result = hm.list_active_maintenance({})
    domains = [d["domain"] for d in result["domains"]]
    assert domains == ["demo1.example"]
    assert result["domains"][0]["username"] == "demo1"


def test_get_maintenance_rejects_unknown_domain(isolated_db):
    with pytest.raises(RuntimeError):
        hm.get_maintenance({"domain": "never-provisioned.example"})


def test_sweep_expired_disables_past_deadline(domain_row, monkeypatch):
    import datetime as dt

    from shared.db import write_session
    from shared.models import MaintenanceMode, utcnow

    hm.set_maintenance({"domain": "demo1.example", "enabled": True, "auto_disable_minutes": 60})
    with write_session() as session:
        row = session.query(MaintenanceMode).filter_by(domain="demo1.example").one()
        row.auto_disable_at = utcnow() - dt.timedelta(minutes=1)

    calls = []
    monkeypatch.setattr(hm.ols, "refresh_vhost", lambda account: calls.append(account.username))
    result = hm.sweep_expired()
    assert result["disabled"] == ["demo1.example"]
    assert calls == ["demo1"]
    assert hm.get_maintenance({"domain": "demo1.example"})["enabled"] is False


def test_sweep_expired_leaves_manual_mode_alone(domain_row, monkeypatch):
    hm.set_maintenance({"domain": "demo1.example", "enabled": True, "auto_disable_minutes": None})
    monkeypatch.setattr(hm.ols, "refresh_vhost", lambda account: None)
    result = hm.sweep_expired()
    assert result["disabled"] == []
    assert hm.get_maintenance({"domain": "demo1.example"})["enabled"] is True


def test_backup_quiesce_restores_customer_maintenance_settings(domain_row):
    from shared.db import write_session
    from shared.models import Account
    hm.set_maintenance({'domain':'demo1.example','enabled':False,'title':'Customer title',
        'message':'Customer message','estimated_time':'Tomorrow','auto_disable_minutes':60})
    with write_session() as session:account=session.query(Account).filter_by(username='demo1').one()
    states=hm.quiesce_account(account)
    active=hm.get_maintenance({'domain':'demo1.example'})
    assert active['enabled'] is True and 'consistent backup' in active['message']
    hm.restore_quiesced_account(account,states)
    restored=hm.get_maintenance({'domain':'demo1.example'})
    assert restored['enabled'] is False
    assert (restored['title'],restored['message'],restored['estimated_time'],restored['auto_disable_minutes']) == (
        'Customer title','Customer message','Tomorrow',60)


def test_backup_quiesce_does_not_change_existing_maintenance(domain_row):
    from shared.db import write_session
    from shared.models import Account
    hm.set_maintenance({'domain':'demo1.example','enabled':True,'title':'Planned maintenance'})
    with write_session() as session:account=session.query(Account).filter_by(username='demo1').one()
    assert hm.quiesce_account(account)==[]
    assert hm.get_maintenance({'domain':'demo1.example'})['title']=='Planned maintenance'
