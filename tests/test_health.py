import datetime as dt

from daemon import health
from shared.db import write_session
from shared.models import HealthSnapshot


def test_get_live_returns_expected_shape():
    live = health.get_live()
    assert isinstance(live["cpu_pct"], float)
    assert live["cpu_count"] >= 1
    assert live["mem_total"] > 0
    assert isinstance(live["disks"], list)
    assert live["net_rx_bytes"] >= 0
    assert live["uptime_seconds"] > 0


def test_take_snapshot_writes_a_row(isolated_db):
    health.take_snapshot()
    with write_session() as session:
        rows = session.query(HealthSnapshot).all()
        assert len(rows) == 1
        assert rows[0].mem_total_bytes > 0


def test_take_snapshot_prunes_old_rows(isolated_db):
    with write_session() as session:
        old = HealthSnapshot(
            taken_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=health.RETENTION_HOURS + 1),
            cpu_pct=1.0,
        )
        session.add(old)
    health.take_snapshot()
    with write_session() as session:
        rows = session.query(HealthSnapshot).all()
        assert len(rows) == 1  # old one pruned, only the fresh one remains


def test_get_history_computes_network_deltas(isolated_db):
    now = dt.datetime.now(dt.timezone.utc)
    with write_session() as session:
        session.add(HealthSnapshot(taken_at=now - dt.timedelta(minutes=2), cpu_pct=10.0, net_rx_bytes=1000, net_tx_bytes=500, mem_total_bytes=100, mem_used_bytes=50))
        session.add(HealthSnapshot(taken_at=now - dt.timedelta(minutes=1), cpu_pct=20.0, net_rx_bytes=1500, net_tx_bytes=700, mem_total_bytes=100, mem_used_bytes=60))

    result = health.get_history({"hours": 24})
    points = result["points"]
    assert len(points) == 2
    assert points[0]["net_rx_delta"] == 0  # first point has no prior to diff against
    assert points[1]["net_rx_delta"] == 500
    assert points[1]["net_tx_delta"] == 200


def test_get_history_respects_hours_window(isolated_db):
    now = dt.datetime.now(dt.timezone.utc)
    with write_session() as session:
        session.add(HealthSnapshot(taken_at=now - dt.timedelta(hours=30)))
        session.add(HealthSnapshot(taken_at=now - dt.timedelta(hours=1)))

    result = health.get_history({"hours": 24})
    assert len(result["points"]) == 1
