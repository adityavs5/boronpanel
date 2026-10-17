"""Simultaneous first page/poll checks must share one cache row."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select
from daemon import updates
from shared.config import settings
from shared.db import write_session
from shared.models import UpdateState


def test_simultaneous_first_checks_create_one_state(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, 'update_github_repo', 'acme/boron')
    monkeypatch.setattr(settings, 'update_channel', 'stable')
    monkeypatch.setattr(updates, 'load_test_update_override', lambda: None)
    fetched = []
    def fetch():
        fetched.append(True)
        return {'version': '99.0.0', 'changelog_url': '', 'tarball_url': '', 'checksum_url': ''}
    monkeypatch.setattr(updates, '_fetch_release', fetch)
    barrier = Barrier(2)
    original = updates._get_state
    def concurrent_state(session):
        barrier.wait(timeout=10)
        return original(session)
    monkeypatch.setattr(updates, '_get_state', concurrent_state)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: updates.check(), range(2)))
    assert all(r['error'] is None and r['latest_version'] == '99.0.0' for r in results)
    assert len(fetched) == 1
    with write_session() as session:
        assert session.scalar(select(func.count()).select_from(UpdateState)) == 1
