"""The server-wide database/DNS mutation locks wait instead of failing at once
(live findings: concurrent unrelated saves, account creation and account
termination failed immediately with "operation is in progress", and a
termination stopped half-done with databases left behind)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from daemon import database_operations, dns_operations, mutation_locks, snapshot_jobs
from shared.validation import BusyError, ConflictError


@pytest.mark.parametrize("module,name", [
    (database_operations, "database-mutations"),
    (dns_operations, "dns-mutations"),
])
def test_request_waits_for_a_short_cross_process_holder(isolated_db, monkeypatch, module, name):
    monkeypatch.setattr(mutation_locks, "REQUEST_WAIT_SECONDS", 10.0)
    calls = []

    @module.serialized
    def mutate():
        calls.append(time.monotonic())
        return "done"

    holder_ready, release = threading.Event(), threading.Event()

    def hold():
        # A separate open file description behaves like another process.
        with snapshot_jobs.lock(name, blocking=False):
            holder_ready.set()
            release.wait(5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.submit(hold)
        assert holder_ready.wait(2)
        pending = executor.submit(mutate)
        time.sleep(0.3)
        assert not calls, "the mutation must wait while the lock is held"
        released_at = time.monotonic()
        release.set()
        assert pending.result(timeout=5) == "done"
    assert calls[0] >= released_at


@pytest.mark.parametrize("module,message", [
    (database_operations, "database backup or management operation is in progress"),
    (dns_operations, "DNS backup, restore or management operation is in progress"),
])
def test_wait_is_bounded_and_reports_busy_as_conflict(isolated_db, monkeypatch, module, message):
    monkeypatch.setattr(mutation_locks, "REQUEST_WAIT_SECONDS", 0.3)

    @module.serialized
    def mutate():
        raise AssertionError("must not run while another holder keeps the lock")

    name = "database-mutations" if module is database_operations else "dns-mutations"
    with snapshot_jobs.lock(name, blocking=False):
        started = time.monotonic()
        with pytest.raises(BusyError, match=message) as raised:
            mutate()
        waited = time.monotonic() - started
    assert isinstance(raised.value, ConflictError)
    assert 0.25 <= waited < 3


def test_in_process_contention_queues_instead_of_failing(isolated_db, monkeypatch):
    """Two customers saving DNS at once: both saves succeed, one after the other."""
    monkeypatch.setattr(mutation_locks, "REQUEST_WAIT_SECONDS", 10.0)
    active, overlaps, done = [], [], []

    @dns_operations.serialized
    def save(zone):
        active.append(zone)
        if len(active) > 1:
            overlaps.append(tuple(active))
        time.sleep(0.05)
        active.remove(zone)
        done.append(zone)

    with ThreadPoolExecutor(max_workers=8) as executor:
        for future in [executor.submit(save, f"zone{i}.test") for i in range(8)]:
            future.result(timeout=10)
    assert sorted(done) == sorted(f"zone{i}.test" for i in range(8))
    assert overlaps == []


def test_background_wait_outlasts_the_request_budget(isolated_db, monkeypatch):
    monkeypatch.setattr(mutation_locks, "REQUEST_WAIT_SECONDS", 0.1)
    monkeypatch.setattr(mutation_locks, "BACKGROUND_WAIT_SECONDS", 10.0)

    @database_operations.serialized
    def create():
        return "created"

    def queued_task():
        with mutation_locks.background_wait():
            return create()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with database_operations.mutation_lock():
            pending = executor.submit(queued_task)
            time.sleep(0.4)  # well past the request budget
            assert not pending.done()
        assert pending.result(timeout=5) == "created"
    # The flag is per thread and restored afterwards.
    assert mutation_locks._wait_limit(False, None) == 0.1


def test_nested_account_lock_contention_is_reported_as_busy(isolated_db):
    with snapshot_jobs.lock("account-77", blocking=False):
        with pytest.raises(BusyError):
            with database_operations.mutation_lock():
                with snapshot_jobs.lock("account-77", blocking=False):
                    pass
    with snapshot_jobs.lock("database-mutations", blocking=False):
        pass


def test_control_tasks_run_with_background_waits(isolated_db, monkeypatch):
    from daemon import control_tasks

    seen = []
    submitted = []
    monkeypatch.setattr(control_tasks._executor, "submit", lambda fn, task_id: submitted.append((fn, task_id)))
    for name in ("begin_action_trace",):
        monkeypatch.setattr(control_tasks.audit, name, lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(control_tasks.audit, "end_action_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(control_tasks.audit, "record", lambda *_args, **_kwargs: None)

    def execute(row, params):
        seen.append(mutation_locks._wait_limit(False, None))
        return {"id": 1, "username": "taskacct"}, None, None, "taskacct"

    monkeypatch.setattr(control_tasks, "_execute", execute)
    control_tasks.start_account_create({
        "username": "taskacct", "_authenticated_username": "administrator",
        "_authenticated_role": "admin", "_authenticated_account_id": None, "_source_ip": "192.0.2.10",
    })
    submitted[0][0](submitted[0][1])
    assert seen == [mutation_locks.BACKGROUND_WAIT_SECONDS]


def test_termination_holds_both_mutation_locks_for_every_hook(isolated_db, monkeypatch):
    """Teardown hooks run inside one critical section, so a concurrent
    database or DNS operation can no longer make one of them fail halfway."""
    from daemon import handlers_account as ha
    from shared.db import write_session
    from shared.models import Account

    for name in ("TERMINATE_HOOKS",):
        monkeypatch.setattr(ha, name, [])
    monkeypatch.setattr(ha.sysops, "remove_quota", lambda username: None)
    monkeypatch.setattr(ha.sysops, "delete_linux_user", lambda username, **_kwargs: None)
    with write_session() as session:
        session.add(Account(username="demo1", status="active"))

    observed = []

    @database_operations.serialized
    def drop_databases(account):
        observed.append("databases")

    @dns_operations.serialized
    def delete_zone(account):
        observed.append("dns")

    def lock_files_are_held(account):
        for name in ("database-mutations", "dns-mutations"):
            with pytest.raises(BlockingIOError):
                with snapshot_jobs.lock(name, blocking=False):
                    pass
        observed.append("held")

    ha.TERMINATE_HOOKS.extend([drop_databases, delete_zone, lock_files_are_held])
    monkeypatch.setattr(mutation_locks, "REQUEST_WAIT_SECONDS", 10.0)
    with ThreadPoolExecutor(max_workers=1) as executor:
        # Another tenant's database operation is still running when the
        # termination starts: it waits rather than failing a hook.
        with database_operations.mutation_lock():
            pending = executor.submit(ha.terminate_account, {"username": "demo1"})
            time.sleep(0.3)
            assert observed == []
        result = pending.result(timeout=10)
    assert result["status"] == "terminated", result.get("last_error")
    assert observed == ["databases", "dns", "held"]


def test_long_holder_cannot_pin_the_request_thread_pool(isolated_db, monkeypatch):
    """While a backup keeps a lock for minutes, only REQUEST_WAITER_SLOTS
    request threads wait the full budget; the rest give up after
    OVERFLOW_WAIT_SECONDS, so sign-in and unrelated operations on the shared
    default executor are never starved by lock waiters."""
    monkeypatch.setattr(mutation_locks, "REQUEST_WAIT_SECONDS", 3.0)
    monkeypatch.setattr(mutation_locks, "OVERFLOW_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(mutation_locks, "REQUEST_WAITER_SLOTS", 2)
    outcomes = []

    def attempt(module):
        started = time.monotonic()
        try:
            with module.mutation_lock():
                outcomes.append(("ok", time.monotonic() - started))
        except BusyError:
            outcomes.append(("busy", time.monotonic() - started))

    with snapshot_jobs.lock("database-mutations", blocking=False), \
            snapshot_jobs.lock("dns-mutations", blocking=False):
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(attempt, database_operations if i % 2 else dns_operations)
                       for i in range(6)]
            time.sleep(1.0)
            fast = [elapsed for kind, elapsed in outcomes if kind == "busy"]
            # Slots are shared across both locks: four overflow waiters gave up quickly.
            assert len(fast) == 4 and all(elapsed < 0.9 for elapsed in fast)
        for future in futures:
            future.result(timeout=5)
    assert len(outcomes) == 6
    assert mutation_locks._request_waiters == 0, "every slot is returned"


def test_waiter_slot_is_returned_once_the_lock_is_held(isolated_db, monkeypatch):
    monkeypatch.setattr(mutation_locks, "REQUEST_WAIT_SECONDS", 5.0)
    monkeypatch.setattr(mutation_locks, "REQUEST_WAITER_SLOTS", 1)
    inside = []
    holder_ready, release = threading.Event(), threading.Event()

    def hold():
        with snapshot_jobs.lock("dns-mutations", blocking=False):
            holder_ready.set()
            release.wait(5)

    def mutate():
        with dns_operations.mutation_lock():
            inside.append(mutation_locks._request_waiters)

    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.submit(hold)
        assert holder_ready.wait(2)
        pending = executor.submit(mutate)
        time.sleep(0.2)
        assert mutation_locks._request_waiters == 1
        release.set()
        pending.result(timeout=5)
    assert inside == [0]


def test_background_and_worker_waits_do_not_use_request_slots(isolated_db, monkeypatch):
    monkeypatch.setattr(mutation_locks, "REQUEST_WAITER_SLOTS", 0)
    monkeypatch.setattr(mutation_locks, "OVERFLOW_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(mutation_locks, "BACKGROUND_WAIT_SECONDS", 5.0)
    done = []

    def queued():
        with mutation_locks.background_wait(), database_operations.mutation_lock():
            done.append("background")

    def worker():
        with database_operations.mutation_lock(blocking=True):
            done.append("worker")

    with ThreadPoolExecutor(max_workers=2) as executor:
        with database_operations.mutation_lock():
            futures = [executor.submit(queued), executor.submit(worker)]
            time.sleep(0.3)
            assert not done
        for future in futures:
            future.result(timeout=5)
    assert sorted(done) == ["background", "worker"]
