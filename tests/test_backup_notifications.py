from daemon import backup_notifications as notifications
from shared.db import write_session
import datetime as dt

from shared.models import BackupNotificationDelivery, BackupNotificationDigest, utcnow


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
    assert notifications.send_telegram('backup.completed',account,job_id=7) is True
    assert len(sent)==1 and sent[0][0]==token and sent[0][1]=='12345' and 'alpha' in sent[0][2]
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


def test_daily_digest_is_durable_and_delivered_once(isolated_db,monkeypatch):
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd'
    notifications.save_telegram({'enabled':True,'chat_id':'12345','token':token,
        'events':['backup.completed']})
    sent=[]
    monkeypatch.setattr(notifications,'_send',lambda actual,chat,text:sent.append((actual,chat,text)))
    account=type('Account',(),{'username':'alpha'})()
    result=notifications.dispatch('backup.completed',account,['telegram'],job_id=7,policy_id=2,
        digest_frequency='daily',notification_events=['backup.completed'])
    assert result=={'telegram':'digest queued'} and sent==[]
    with write_session() as session:
        row=session.query(BackupNotificationDigest).one()
        assert row.status=='queued' and row.items[0]['job_id']==7
    flushed=notifications.flush_digests(utcnow()+dt.timedelta(days=2))
    assert flushed=={'delivered':1,'failed':0}
    assert len(sent)==1 and '1-event summary' in sent[0][2]
    assert notifications.flush_digests(utcnow()+dt.timedelta(days=2))=={'delivered':0,'failed':0}


def test_httpx_request_log_never_contains_bot_token_or_url_path(isolated_db,caplog):
    """Regression: httpx logs 'HTTP Request: POST <url>' at INFO, and the
    Telegram URL path carries the bot token (webhook paths carry secrets)."""
    import httpx
    import logging
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd'
    notifications.save_telegram({'enabled':True,'chat_id':'12345','token':token,'events':['backup.completed']})
    original=notifications._transport
    notifications._transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'ok':True}))
    try:
        with caplog.at_level(logging.INFO,logger='httpx'):
            assert notifications.send_telegram('backup.completed',None,job_id=11) is True
            with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(204))) as client:
                # webhooks._pinned_post always requests the pinned IP literal
                client.post('https://203.0.113.9:443/services/T000/B000/webhooksecretvalue?sig=abc',
                            headers={'Host':'hooks.example.test'})
    finally:
        notifications._transport=original
    rendered='\n'.join(record.getMessage() for record in caplog.records)
    assert 'HTTP Request' in rendered and 'api.telegram.org' in rendered and '203.0.113.9' in rendered
    assert token not in rendered and 'ABCDEFGHIJ' not in rendered
    assert 'webhooksecretvalue' not in rendered and 'sig=abc' not in rendered


def test_telegram_failure_detail_and_log_redact_token(isolated_db,monkeypatch,caplog):
    import httpx
    token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd'
    notifications.save_telegram({'enabled':True,'chat_id':'12345','token':token,'events':['backup.failed']})

    def boom(request):
        raise httpx.ConnectError(f'cannot reach {request.url}',request=request)

    original=notifications._transport
    notifications._transport=httpx.MockTransport(boom)
    monkeypatch.setattr(notifications.time,'sleep',lambda *_:None)
    try:
        assert notifications.send_telegram('backup.failed',None,job_id=12) is False
    finally:
        notifications._transport=original
    with write_session() as session:
        row=session.query(BackupNotificationDelivery).one()
        assert row.status=='failed' and token not in row.detail and 'redacted' in row.detail
    assert token not in caplog.text
    assert notifications._redact('GET https://api.telegram.org/bot123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd/x')==\
        'GET https://api.telegram.org/bot[redacted]/x'
