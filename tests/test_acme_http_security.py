import json
import os
import pwd
from pathlib import Path
import shutil
import subprocess
import tempfile

from configobj import ConfigObj
import pytest

from daemon import acme_http as acme
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, Domain


def test_challenge_dispatch_drops_privileges(monkeypatch):
    monkeypatch.setattr(acme, '_site', lambda domain: ('/home/demo', '/home/demo/public_html', 2100, 2101))
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return ProcResult(args, 0, '', '')
    monkeypatch.setattr(acme, 'run', run)
    acme.perform('auth', 'demo.example', 'token', 'token.thumbprint')
    args, kwargs = calls[0]
    assert args[:2] == ['/usr/bin/python3', '-I']
    assert (kwargs['uid'], kwargs['gid'], kwargs['cwd']) == (2100, 2101, '/')
    assert json.loads(kwargs['input_text'])['token'] == 'token'


@pytest.mark.parametrize('token,validation', [('../x', 'x.y'), ('x', 'x\ny'), ('x', 'x' * 1000), ('', 'x.y')])
def test_challenge_rejects_invalid_payload_before_dispatch(monkeypatch, token, validation):
    monkeypatch.setattr(acme, '_site', lambda *a: pytest.fail('Invalid request reached identity lookup'))
    with pytest.raises(ValueError):
        acme.perform('auth', 'demo.example', token, validation)


def test_renewal_migration_preserves_dns_and_foreign_configs(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(acme, 'RENEWAL_DIR', tmp_path)
    original = 'version = 5.0.0\n[renewalparams]\nauthenticator = webroot\naccount = preserved\nwebroot_path = /home/demo/public_html,\n[[webroot_map]]\ndemo.example = /home/demo/public_html\n'
    with write_session() as session:
        owner = Account(username='demo', status='active')
        session.add(owner)
        session.flush()
        for name in ['demo.example', 'dns.example']:
            session.add(Domain(account_id=owner.id, domain=name, docroot='/home/demo/public_html'))
    path = tmp_path / 'demo.example.conf'
    path.write_text(original)
    (tmp_path / 'dns.example.conf').write_text('[renewalparams]\nauthenticator = dns-cloudflare\n')
    (tmp_path / 'foreign.example.conf').write_text(original)
    assert acme.migrate_renewals() == 1
    params = ConfigObj(str(path))['renewalparams']
    assert params['authenticator'] == 'manual'
    assert params['account'] == 'preserved'
    assert 'webroot_map' not in params and 'webroot_path' not in params
    from certbot._internal.renewal import _restore_pref_challs
    assert _restore_pref_challs('pref_challs', params['pref_challs']) == ['http-01']
    assert params['manual_auth_hook'] == acme.hook_command('auth')
    assert (tmp_path / 'demo.example.conf.before-boron-safe-http').read_text() == original
    assert (tmp_path / 'foreign.example.conf').read_text() == original
    assert 'dns-cloudflare' in (tmp_path / 'dns.example.conf').read_text()
    assert acme.migrate_renewals() == 0
    assert path.stat().st_mode & 0o777 == 0o600


def test_site_lookup_checks_identity_and_docroot(isolated_db, monkeypatch):
    with write_session() as session:
        owner = Account(username='demo', status='active', uid=2100, gid=2101)
        session.add(owner)
        session.flush()
        session.add(Domain(account_id=owner.id, domain='demo.example', docroot='/home/demo/public_html'))
    monkeypatch.setattr(acme.pwd, 'getpwnam', lambda name: pwd.struct_passwd((name, 'x', 2100, 2101, '', '/home/demo', '/bin/bash')))
    monkeypatch.setattr(acme.settings, 'home_base', '/home')
    assert acme._site('www.demo.example') == ('/home/demo', '/home/demo/public_html', 2100, 2101)
    monkeypatch.setattr(acme.pwd, 'getpwnam', lambda name: pwd.struct_passwd((name, 'x', 2200, 2101, '', '/home/demo', '/bin/bash')))
    with pytest.raises(ValueError, match='identity'):
        acme._site('demo.example')


@pytest.mark.skipif(os.environ.get('BORON_REAL_UID_TESTS') != '1', reason='Explicit disposable VM UID test')
def test_real_uid_challenge_writer_and_symlink_canary():
    owner = pwd.getpwnam('auditweb')
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='boron-acme-uid-') as work:
        base = Path(work)
        base.chmod(0o755)
        (base / 'scripts').mkdir()
        (base / 'daemon').mkdir()
        shutil.copy(repo / 'scripts/acme_http_file.py', base / 'scripts/acme_http_file.py')
        shutil.copy(repo / 'daemon/safeio.py', base / 'daemon/safeio.py')
        (base / 'daemon/__init__.py').touch()
        home = base / 'home'
        docroot = home / 'public_html'
        docroot.mkdir(parents=True)
        for path in [home, docroot]:
            os.chown(path, owner.pw_uid, owner.pw_gid)
            path.chmod(0o750)
        payload = dict(action='auth', home=str(home), docroot=str(docroot), token='canary', validation='canary.thumbprint')
        def invoke():
            return subprocess.run(['/usr/bin/python3', '-I', str(base / 'scripts/acme_http_file.py')],
                input=json.dumps(payload), text=True, capture_output=True, user=owner.pw_uid,
                group=owner.pw_gid, extra_groups=(), cwd='/', timeout=5)
        assert invoke().returncode == 0
        challenge = docroot / '.well-known/acme-challenge/canary'
        assert challenge.read_text() == payload['validation']
        assert challenge.stat().st_uid == owner.pw_uid
        assert docroot.stat().st_mode & 0o777 == 0o750
        payload['action'] = 'cleanup'
        assert invoke().returncode == 0 and not challenge.exists()
        shutil.rmtree(docroot / '.well-known')
        private = base / 'private'
        private.mkdir(mode=0o700)
        marker = private / 'marker'
        marker.write_text('root protected')
        (docroot / '.well-known').symlink_to(private, target_is_directory=True)
        # Reproduce the old root webroot plugin's write using an inert
        # challenge inside this private synthetic directory only.
        from collections import defaultdict
        from types import SimpleNamespace
        from certbot._internal.plugins.webroot import Authenticator
        private_challenges = private / 'acme-challenge'
        private_challenges.mkdir(mode=0o700)
        class Challenge:
            identifier = SimpleNamespace(value='audit.example')
            chall = SimpleNamespace(encode=lambda name: 'old-root-canary')
            def response_and_validation(self):
                return None, 'inert-old-write'
        plugin = object.__new__(Authenticator)
        plugin.full_roots = {'audit.example': str(docroot / '.well-known/acme-challenge')}
        plugin.performed = defaultdict(set)
        plugin._perform_single(Challenge())
        old = private_challenges / 'old-root-canary'
        assert old.read_text() == 'inert-old-write' and old.stat().st_uid == 0
        old.unlink()
        payload['action'] = 'auth'
        assert invoke().returncode != 0
        assert list(private_challenges.iterdir()) == []
        assert set(private.iterdir()) == {marker, private_challenges}
        assert marker.read_text() == 'root protected'
