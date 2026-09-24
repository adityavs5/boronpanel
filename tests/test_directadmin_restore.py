from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from daemon import directadmin_restore as restore
from daemon import cpanel_import as ci


def test_mail_restores_password_hash_without_reporting_it(tmp_path, monkeypatch):
    hashed='$6$salt$'+'a'*86
    entry={'domain':'example.com','local_part':'sales','password_hash':'{CRYPT}'+hashed,'quota_mb':500}
    monkeypatch.setattr(restore.handlers_mail,'create_mailbox',lambda p: None)
    conn=MagicMock(); cursor=conn.cursor.return_value.__enter__.return_value;cursor.rowcount=1
    monkeypatch.setattr(restore.mail,'_connect',lambda:conn)
    monkeypatch.setattr(restore.mail,'_domain_id',lambda domain:17)
    result=restore.restore_mail(tmp_path,entry)
    assert hashed not in result
    assert cursor.execute.call_args.args[1]==('{CRYPT}'+hashed,17,'sales')


@pytest.mark.parametrize('enabled',[False,True])
def test_app_recreated_through_user_handlers_and_source_state_preserved(tmp_path,monkeypatch,enabled):
    app=tmp_path/'homedir/apps/site';app.mkdir(parents=True)
    (app/'app.js').write_text('test')
    calls=[]
    monkeypatch.setattr(restore.nodeapps,'create_app',lambda params: calls.append(('create',params)) or {'id':8})
    monkeypatch.setattr(restore.nodeapps,'npm_install',lambda params:calls.append(('install',params)))
    monkeypatch.setattr(restore.nodeapps,'start_app',lambda params:calls.append(('start',params)))
    monkeypatch.setattr(restore.nodeapps,'get_app',lambda params:{'active':'active'})
    monkeypatch.setattr(ci,'_copy_homedir',lambda *args:calls.append(('copy',{})))
    result=restore.restore_app(tmp_path,'alice',{'index':1,'kind':'node','domain':'example.com','root':'apps/site','startup':'app.js','version':'22','env':{'MODE':'production'},'enabled':enabled},{})
    assert [kind for kind,_ in calls]==(['create','copy','install','start'] if enabled else ['create','copy','install'])
    assert calls[0][1]['node_version']=='22'
    assert 'recreated' in result


def test_dependency_errors_cannot_leak_registry_credentials(tmp_path,monkeypatch):
    monkeypatch.setattr(restore.nodeapps,'create_app',lambda params:(_ for _ in ()).throw(RuntimeError('secret-token')))
    with pytest.raises(Exception) as error:
        restore.restore_app(tmp_path,'alice',{'index':1,'kind':'node','domain':'example.com','root':'apps/site','startup':'app.js','version':'22'}, {})
    assert 'secret-token' not in str(error.value)


def test_node_database_mapping_changes_only_destination_copy(tmp_path,monkeypatch):
    app=tmp_path/'homedir/nodeapps/backend';app.mkdir(parents=True)
    original='DB_HOST=localhost\nDB_NAME=alice_shop\nDB_USER=olduser\nDB_PASSWORD=old-secret\nOTHER=keep\n'
    (app/'.env').write_text(original)
    created=[];copied=[]
    monkeypatch.setattr(restore.nodeapps,'create_app',lambda p:created.append(p) or {'id':9})
    monkeypatch.setattr(restore.nodeapps,'npm_install',lambda p:None)
    def copy(root,user): copied.append((root/'homedir/nodeapps/migrated1/.env').read_text())
    monkeypatch.setattr(ci,'_copy_homedir',copy)
    entry={'index':1,'kind':'node','domain':'example.com','root':'nodeapps/backend','startup':'server.js','version':'20','enabled':False,'database_name':'alice_shop'}
    result=restore.restore_app(tmp_path,'alice',entry,{'alice_shop':'alice_shop'}, {'alice_shop':('alice_shop','new-secret')})
    assert created[0]['env_vars']['DB_PASSWORD']=='new-secret'
    assert 'old-secret' not in copied[0] and 'new-secret' in copied[0] and 'OTHER=keep' in copied[0]
    assert (app/'.env').read_text()==original
    assert 'new-secret' not in result
    with pytest.raises(Exception,match='database restore did not complete'):
        restore.restore_app(tmp_path,'alice',entry,{}, {})
