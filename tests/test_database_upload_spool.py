"""SQL transfers use a private shared spool, never a service's private /tmp."""
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from api.routers import databases as api
from daemon import handlers_database as helper
from shared.config import settings
from shared.database_uploads import database_upload_directory
from shared.validation import ValidationError


@pytest.fixture
def spool(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'db_path', str(tmp_path / 'panel.db'))
    monkeypatch.setattr(helper.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid()))
    helper.prepare_import_spool()
    return database_upload_directory()


def upload(spool, text=b'CREATE TABLE marker (id INT);'):
    path = spool / 'boron-db-upload-fixture.sql'
    path.write_bytes(text)
    path.chmod(0o600)
    return path


def test_private_spool_is_shared_and_initialization_idempotent(spool):
    source = upload(spool)
    assert helper.prepare_import_spool() == spool
    assert source.read_bytes() == b'CREATE TABLE marker (id INT);'
    assert spool.stat().st_mode & 0o777 == 0o700
    assert helper._verified_import(str(source)) == source


def test_route_hands_verified_bytes_to_helper_and_cleans_up(spool, monkeypatch):
    monkeypatch.setattr(api, 'require_account_access', lambda *a: None)
    observed = []
    def call(op, identity, **params):
        assert op == 'db.import'
        path = helper._verified_import(params['path'])
        observed.append(path)
        assert path.parent == spool
        assert path.read_bytes() == b'CREATE TABLE marker (id INT);'
        work = spool.parent / 'root-work'
        work.mkdir(mode=0o700)
        assert helper._copy_import_source(path, work).read_bytes() == path.read_bytes()
        return {'status': 'imported'}
    monkeypatch.setattr(api, 'call_daemon', call)
    result = api.import_database('sample', 'sample_marker', UploadFile(file=io.BytesIO(b'CREATE TABLE marker (id INT);'), filename='input.sql'), object())
    assert result['status'] == 'imported'
    assert not observed[0].exists()


@pytest.mark.parametrize('failure', ['empty', 'symlink', 'hardlink', 'public', 'directory', 'outside', 'missing'])
def test_import_rejects_unsafe_sources(spool, failure, tmp_path):
    source = upload(spool)
    if failure == 'empty': source.write_bytes(b'')
    elif failure == 'symlink':
        target = tmp_path / 'secret'; target.write_text('not SQL')
        source.unlink(); source.symlink_to(target)
    elif failure == 'hardlink': os.link(source, spool / 'second')
    elif failure == 'public': source.chmod(0o644)
    elif failure == 'directory': source.unlink(); source.mkdir()
    elif failure == 'outside': source = tmp_path / 'other.sql'; source.write_text('SQL')
    elif failure == 'missing': source.unlink()
    with pytest.raises(ValidationError): helper._verified_import(str(source))


def test_import_rechecks_descriptor_after_path_validation(spool, tmp_path):
    source = helper._verified_import(str(upload(spool)))
    source.unlink()
    os.mkfifo(source, 0o600)
    with pytest.raises(ValidationError, match='regular file'):
        helper._copy_import_source(source, tmp_path)


def test_unavailable_spool_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'db_path', str(tmp_path / 'missing' / 'panel.db'))
    monkeypatch.setattr(api, 'require_account_access', lambda *a: None)
    with pytest.raises(HTTPException) as error:
        api.import_database('sample', 'sample_marker', UploadFile(file=io.BytesIO(b'SELECT 1;')), object())
    assert error.value.status_code == 503
    assert 'upload storage' in error.value.detail


def test_route_cleans_up_after_helper_failure(spool, monkeypatch):
    monkeypatch.setattr(api, 'require_account_access', lambda *a: None)
    def fail(*a, **k): raise ValidationError('Import rejected')
    monkeypatch.setattr(api, 'call_daemon', fail)
    with pytest.raises(ValidationError):
        api.import_database('sample', 'sample_marker', UploadFile(file=io.BytesIO(b'SELECT 1;')), object())
    assert list(spool.iterdir()) == []
