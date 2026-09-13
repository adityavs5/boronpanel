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
