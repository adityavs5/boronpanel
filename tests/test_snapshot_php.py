from types import SimpleNamespace

import pytest

from daemon import snapshot_php
from shared.db import write_session
from shared.models import Account, Domain, PhpIniOverride, PhpIniDirective, PhpExtensionSet, PhpFunctionOverride
from shared.validation import ValidationError


@pytest.fixture
def accounts(isolated_db):
    with write_session() as session:
        alpha=Account(username='alpha',status='active',php_version='8.3')
        bravo=Account(username='bravo',status='active',php_version='8.5')
        session.add_all([alpha,bravo]);session.flush()
        session.add_all([
            Domain(account_id=alpha.id,domain='alpha.test',docroot='/home/alpha/public_html',kind='addon'),
            Domain(account_id=alpha.id,domain='app.alpha.test',docroot='/home/alpha/app',kind='subdomain',php_version='8.2'),
            Domain(account_id=bravo.id,domain='bravo.test',docroot='/home/bravo/public_html',kind='addon'),
            PhpIniOverride(account_id=alpha.id,memory_limit='384M',upload_max_filesize='64M',post_max_size='80M',
                           max_execution_time=120,display_errors=False,error_reporting='E_ALL'),
            PhpIniDirective(account_id=alpha.id,name='max_input_vars',value='5000'),
            PhpIniDirective(account_id=bravo.id,name='max_input_vars',value='9000'),
            PhpExtensionSet(account_id=alpha.id,enabled=['curl','mysqli']),
            PhpFunctionOverride(account_id=alpha.id,domain=None,disable_functions='exec,shell_exec'),
            PhpFunctionOverride(account_id=alpha.id,domain='app.alpha.test',disable_functions='exec'),
            PhpFunctionOverride(account_id=bravo.id,domain=None,disable_functions='system'),
        ])
    return alpha,bravo


def test_php_backup_preserves_explicit_settings_and_account_scope(accounts):
    alpha,bravo=accounts
    result=snapshot_php.capture(alpha)
    assert result['default_version']=='8.3'
    assert result['sites']==[{'domain':'alpha.test','version_override':None},{'domain':'app.alpha.test','version_override':'8.2'}]
    assert result['ini_override']['memory_limit']=='384M'
    assert result['extra_directives']=={'max_input_vars':'5000'}
    assert result['enabled_extensions']==['curl','mysqli']
    assert result['administrator_function_policy']==[
        {'domain':None,'disable_functions':'exec,shell_exec'},{'domain':'app.alpha.test','disable_functions':'exec'}]
    assert 'bravo' not in repr(result)


def test_php_backup_distinguishes_defaults_from_empty_extension_selection(accounts):
    _,bravo=accounts
    result=snapshot_php.capture(bravo)
    assert result['ini_override'] is None and result['enabled_extensions'] is None
    with write_session() as session:
        session.add(PhpExtensionSet(account_id=bravo.id,enabled=[]))
    assert snapshot_php.capture(bravo)['enabled_extensions']==[]


def test_php_backup_reloads_current_account_and_rejects_stale_identity(accounts):
    alpha,_=accounts
    with write_session() as session:
        session.get(Account,alpha.id).php_version='8.4'
    assert snapshot_php.capture(alpha)['default_version']=='8.4'
    with pytest.raises(ValidationError,match='no longer'):
        snapshot_php.capture(SimpleNamespace(id=alpha.id,username='bravo'))


def test_php_backup_rejects_cross_account_function_policy(accounts):
    alpha,_=accounts
    with write_session() as session:
        session.add(PhpFunctionOverride(account_id=alpha.id,domain='bravo.test',disable_functions='exec'))
    with pytest.raises(ValidationError,match='unowned'):
        snapshot_php.capture(alpha)


def test_capture_uses_one_database_view_during_concurrent_setting_change(accounts):
    from sqlalchemy import event, select
    from shared import db
    alpha,_=accounts
    changed=False
    def after_account_read(conn,cursor,statement,parameters,context,executemany):
        nonlocal changed
        if changed or not statement.lstrip().startswith('SELECT accounts.'):
            return
        changed=True
        with write_session() as writer:
            writer.get(Account,alpha.id).php_version='8.5'
            row=writer.scalar(select(PhpIniDirective).where(PhpIniDirective.account_id==alpha.id))
            row.value='6000'
    event.listen(db._write_engine,'after_cursor_execute',after_account_read)
    try:
        result=snapshot_php.capture(alpha)
    finally:
        event.remove(db._write_engine,'after_cursor_execute',after_account_read)
    assert changed
    assert result['default_version']=='8.3'
    assert result['extra_directives']=={'max_input_vars':'5000'}
    updated=snapshot_php.capture(alpha)
    assert updated['default_version']=='8.5'
    assert updated['extra_directives']=={'max_input_vars':'6000'}


def test_recovery_validation_preserves_settings_but_excludes_admin_policy(accounts, monkeypatch):
    from daemon import phpext
    monkeypatch.setattr(phpext,'inventory',lambda:{'curl':{},'mysqli':{}})
    alpha,_=accounts
    saved=snapshot_php.capture(alpha)
    validated=snapshot_php.validate_for_restore(alpha,saved)
    assert 'administrator_function_policy' not in validated
    assert validated['ini_override']['memory_limit']=='384M'
    assert validated['extra_directives']=={'max_input_vars':'5000'}
    assert validated['sites'][0]['version_override'] is None
    assert validated['enabled_extensions']==['curl','mysqli']
    assert snapshot_php.capture(alpha)==saved


@pytest.mark.parametrize('field,value',[
    ('username','bravo'),('account_id',True),('format',True),
    ('default_version','99.0'),('default_version',None),('sites',[{'domain':'bravo.test','version_override':'8.3'}]),
    ('sites',[{'domain':'deleted.test','version_override':None}]),
    ('extra_directives',{'extension':'/tmp/evil.so'}),
    ('extra_directives',{'allow_url_fopen':'unrecognized'}),
    ('extra_directives',{'allow_url_fopen':1}),
    ('extra_directives',{'max_file_uploads':True}),
    ('extra_directives',{'max_file_uploads':20.5}),
    ('extra_directives',{'max_file_uploads':[]}),
    ('extra_directives',{'date.timezone':{}}),
    ('enabled_extensions',['missing-extension']),
])
def test_recovery_rejects_foreign_or_unsupported_settings(accounts, monkeypatch, field, value):
    from daemon import phpext
    monkeypatch.setattr(phpext,'inventory',lambda:{'curl':{},'mysqli':{}})
    alpha,_=accounts
    saved=snapshot_php.capture(alpha)
    invalid={**saved,field:value}
    with pytest.raises(ValidationError):
        snapshot_php.validate_for_restore(alpha,invalid)
    assert snapshot_php.capture(alpha)==saved


def test_recovery_rejects_invalid_limits_and_dependencies(accounts, monkeypatch):
    from daemon import phpext
    monkeypatch.setattr(phpext,'inventory',lambda:{'curl':{},'mysqli':{}})
    monkeypatch.setattr(phpext,'EXTENSION_DEPS',{'mysqli':('mysqlnd',)})
    alpha,_=accounts
    saved=snapshot_php.capture(alpha)
    with pytest.raises(ValidationError,match='dependency'):
        snapshot_php.validate_for_restore(alpha,saved)
    saved['ini_override']['post_max_size']='1M'
    with pytest.raises(ValidationError,match='post_max_size'):
        snapshot_php.validate_for_restore(alpha,saved)
