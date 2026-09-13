"""Bounded, cached chrony telemetry for clock drift and 2FA health."""
import csv
import math
import threading
import time
from daemon.procutil import run

_cache=None
_cached_at=0.0
_lock=threading.Lock()


def parse_tracking(output,now=None):
    fields=next(csv.reader([output.strip()]))
    if len(fields)!=14:raise ValueError('Unexpected chrony tracking response')
    numbers=[float(value) for value in fields[2:13]]
    if not all(math.isfinite(value) for value in numbers):raise ValueError('Non-finite chrony values')
    stratum,reference,offset,last,rms,frequency,residual,skew,delay,dispersion,interval=numbers
    if delay<0 or dispersion<0 or interval<0:raise ValueError('Invalid chrony uncertainty')
    age=(time.time() if now is None else now)-reference
    bound=abs(offset)+dispersion+delay/2
    synchronized=fields[13] in ('Normal','Insert second','Delete second') and 0<stratum<16 and fields[0] not in ('00000000','7F7F0101')
    if not synchronized:status,message='critical','Clock is not synchronized to an external time source.'
    elif age < -5 or age>max(300,min(3600,4*interval)):status,message='critical','Time-source measurements are stale; check time synchronization.'
    elif bound>=5:status,message='critical','Clock uncertainty is high enough to disrupt authentication codes.'
    elif bound>=1:status,message='warning','Clock uncertainty is increasing; check time-source reachability.'
    else:status,message='healthy','Clock is synchronized for time-based authentication.'
    return {'status':status,'message':message,'synchronized':synchronized,'source':fields[1],
        'offset_seconds':offset,'error_bound_seconds':bound,'reference_age_seconds':max(0,age),
        'stratum':int(stratum),'checked_at':time.time() if now is None else now}


def get_status(refresh=False):
    global _cache,_cached_at
    with _lock:
        now=time.monotonic()
        if not refresh and _cache is not None and now-_cached_at<30:return dict(_cache)
        try:
            result=run(['chronyc','-c','-n','tracking'],timeout=3)
            result.raise_if_failed('Read clock synchronization')
            value=parse_tracking(result.stdout)
        except Exception:
            value={'status':'unknown','message':'Clock synchronization could not be verified. Check that chrony is running.',
                'synchronized':False,'checked_at':time.time()}
        _cache=value;_cached_at=time.monotonic()
        return dict(value)
