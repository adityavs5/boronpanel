"""Channel selection never turns a branch into an unsigned installation source."""
import json

import httpx
import pytest

from daemon import updates, panel_config
from shared.config import settings, settings_from_mapping, ConfigError
from shared.db import write_session
from shared.models import UpdateJob, UpdateState
from shared.release_metadata import validate_release_metadata, ReleaseMetadataError
from shared.validation import ValidationError
from tests.test_updates import _release_json


@pytest.fixture(autouse=True)
def channel_defaults(monkeypatch):
    monkeypatch.setattr(settings, 'update_channel', 'stable')
    monkeypatch.setattr(settings, 'update_github_repo', 'acme/boron')
    monkeypatch.setattr(updates, 'load_test_update_override', lambda: None)


def release(version, channel, **extra):
    return {**_release_json(version), 'prerelease': channel != 'stable',
            'target_commitish': updates.CHANNEL_BRANCHES[channel], **extra}


@pytest.mark.parametrize('channel', ['beta', 'development'])
def test_preview_discovery_selects_only_matching_branch(monkeypatch, channel):
    monkeypatch.setattr(settings, 'update_channel', channel)
    releases = [release('3.9.0', 'stable'), release('3.6.0', 'beta'),
                release('3.7.0', 'development'), release('9.0.0', channel, draft=True)]
    monkeypatch.setattr(updates, '_transport', httpx.MockTransport(
        lambda request: httpx.Response(200, json=releases)))
    result = updates._fetch_release()
    assert result['version'] == {'beta': '3.6.0', 'development': '3.7.0'}[channel]
    assert result['channel'] == channel


def test_preview_empty_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(settings, 'update_channel', 'beta')
    monkeypatch.setattr(updates, '_transport', httpx.MockTransport(
        lambda request: httpx.Response(200, json=[release('3.9.0', 'stable')])))
    with pytest.raises(ValidationError, match='No beta release'):
        updates._fetch_release()


@pytest.mark.parametrize('channel,other', [('stable', 'beta'), ('beta', 'development'), ('development', 'stable')])
def test_exact_tag_cannot_cross_tiers(monkeypatch, channel, other):
    monkeypatch.setattr(settings, 'update_channel', channel)
    monkeypatch.setattr(updates, '_transport', httpx.MockTransport(
        lambda request: httpx.Response(200, json=release('3.5.0', other))))
    with pytest.raises(ValidationError):
        updates._fetch_release('v3.5.0')


def test_preview_requires_signed_metadata():
    with pytest.raises(ValidationError, match='require signed channel metadata'):
        updates._fetch_release_metadata({'channel': 'beta'})


def test_metadata_channel_defaults_and_validation():
    data = {'format': 1, 'minimum_source_version': '3.1.0'}
    assert validate_release_metadata(data)['channel'] == 'stable'
    assert validate_release_metadata({**data, 'channel': 'beta'})['channel'] == 'beta'
    with pytest.raises(ReleaseMetadataError):
        validate_release_metadata({**data, 'channel': 'arbitrary'})
    with pytest.raises(ConfigError):
        settings_from_mapping({'update_channel': 'arbitrary'})


def test_set_channel_persists_and_clears_cache(isolated_db, tmp_path, monkeypatch):
    config = tmp_path / 'boron.toml'
    config.write_text('update_github_repo = "acme/boron"\n')
    monkeypatch.setattr(updates, 'CONFIG_PATH', config)
    monkeypatch.setattr(settings, 'backup_dir', str(tmp_path / 'backups'))
    with write_session() as session:
        session.add(UpdateState(id=1, latest_version='9.0.0', release_metadata={'channel': 'stable'}))
    assert updates.set_channel({'channel': 'beta'}) == {'channel': 'beta', 'branch': 'beta'}
    assert settings.update_channel == 'beta'
    assert 'update_channel = "beta"' in config.read_text()
    with write_session() as session:
        state = session.get(UpdateState, 1)
        assert state.latest_version is None
        assert state.checked_at is None
        assert state.release_metadata == {}


