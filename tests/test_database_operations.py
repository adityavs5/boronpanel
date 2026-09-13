import subprocess
import sys
from pathlib import Path

from daemon import database_operations, snapshot_jobs
from shared.config import settings


def test_database_mutation_lock_blocks_another_process_and_releases(isolated_db, tmp_path):
    sentinel = tmp_path / 'mutation-ran'
    script = '''
import sys
from pathlib import Path
from shared.config import settings
from shared.validation import ValidationError
from daemon.database_operations import serialized
settings.snapshot_private_dir=sys.argv[1]
@serialized
def mutate():
    Path(sys.argv[2]).write_text('changed')
try:
    mutate()
except ValidationError:
    raise SystemExit(23)
'''
    args = [sys.executable, '-c', script, settings.snapshot_private_dir, str(sentinel)]
    with snapshot_jobs.lock('database-mutations'):
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        assert result.returncode == 23, result.stderr
        assert not sentinel.exists()
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text() == 'changed'


def test_nested_restore_mutations_are_reentrant_and_release_on_failure(isolated_db):
    @database_operations.serialized
    def nested():
        raise RuntimeError('import failed')
    try:
        with database_operations.mutation_lock():
            nested()
    except RuntimeError:
        pass
    with snapshot_jobs.lock('database-mutations', blocking=False):
        pass


def test_background_worker_waits_for_another_process(isolated_db, tmp_path):
    sentinel = tmp_path / 'worker-finished'
    script = '''
import sys
from pathlib import Path
from shared.config import settings
from daemon.database_operations import serialized_worker, serialized
settings.snapshot_private_dir=sys.argv[1]
@serialized
def nested():
    Path(sys.argv[2]).write_text('finished')
@serialized_worker
def worker():
    nested()
print('ready', flush=True)
worker()
'''
    with snapshot_jobs.lock('database-mutations'):
        process = subprocess.Popen([sys.executable, '-c', script, settings.snapshot_private_dir, str(sentinel)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert process.stdout.readline().strip() == 'ready'
            # A background job must remain alive until the other process releases.
            try:
                process.wait(timeout=0.2)
                raise AssertionError('Worker exited instead of waiting')
            except subprocess.TimeoutExpired:
                pass
            assert not sentinel.exists()
        except BaseException:
            process.kill(); process.communicate(); raise
    try:
        output, error = process.communicate(timeout=20)
        assert process.returncode == 0, error
        assert sentinel.read_text() == 'finished'
    finally:
        if process.poll() is None:
            process.kill(); process.communicate()


def test_background_thread_waits_for_another_thread(isolated_db):
    import threading
    started, finished = threading.Event(), threading.Event()
    errors = []
    @database_operations.serialized_worker
    def worker():
        finished.set()
    def target():
        started.set()
        try:worker()
        except BaseException as exc:errors.append(exc)
    with database_operations.mutation_lock():
        thread = threading.Thread(target=target)
        thread.start()
        assert started.wait(2)
        assert not finished.wait(0.1)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert finished.is_set() and not errors
