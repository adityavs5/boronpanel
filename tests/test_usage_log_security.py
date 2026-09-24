import os

import pytest

from daemon import usage


def line(byte_count='123', date=None):
    date = date or usage.utcnow().strftime('%d/%b/%Y')
    return f'1.2.3.4 - - [{date}:06:35:50 +0000] "GET / HTTP/1.1" 200 {byte_count} "-" "curl"\n'


@pytest.mark.parametrize('kind', ['leaf', 'parent', 'fifo'])
def test_usage_log_rejects_unsafe_paths(tmp_path, kind):
    protected = tmp_path / 'protected'
    protected.mkdir()
    (protected / 'access.log').write_text(line())
    logs = tmp_path / 'logs'
    if kind == 'parent':
        logs.symlink_to(protected, target_is_directory=True)
    else:
        logs.mkdir()
        if kind == 'leaf':
            (logs / 'access.log').symlink_to(protected / 'access.log')
        else:
            os.mkfifo(logs / 'access.log')
    with pytest.raises((OSError, ValueError)):
        usage._parse_access_log(logs / 'access.log')


def test_usage_skips_oversized_records_and_invalid_dates_without_losing_next_record(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, 'ACCESS_LOG_MAX_LINE', 150)
    log = tmp_path / 'access.log'
    log.write_text('x' * 1000 + line('999') + line(date='99/Sep/2026') + line('9' * 500) + line())
    assert usage._parse_access_log(log) == {usage.utcnow().date().isoformat(): 123}


def test_usage_oversized_file_does_not_return_partial_total(tmp_path, monkeypatch):
    log = tmp_path / 'access.log'
    log.write_text(line() * 4)
    monkeypatch.setattr(usage, 'ACCESS_LOG_MAX_BYTES', 100)
    with pytest.raises(ValueError, match='scan size'):
        usage._parse_access_log(log)


def test_usage_time_budget_discards_partial_total(tmp_path, monkeypatch):
    log = tmp_path / 'access.log'
    log.write_text(line())
    times = iter([0, 11])
    monkeypatch.setattr(usage.time, 'monotonic', lambda: next(times))
    with pytest.raises(ValueError, match='resource budget'):
        usage._parse_access_log(log)
