from daemon.configtx import ConfigWriter, StepResult


def test_apply_success(tmp_path):
    target = tmp_path / "vhost.conf"
    writer = ConfigWriter(
        target_path=str(target),
        validate=lambda p: StepResult(True),
        reload=lambda: StepResult(True),
        verify=lambda: StepResult(True),
        backup_dir=str(tmp_path / "backups"),
        subsystem="test",
    )
    result = writer.apply("docRoot /home/demo/public_html\n")
    assert result.ok
    assert target.read_text() == "docRoot /home/demo/public_html\n"


def test_validate_failure_leaves_target_untouched(tmp_path):
    target = tmp_path / "vhost.conf"
    target.write_text("original content\n")
    writer = ConfigWriter(
        target_path=str(target),
        validate=lambda p: StepResult(False, "syntax error"),
        reload=lambda: StepResult(True),
        verify=lambda: StepResult(True),
        backup_dir=str(tmp_path / "backups"),
        subsystem="test",
    )
    result = writer.apply("broken {{{ config")
    assert not result.applied
    assert not result.rolled_back
    assert target.read_text() == "original content\n"


def test_verify_failure_triggers_rollback(tmp_path):
    target = tmp_path / "vhost.conf"
    target.write_text("good old config\n")
    writer = ConfigWriter(
        target_path=str(target),
        validate=lambda p: StepResult(True),
        reload=lambda: StepResult(True),
        verify=lambda: StepResult(False, "process did not restart"),
        backup_dir=str(tmp_path / "backups"),
        subsystem="test",
    )
    result = writer.apply("new but broken at runtime config\n")
    assert result.applied
    assert result.rolled_back
    assert target.read_text() == "good old config\n"


def test_reload_failure_triggers_rollback(tmp_path):
    target = tmp_path / "vhost.conf"
    target.write_text("good old config\n")
    writer = ConfigWriter(
        target_path=str(target),
        validate=lambda p: StepResult(True),
        reload=lambda: StepResult(False, "service failed to start"),
        verify=lambda: StepResult(True),
        backup_dir=str(tmp_path / "backups"),
        subsystem="test",
    )
    result = writer.apply("new config\n")
    assert result.rolled_back
    assert target.read_text() == "good old config\n"


def test_no_prior_file_removed_on_validate_pass_but_reload_fail(tmp_path):
    target = tmp_path / "fresh_vhost.conf"
    writer = ConfigWriter(
        target_path=str(target),
        validate=lambda p: StepResult(True),
        reload=lambda: StepResult(False, "boom"),
        verify=lambda: StepResult(True),
        backup_dir=str(tmp_path / "backups"),
        subsystem="test",
    )
    result = writer.apply("brand new vhost\n")
    assert result.rolled_back
    assert not target.exists()


def test_validator_exception_is_treated_as_failure(tmp_path):
    target = tmp_path / "vhost.conf"

    def boom(_path):
        raise RuntimeError("validator crashed")

    writer = ConfigWriter(
        target_path=str(target),
        validate=boom,
        reload=lambda: StepResult(True),
        verify=lambda: StepResult(True),
        backup_dir=str(tmp_path / "backups"),
        subsystem="test",
    )
    result = writer.apply("config\n")
    assert not result.applied
    assert not target.exists()
