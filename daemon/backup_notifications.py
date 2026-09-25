"""Backup-specific notification status and Telegram delivery."""
from __future__ import annotations

import logging
import re
import time
import datetime as dt

import httpx
from sqlalchemy import select

from daemon.appcrypto import decrypt_env, encrypt_env
from shared.db import write_session
from shared.models import (AccountNotificationPrefs, BackupNotificationDelivery,
    BackupNotificationDigest, BackupTelegramSettings, NotificationSettings, Webhook, utcnow)
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


def _record(event,run_id,channel='telegram'):
    with write_session() as session:
        if run_id is not None:
            existing=session.scalar(select(BackupNotificationDelivery).where(
                BackupNotificationDelivery.channel==channel,BackupNotificationDelivery.event==event,
                BackupNotificationDelivery.run_id==run_id).order_by(BackupNotificationDelivery.id.desc()))
            if existing:return None
        row=BackupNotificationDelivery(channel=channel,event=event,run_id=run_id,status='queued')
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


def _email_recipients(account,event,requested):
    """Resolve explicit job recipients, or the account's permitted contact."""
    with write_session() as session:
        settings=session.get(NotificationSettings,1)
        if not settings or not settings.sender_address or not settings.events.get(event,True):return []
        if requested:return list(dict.fromkeys(requested))
        if account is None:return []
        prefs=session.scalar(select(AccountNotificationPrefs).where(AccountNotificationPrefs.account_id==account.id))
        if not prefs or not prefs.customer_email or not prefs.events.get(event,True):return []
        return [prefs.customer_email]


def _due(frequency):
    return utcnow()+dt.timedelta(days=1 if frequency=='daily' else 7)


def _queue_digest(event,account,channels,context):
    frequency=context['digest_frequency'];policy_id=context.get('policy_id');username=getattr(account,'username',None) or 'server'
    item={'event':event,'account':username,'job_id':context.get('job_id'),
        'detail':str(context.get('error') or context.get('detail') or '')[:500],'created_at':utcnow().isoformat()}
    queued={}
    for channel in dict.fromkeys(channels or []):
        # Webhook consumers normally automate responses and therefore remain real-time.
        if channel=='webhook':continue
        recipients=_email_recipients(account,event,context.get('recipients',[])) if channel=='email' else [None]
        if channel=='telegram':
            with write_session() as session:
                settings=_settings(session)
                if not settings.enabled or event not in settings.events:recipients=[]
        for recipient in recipients:
            with write_session() as session:
                row=session.scalar(select(BackupNotificationDigest).where(
                    BackupNotificationDigest.policy_id==policy_id,BackupNotificationDigest.channel==channel,
                    BackupNotificationDigest.recipient==recipient,BackupNotificationDigest.frequency==frequency,
                    BackupNotificationDigest.status=='queued').order_by(BackupNotificationDigest.id.desc()))
                if row is None:
                    row=BackupNotificationDigest(policy_id=policy_id,channel=channel,recipient=recipient,
                        frequency=frequency,items=[item],due_at=_due(frequency));session.add(row)
                else:row.items=[*row.items,item]
            queued[channel]='digest queued'
    return queued


def _digest_text(items):
    lines=[f"Boron backup {len(items)}-event summary"]
    for item in items:
        line=f"- {item['event']} · {item['account']}"
        if item.get('job_id') is not None:line+=f" · job {item['job_id']}"
        if item.get('detail'):line+=f" · {item['detail']}"
        lines.append(line)
    return '\n'.join(lines)


