import pytest
from daemon import cpanel_import as ci
from shared.db import write_session
from shared.models import Account, Domain


def test_import_reuses_primary_created_with_account(isolated_db, monkeypatch):
    with write_session() as db:
        a = Account(username='newimport', primary_domain='example.com')
        db.add(a)
        db.flush()
        db.add(Domain(account_id=a.id, domain='example.com', kind='primary', docroot='/unused'))
    monkeypatch.setattr(ci.handlers_domain, 'add_domain', lambda p: pytest.fail('primary must not be added twice'))
    assert 'already provisioned' in ci._add_domain_step('newimport', {'domain': 'example.com', 'kind': 'primary'})


@pytest.mark.parametrize('case', ['other_owner', 'wrong_kind', 'wrong_primary', 'missing'])
def test_unexpected_domains_still_use_normal_conflict_validation(isolated_db, monkeypatch, case):
    with write_session() as db:
        a = Account(username='newimport', primary_domain='other.example.com' if case == 'wrong_primary' else 'example.com')
        other = Account(username='existing')
        db.add_all([a, other])
        db.flush()
        if case != 'missing':
            db.add(Domain(account_id=other.id if case == 'other_owner' else a.id,
                          domain='example.com', kind='addon' if case == 'wrong_kind' else 'primary', docroot='/unused'))
    calls = []
    def validate(params):
        calls.append(params)
        raise RuntimeError('normal ownership validation')
    monkeypatch.setattr(ci.handlers_domain, 'add_domain', validate)
    with pytest.raises(RuntimeError, match='normal ownership validation'):
        ci._add_domain_step('newimport', {'domain': 'example.com', 'kind': 'primary'})
    assert calls == [{'username': 'newimport', 'domain': 'example.com', 'kind': 'primary'}]
