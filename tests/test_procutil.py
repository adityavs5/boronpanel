import logging

from daemon.procutil import run


def test_run_logs_full_argv_by_default(caplog):
    with caplog.at_level(logging.INFO, logger="borond.proc"):
        run(["echo", "hello-world-marker"], timeout=5)
    assert any("hello-world-marker" in r.message for r in caplog.records)


def test_run_redacts_listed_secret_from_log_line(caplog):
    """Phase 4 feature 8's own real near-miss: Joomla's password-hashing
    call originally passed the admin password as a CLI argument, and
    daemon/procutil.py's run() logs the full argv unconditionally -- found
    by grepping daemon.log after this feature's own live verification.
    Fixed there by switching to stdin; this redact param exists for the
    one remaining case (PrestaShop's install/index_cli.php) that has no
    stdin alternative at all."""
    with caplog.at_level(logging.INFO, logger="borond.proc"):
        result = run(["echo", "--password=TopSecret123!"], timeout=5, redact=["TopSecret123!"])
    assert not any("TopSecret123!" in r.message for r in caplog.records)
    assert any("REDACTED" in r.message for r in caplog.records)
    # the real subprocess still receives the unredacted value -- only the
    # log line is scrubbed
    assert "TopSecret123!" in result.stdout


def test_run_redact_does_not_affect_actual_subprocess_args():
    result = run(["echo", "--password=Real Value 42!"], timeout=5, redact=["Real Value 42!"])
    assert "Real Value 42!" in result.stdout


def test_run_redact_none_is_a_no_op(caplog):
    with caplog.at_level(logging.INFO, logger="borond.proc"):
        run(["echo", "plain"], timeout=5, redact=None)
    assert any("exec: echo plain" in r.message for r in caplog.records)


def test_streamed_input_and_discarded_output(tmp_path):
    source=tmp_path/'input';source.write_bytes(b'raw\0bytes\n')
    result=run(['/usr/bin/wc','-c'],input_path=str(source))
    assert result.ok and int(result.stdout.strip())==len(source.read_bytes())
    result=run(['/usr/bin/cat'],input_path=str(source),discard_stdout=True)
    assert result.ok and result.stdout==''


def test_bounded_command_preserves_input_and_both_output_streams():
    import sys
    payload = 'test-data' * 100_000
    result = run([sys.executable, '-c', 'import sys; sys.stderr.write("ready"); data=sys.stdin.read(); sys.stdout.write(data)'],
                 input_text=payload, output_limit=2_000_000)
    assert result.ok and result.stdout == payload and result.stderr == 'ready'


def test_bounded_command_rejects_excess_output():
    import sys
    import pytest
    with pytest.raises(RuntimeError, match='capture limit'):
        run([sys.executable, '-c', 'import os; os.write(2, b"x"*100000)'], output_limit=4096)


def test_bounded_command_timeout_cleans_up_pipe_holding_child(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time
    import pytest
    pidfile = tmp_path / 'pid'
    code = 'import os,time; p=os.fork(); open(sys.argv[1],"w").write(str(os.getpid())) if p == 0 else None; time.sleep(30)'
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, '-c', 'import sys; ' + code, str(pidfile)], output_limit=4096, timeout=2.0)
    child = int(pidfile.read_text())
    for _ in range(50):
        path = __import__('pathlib').Path(f'/proc/{child}/stat')
        if not path.exists() or path.read_text().split()[2] == 'Z':
            break
        time.sleep(0.01)
    else:
        os.kill(child, signal.SIGKILL)
        pytest.fail('timed out command left a child running')


def test_tenant_wrapper_automatically_enforces_capture_limit(monkeypatch):
    import pytest
    from daemon import procutil
    def bounded(args, **kwargs):
        assert kwargs['limit'] == procutil.MAX_TENANT_OUTPUT
        raise RuntimeError('bounded runner reached')
    monkeypatch.setattr(procutil, '_bounded_run', bounded)
    with pytest.raises(RuntimeError, match='bounded runner reached'):
        run(['/usr/sbin/runuser', '-u', 'tenant', '--', '/bin/true'])
