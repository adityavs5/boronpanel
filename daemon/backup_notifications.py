"""Backup-specific notification status and Telegram delivery."""
from __future__ import annotations

import logging
import re
import time

import httpx
from sqlalchemy import select

from daemon.appcrypto import decrypt_env, encrypt_env
from shared.db import write_session
from shared.models import (BackupNotificationDelivery, BackupTelegramSettings,
    NotificationSettings, Webhook, utcnow)
from shared.validation import ValidationError

logger=logging.getLogger('borond.backup_notifications')
EVENTS=('backup.completed','backup.failed','backup.partial','backup.overdue',
    'backup.destination_unavailable','backup.restore_completed','backup.restore_failed','backup.download_ready')
_transport=None


def _settings(session):
    row=session.get(BackupTelegramSettings,1)
    if row is None:
        row=BackupTelegramSettings(id=1);session.add(row);session.flush()
    return row


def _delivery(row):
    return {'id':row.id,'channel':row.channel,'event':row.event,'run_id':row.run_id,
        'status':row.status,'detail':row.detail,'created_at':row.created_at.isoformat(),
        'completed_at':row.completed_at.isoformat() if row.completed_at else None}


def status(params=None):
    with write_session() as session:
        telegram=_settings(session);email=session.get(NotificationSettings,1)
        webhooks=session.scalars(select(Webhook).where(Webhook.enabled==True)).all()
        return {'plugins':[
            {'kind':'email','enabled':bool(email and email.sender_address),
             'summary':email.sender_address if email and email.sender_address else 'Sender address is not configured'},
            {'kind':'telegram','enabled':telegram.enabled,'configured':bool(telegram.token_enc and telegram.chat_id),
             'chat_id':telegram.chat_id,'events':telegram.events,'summary':telegram.chat_id or 'Bot is not configured'},
            {'kind':'webhook','enabled':bool(webhooks),'count':len(webhooks),
             'summary':f'{len(webhooks)} enabled webhook(s)' if webhooks else 'No enabled webhooks'},
        ]}


def save_telegram(params):
    enabled=params.get('enabled')
    if not isinstance(enabled,bool):raise ValidationError('Choose whether Telegram notifications are enabled')
    chat_id=str(params.get('chat_id','')).strip()
    if chat_id and not re.fullmatch(r'-?[1-9][0-9]{0,19}',chat_id):raise ValidationError('Enter a valid Telegram chat ID')
    events=params.get('events',[])
    if not isinstance(events,list) or not events or any(item not in EVENTS for item in events):
        raise ValidationError('Choose valid Telegram backup events')
    token=str(params.get('token','')).strip()
    if token and (len(token)>256 or not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,200}',token)):
        raise ValidationError('Enter a valid Telegram bot token')
    with write_session() as session:
        row=_settings(session)
        if token:row.token_enc=encrypt_env({'token':token})
        if enabled and (not row.token_enc or not chat_id):raise ValidationError('Bot token and chat ID are required before enabling Telegram')
        row.enabled=enabled;row.chat_id=chat_id;row.events=list(dict.fromkeys(events));row.updated_at=utcnow()
    return status()


def _record(event,run_id):
    with write_session() as session:
        row=BackupNotificationDelivery(channel='telegram',event=event,run_id=run_id,status='pending')
        session.add(row);session.flush();return row.id


def _finish(ident,status_value,detail):
    with write_session() as session:
        row=session.get(BackupNotificationDelivery,ident)
        if row:row.status=status_value;row.detail=str(detail)[:1000];row.completed_at=utcnow()


def _send(token,chat_id,text):
    url=f'https://api.telegram.org/bot{token}/sendMessage'
    with httpx.Client(timeout=10,trust_env=False,follow_redirects=False,transport=_transport) as client:
        response=client.post(url,json={'chat_id':chat_id,'text':text,'disable_web_page_preview':True})
    if response.status_code<200 or response.status_code>=300:
        raise RuntimeError(f'Telegram returned HTTP {response.status_code}')


def send_telegram(event,account=None,**context):
    with write_session() as session:
        row=_settings(session)
        if not row.enabled or event not in row.events:return False
        token=decrypt_env(row.token_enc).get('token','');chat_id=row.chat_id
    delivery_id=_record(event,context.get('job_id'))
    username=getattr(account,'username',None) or context.get('username') or 'server'
    text=f'Boron backup notification\nEvent: {event}\nAccount: {username}'
    if context.get('job_id'):text+=f"\nRun: {context['job_id']}"
    if context.get('error'):text+=f"\nError: {str(context['error'])[:500]}"
    for attempt in range(3):
        try:
            _send(token,chat_id,text);_finish(delivery_id,'success',f'Delivered on attempt {attempt+1}');return True
        except (httpx.HTTPError,RuntimeError) as exc:
            if attempt==2:
                _finish(delivery_id,'failed',exc);logger.warning('Telegram backup notification failed: %s',exc);return False
            time.sleep((.5,1.5)[attempt])
    return False


def test_telegram(params=None):
    with write_session() as session:
        row=_settings(session)
        if not row.token_enc or not row.chat_id:raise ValidationError('Configure the Telegram bot first')
        token=decrypt_env(row.token_enc).get('token','');chat_id=row.chat_id
    delivery_id=_record('backup.test',None)
    try:_send(token,chat_id,'Boron backup notifications are connected.');_finish(delivery_id,'success','Test delivered')
    except Exception as exc:_finish(delivery_id,'failed',exc);raise ValidationError(f'Telegram test failed: {exc}') from None
    return {'status':'delivered'}


def deliveries(params=None):
    with write_session() as session:
        rows=session.scalars(select(BackupNotificationDelivery).order_by(
            BackupNotificationDelivery.id.desc()).limit(200)).all()
        return {'deliveries':[_delivery(row) for row in rows]}