def test_set_channel_blocked_during_update(isolated_db, monkeypatch):
    with write_session() as session:
        session.add(UpdateJob(kind='update', status='pending', from_version='3.1.0', to_version='3.2.0', initiated_by='test-admin'))
    monkeypatch.setattr(panel_config, 'apply_setup_values', lambda *a, **k: pytest.fail('must not write'))
    with pytest.raises(ValidationError, match='Wait for'):
        updates.set_channel({'channel': 'beta'})


def test_set_channel_rejects_unknown_tier():
    with pytest.raises(ValidationError):
        updates.set_channel({'channel': '../../malicious'})


def test_signed_metadata_must_match_selected_tier(monkeypatch, tmp_path):
    data = {'format': 1, 'minimum_source_version': '3.1.0', 'channel': 'stable'}
    def download(info, url, dest, maximum):
        from pathlib import Path
        Path(dest).write_bytes(json.dumps(data).encode() if url == 'metadata' else b'x' * 64)
    monkeypatch.setattr(updates, '_download_release_asset', download)
    monkeypatch.setattr(updates, '_verify_release_signature', lambda *a: None)
    with pytest.raises(ValidationError, match='Signed release channel'):
        updates._fetch_release_metadata({'version': '3.2.0', 'channel': 'beta',
                                        'metadata_url': 'metadata', 'metadata_signature_url': 'signature'})


def test_cached_stable_result_is_not_used_after_external_tier_change(isolated_db, monkeypatch):
    from shared.models import utcnow
    with write_session() as session:
        session.add(UpdateState(id=1, latest_version='9.0.0', checked_at=utcnow(),
                                release_metadata={'channel': 'stable'}))
    monkeypatch.setattr(settings, 'update_channel', 'beta')
    calls = []
    def unavailable(*a, **k):
        calls.append(True)
        raise ValidationError('No beta release has been published')
    monkeypatch.setattr(updates, '_fetch_release', unavailable)
    result = updates.check()
    assert result['channel'] == 'beta'
    assert result['latest_version'] is None
    assert result['update_available'] is False
    assert 'No beta' in result['error']
    updates.check()
    assert len(calls) == 1  # failed lookups are cached within the same tier too


@pytest.mark.parametrize('channel', ['beta', 'development'])
def test_empty_tier_is_informational_and_cached(isolated_db, monkeypatch, channel):
    monkeypatch.setattr(settings, 'update_channel', channel)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=[release('3.9.0', 'stable')])
    monkeypatch.setattr(updates, '_transport', httpx.MockTransport(respond))
    # Old cached errors are refreshed automatically after deployment.
    from shared.models import utcnow
    with write_session() as session:
        session.add(UpdateState(id=1, checked_at=utcnow(), release_metadata={'channel': channel},
                                last_error=f'No {channel} release has been published in acme/boron'))
    result = updates.check()
    assert result['error'] is None
    assert result['release_availability'] == 'unpublished'
    assert result['latest_version'] is None
    assert result['update_available'] is False
    assert result['channel'] == channel
    assert updates.check()['release_availability'] == 'unpublished'
    assert len(calls) == 1
    monkeypatch.setattr(updates, '_transport', httpx.MockTransport(
        lambda request: httpx.Response(200, json=[release('3.9.0', channel)])))
    monkeypatch.setattr(updates, '_fetch_release_metadata', lambda info: {'channel': channel})
    result = updates.check({'force': True})
    assert result['latest_version'] == '3.9.0'
    assert result['release_availability'] is None


def test_preview_network_failure_remains_error(isolated_db, monkeypatch):
    monkeypatch.setattr(settings, 'update_channel', 'development')
    monkeypatch.setattr(updates, '_transport', httpx.MockTransport(
        lambda request: httpx.Response(403, json={'message': 'rate limited'})))
    result = updates.check({'force': True})
    assert result['error']
    assert result['release_availability'] is None
