import pytest

from testsupport import pytest_shard


def test_shard_assignment_is_stable_and_exhaustive():
    nodeids = [f"tests/test_sample.py::test_case[{index}]" for index in range(500)]
    first = [pytest_shard.shard_for(nodeid, 8) for nodeid in nodeids]
    second = [pytest_shard.shard_for(nodeid, 8) for nodeid in nodeids]
    assert first == second
    assert set(first) == set(range(8))
    assert all(0 <= index < 8 for index in first)


@pytest.mark.parametrize("total", [0, -1])
def test_shard_assignment_rejects_nonpositive_total(total):
    with pytest.raises(ValueError, match="positive"):
        pytest_shard.shard_for("test", total)


def test_shard_configuration_requires_a_complete_pair(monkeypatch):
    monkeypatch.setenv("BORON_TEST_SHARD", "0")
    monkeypatch.delenv("BORON_TEST_SHARDS", raising=False)
    with pytest.raises(pytest.UsageError, match="set together"):
        pytest_shard._configuration()
