from daemon import backup_notifications as notifications
from shared.db import write_session
from shared.models import BackupNotificationDelivery


def test_telegram_configuration_encrypts_token(isolated_db):
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd'
    result=notifications.save_telegram({'enabled':True,'chat_id':'-1001234567890','token':token,
        'events':['backup.completed','backup.failed']})
    telegram=next(item for item in result['plugins'] if item['kind']=='telegram')
    assert telegram['enabled'] is True and telegram['configured'] is True
    assert token not in str(result)
    assert token.encode() not in isolated_db.read_bytes()


def test_telegram_delivery_records_result(isolated_db,monkeypatch):
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd'
    notifications.save_telegram({'enabled':True,'chat_id':'12345','token':token,
        'events':['backup.completed']})
    sent=[]
    monkeypatch.setattr(notifications,'_send',lambda actual,chat,text:sent.append((actual,chat,text)))
    account=type('Account',(),{'username':'alpha'})()
    assert notifications.send_telegram('backup.completed',account,job_id=7) is True
    assert sent[0][0]==token and sent[0][1]=='12345' and 'alpha' in sent[0][2]
    with write_session() as session:
        row=session.query(BackupNotificationDelivery).one()
        assert row.status=='success' and row.run_id==7


def test_disabled_or_unselected_telegram_event_is_not_sent(isolated_db,monkeypatch):
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd'
    notifications.save_telegram({'enabled':True,'chat_id':'12345','token':token,
        'events':['backup.failed']})
    monkeypatch.setattr(notifications,'_send',lambda *_: (_ for _ in ()).throw(AssertionError('must not send')))
    assert notifications.send_telegram('backup.completed',None,job_id=8) is False
    assert notifications.deliveries({})['deliveries']==[]
