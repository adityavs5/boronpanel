from concurrent.futures import ThreadPoolExecutor
import pytest

from daemon import dns_operations, snapshot_jobs, powerdns, cloudflare, cloudflare_ops, dnsprovider, handlers_dns, cloudflare_accounts
from shared.validation import ValidationError


def test_dns_lock_is_reentrant_and_released_after_error(isolated_db):
    with dns_operations.mutation_lock():
        with dns_operations.mutation_lock():
            with pytest.raises(BlockingIOError):
                with snapshot_jobs.lock('dns-mutations',blocking=False):pass
    with pytest.raises(RuntimeError):
        with dns_operations.mutation_lock():raise RuntimeError('test')
    with snapshot_jobs.lock('dns-mutations',blocking=False):pass


@pytest.mark.parametrize('function,args',[
    (powerdns.upsert_record,('alpha.test','www','A',['192.0.2.1'])),
    (powerdns.delete_zone,('alpha.test',)),
    (cloudflare.upsert_record,('alpha.test','www','A',['192.0.2.1'])),
    (cloudflare.delete_zone,('alpha.test',)),
    (dnsprovider.upsert_record,('alpha.test','www','A',['192.0.2.1'])),
    (handlers_dns.create_zone,({'domain':'alpha.test','username':'alpha'},)),
    (handlers_dns.delete_zone,({'domain':'alpha.test'},)),
    (cloudflare_ops.zone_enable,({'domain':'alpha.test'},)),
    (cloudflare_ops.zone_disable,({'domain':'alpha.test'},)),
    (cloudflare_ops.zone_status,({'domain':'alpha.test'},)),
    (cloudflare_ops.enable_proxy,({'domain':'alpha.test'},)),
    (cloudflare_accounts.delete_account,({'id':1,'force':True},)),
])
def test_dns_mutations_reject_competing_recovery_lock(isolated_db,function,args):
    # Hold the process lock directly to exercise the cross-process path rather
    # than only the in-process mutex. No backend request should be reached.
    with snapshot_jobs.lock('dns-mutations',blocking=False):
        with pytest.raises(ValidationError,match='DNS backup, restore'):
            function(*args)


def test_dns_thread_contention_fails_promptly_and_worker_resumes(isolated_db):
    import threading
    began=threading.Event()
    completed=threading.Event()
    @dns_operations.serialized_worker
    def worker():completed.set()
    def invoke():
        began.set();worker()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with dns_operations.mutation_lock():
            future=executor.submit(invoke)
            assert began.wait(2)
            assert not completed.wait(.1)
        future.result(timeout=3)
    assert completed.is_set()
