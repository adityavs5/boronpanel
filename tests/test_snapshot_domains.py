from pathlib import Path

import pytest
from sqlalchemy import select

from daemon import snapshot_domains
from shared.config import settings
from shared.db import write_session
from shared.models import Account,Domain


@pytest.fixture
def account_domains(isolated_db,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'home_base',str(tmp_path/'home'))
    home=tmp_path/'home'/'alpha';(home/'domains'/'example.test'/'public_html').mkdir(parents=True)
    (home/'domains'/'blog.example.test'/'public_html').mkdir(parents=True)
    with write_session() as session:
        account=Account(username='alpha',status='active',uid=2000,gid=2000,php_version='8.3')
        session.add(account);session.flush()
        session.add_all([
            Domain(account_id=account.id,domain='example.test',kind='primary',docroot=str(home/'domains'/'example.test'/'public_html')),
            Domain(account_id=account.id,domain='blog.example.test',kind='subdomain',docroot=str(home/'domains'/'blog.example.test'/'public_html')),
        ]);session.flush();session.expunge(account)
    return account,home


def test_domain_metadata_capture_validate_and_apply(account_domains,monkeypatch):
    account,home=account_domains;refresh=[]
    monkeypatch.setattr(snapshot_domains.ols,'refresh_vhost',lambda row:refresh.append(row.username))
    saved=snapshot_domains.capture(account)
    saved['domains'][0]['suspended']=True;saved['domains'][0]['suspension_reason']='Maintenance'
    saved['domains'][0]['php_version']='8.2'
    safety=[]
    result=snapshot_domains.apply_configuration(account,saved,safety.append)
    assert result=={'domains':2} and len(safety)==1 and refresh==['alpha']
    with write_session() as session:
        row=session.scalar(select(Domain).where(Domain.domain==saved['domains'][0]['domain']))
        assert row.suspended is True and row.suspension_reason=='Maintenance' and row.php_version=='8.2'


def test_domain_metadata_rolls_back_if_ols_rejects(account_domains,monkeypatch):
    account,_=account_domains;saved=snapshot_domains.capture(account)
    saved['domains'][0]['suspended']=True
    calls=[]
    def fail_once(_account):
        calls.append(1)
        if len(calls)==1:raise RuntimeError('invalid OLS configuration')
    monkeypatch.setattr(snapshot_domains.ols,'refresh_vhost',fail_once)
    with pytest.raises(RuntimeError,match='invalid OLS'):
        snapshot_domains.apply_configuration(account,saved,lambda _previous:None)
    with write_session() as session:
        assert all(row.suspended is False for row in session.scalars(select(Domain)).all())
    assert len(calls)==2


def test_domain_metadata_rejects_missing_or_escaped_domain(account_domains):
    account,home=account_domains;saved=snapshot_domains.capture(account)
    saved['domains'][0]['docroot']='/etc'
    with pytest.raises(Exception,match='unsafe document root'):
        snapshot_domains.validate_for_restore(account,saved)
    saved=snapshot_domains.capture(account);saved['domains'].append({
        'domain':'missing.example.test','kind':'addon','docroot':str(home/'missing'),
        'php_version':None,'suspended':False,'suspension_reason':None})
    with pytest.raises(Exception,match='Recreate domain'):
        snapshot_domains.validate_for_restore(account,saved)