def flush_digests(now=None):
    """Deliver due summaries. The hourly scheduler retries failed rows explicitly."""
    now=now or utcnow()
    with write_session() as session:
        ids=list(session.scalars(select(BackupNotificationDigest.id).where(
            BackupNotificationDigest.status.in_(('queued','failed')),BackupNotificationDigest.due_at<=now)))
    delivered=failed=0
    for ident in ids:
        with write_session() as session:
            row=session.get(BackupNotificationDigest,ident)
            due=row.due_at.replace(tzinfo=dt.timezone.utc) if row and row.due_at.tzinfo is None else row.due_at if row else None
            if row is None or row.status not in ('queued','failed') or due>now:continue
            row.status='sending';channel=row.channel;recipient=row.recipient;items=list(row.items)
        detail='';ok=False
        try:
            body=_digest_text(items)
            if channel=='email':
                from daemon import notifications
                ok=notifications.send_backup_summary(recipient,f'Boron backup summary ({len(items)} events)',body)
                detail='Accepted by local mail transport' if ok else 'Email sender is unavailable'
            elif channel=='telegram':
                with write_session() as session:
                    settings=_settings(session);token=decrypt_env(settings.token_enc).get('token','') if settings.token_enc else ''
                    chat_id=settings.chat_id
                if token and chat_id:_send(token,chat_id,body);ok=True;detail='Delivered to Telegram'
                else:detail='Telegram is no longer configured'
            else:detail='Unsupported digest channel'
        except Exception as exc:
            detail=str(exc)[:1000];logger.exception('Backup digest delivery failed')
        with write_session() as session:
            row=session.get(BackupNotificationDigest,ident)
            row.status='completed' if ok else 'failed';row.error=None if ok else detail
            row.completed_at=utcnow() if ok else None
            if not ok:row.due_at=utcnow()+dt.timedelta(hours=1)
        delivery_id=_record('backup.digest',None,channel)
        if delivery_id:_finish(delivery_id,'provider_accepted' if ok and channel=='email' else 'success' if ok else 'failed',detail)
        if ok:delivered+=1
        else:failed+=1
    return {'delivered':delivered,'failed':failed}


def send_telegram(event,account=None,**context):
    with write_session() as session:
        row=_settings(session)
        if not row.enabled or event not in row.events:return False
        token=decrypt_env(row.token_enc).get('token','');chat_id=row.chat_id
    delivery_id=_record(event,context.get('job_id'))
    if delivery_id is None:return True
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


def dispatch(event,account,channels,**context):
    """Route one deduplicated backup event through selected configured plugins."""
    from daemon import notifications,webhooks
    selected=context.pop('notification_events',None)
    if selected is not None and event not in selected:return {channel:'event disabled' for channel in channels or []}
    frequency=context.get('digest_frequency','immediate')
    digest_results=_queue_digest(event,account,channels,context) if frequency in ('daily','weekly') else {}
    results={}
    for channel in dict.fromkeys(channels or []):
        if channel in digest_results:
            results[channel]=digest_results[channel];continue
        if channel=='telegram':
            results[channel]='provider accepted' if send_telegram(event,account,**context) else 'not dispatched'
            continue
        delivery_id=_record(event,context.get('job_id'),channel)
        if delivery_id is None:
            results[channel]='deduplicated';continue
        try:
            if channel=='email':
                requested=context.get('recipients',[])
                if requested:
                    resolved=_email_recipients(account,event,requested)
                    subject=notifications._subjects('Boron').get(event,f'Boron notification: {event}')
                    body=notifications._render_body(event,getattr(account,'username','server'),context)+'\n\n— Boron'
                    sent=bool(resolved) and all(notifications.send_backup_summary(recipient,subject,body) for recipient in resolved)
                else:sent=notifications.maybe_send(event,account,**context)
                _finish(delivery_id,'provider_accepted' if sent else 'skipped',
                    'Accepted by local mail transport' if sent else 'No configured recipient or event disabled')
                results[channel]='provider accepted' if sent else 'not dispatched'
            elif channel=='webhook':
                queued=webhooks.maybe_trigger(event,account,**context)
                _finish(delivery_id,'queued' if queued else 'skipped',
                    f'{len(queued)} signed webhook delivery(s) queued' if queued else 'No enabled webhook selected this event')
                results[channel]='queued' if queued else 'not dispatched'
            else:
                _finish(delivery_id,'failed','Unknown notification channel');results[channel]='failed'
        except Exception as exc:
            _finish(delivery_id,'failed',exc);logger.exception('Backup notification dispatch failed');results[channel]='failed'
    return results


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
