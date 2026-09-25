import os
from pathlib import Path
import shutil
from dataclasses import replace
import pytest
from daemon import snapshot_storage as storage


@pytest.fixture
def repo(tmp_path):
    if not shutil.which('restic'): pytest.skip('restic required for storage integration')
    password=tmp_path/'password';password.write_text('test-only-encryption-key');password.chmod(0o600)
    return storage.Repository(kind='local',path=str(tmp_path/'repository'),namespace='test-panel-0001',
        password_file=str(password),cache_dir=str(tmp_path/'cache'))


def exercise_repository(repo, tmp_path):
    source=tmp_path/'source';source.mkdir()
    payload=os.urandom(3*1024*1024)
    (source/'large.bin').write_bytes(payload)
    (source/'site.txt').write_text('original content')
    (source/'exclude.txt').write_text('must not be backed up')
    (source/'outside-link').symlink_to('/etc/shadow')
    storage.initialize(repo)
    first=storage.backup(repo,1,[str(source)],policy_id=1,excludes=['exclude.txt'])
    second=storage.backup(repo,1,[str(source)],policy_id=1,excludes=['exclude.txt'])
    assert second['files_unmodified'] >= 2
    assert second['data_added'] < first['data_added']/10
    assert len(storage.snapshots(repo,1))==2
    assert storage.snapshots(repo,2)==[]
    with pytest.raises(Exception,match='not found for this account'):
        storage.restore_to(repo,2,first['snapshot_id'],str(tmp_path/'forbidden'))
    (source/'site.txt').write_text('modified content')
    third=storage.backup(repo,1,[str(source)],policy_id=1,excludes=['exclude.txt'])
    assert third['files_changed'] >= 1
    assert third['data_added'] < first['data_added']/10
    assert storage.snapshots(replace(repo,namespace='other-panel-0002'),1)==[]
    with pytest.raises(Exception,match='empty directory'):
        storage.restore_to(repo,1,first['snapshot_id'],str(source))
    restored=storage.restore_to(repo,1,first['snapshot_id'],str(tmp_path/'restore')) / str(source).lstrip('/')
    assert (restored/'large.bin').read_bytes()==payload
    assert (restored/'site.txt').read_text()=='original content'
    assert not (restored/'exclude.txt').exists()
    assert (restored/'outside-link').is_symlink()
    assert (source/'site.txt').read_text()=='modified content'
    full=storage.backup(repo,1,[str(source)],policy_id=1,excludes=['exclude.txt'],full_scan=True)
    assert full['files_new'] >= 2
    assert full['data_added'] < first['data_added']/10
    latest=storage.restore_to(repo,1,full['snapshot_id'],str(tmp_path/'restore-latest'))/str(source).lstrip('/')
    assert (latest/'site.txt').read_text()=='modified content'
    listed=storage.entries(repo,1,first['snapshot_id'],str(source))
    assert any(r['path']==str(source/'site.txt') for r in listed)
    partial=storage.restore_to(repo,1,first['snapshot_id'],str(tmp_path/'partial'),selected_paths=[str(source/'site.txt')])/str(source).lstrip('/')
    assert (partial/'site.txt').read_text()=='original content'
    assert not (partial/'large.bin').exists()
    storage.check(repo)
    return first,second,third


def test_local_incremental_filter_and_verified_restore(repo,tmp_path):
    exercise_repository(repo,tmp_path)


def test_landlock_sandbox_blocks_swapped_symlink_ancestor(repo, tmp_path):
    home = tmp_path / 'home' / 'alpha'
    selected = home / 'sub' / 'deep'
    selected.mkdir(parents=True)
    (selected / 'safe.txt').write_text('safe content')
    storage.initialize(repo)

    first = storage.backup(repo, 1, [str(selected)], sandbox_roots=[str(home)])
    restored = storage.restore_to(repo, 1, first['snapshot_id'], str(tmp_path / 'restore-safe'))
    assert (restored / str(selected / 'safe.txt').lstrip('/')).read_text() == 'safe content'

    assert selected.resolve().is_relative_to(home)
    shutil.rmtree(home / 'sub')
    protected = tmp_path / 'protected'
    (protected / 'deep').mkdir(parents=True)
    (protected / 'deep' / 'root-secret.txt').write_text('SECRET_CANARY')
    (home / 'sub').symlink_to(protected, target_is_directory=True)

    with pytest.raises(storage.SnapshotStorageError):
        storage.backup(repo, 1, [str(selected)], sandbox_roots=[str(home)])
    snapshots = storage.snapshots(repo, 1)
    latest = max(snapshots, key=lambda item: item['time'])
    raced = storage.restore_to(repo, 1, latest['id'], str(tmp_path / 'restore-raced'))
    assert not (raced / str(selected / 'root-secret.txt').lstrip('/')).exists()


def test_thousand_mailbox_paths_fit_safety_backup_and_use_one_listing(repo, tmp_path, monkeypatch):
    source = tmp_path / 'mail'
    source.mkdir()
    paths = []
    for index in range(1000):
        mailbox = source / str(index)
        mailbox.mkdir()
        (mailbox / 'message').write_text(str(index))
        paths.append(str(mailbox))
    manifest = source / 'mail-safety.json'
    manifest.write_text('{}')
    storage.initialize(repo)
    result = storage.backup(repo, 1, paths + [str(manifest)], recovery_operation='a'*32)
    commands = []
    execute = storage._execute
    def recorded(repository, args, **kwargs):
        commands.append(args)
        return execute(repository, args, **kwargs)
    monkeypatch.setattr(storage, '_execute', recorded)
    data = storage.restore_to(repo, 1, result['snapshot_id'], str(tmp_path / 'large-restore'), selected_paths=paths)
    assert sum(args[0] == 'ls' for args in commands) == 1
    for index, path in enumerate(paths):
        assert (data / path.lstrip('/') / 'message').read_text() == str(index)


def test_rejects_unsafe_credentials_and_ssh_inputs(repo,tmp_path):
    Path(repo.password_file).chmod(0o644)
    with pytest.raises(Exception,match='private regular files'):repo.arguments()
    with pytest.raises(Exception,match='hostname'):
        replace(repo,kind='ssh',ssh_host='-oProxyCommand=evil',ssh_user='backup')
    with pytest.raises(Exception,match='absolute path'):
        replace(repo,path='/tmp/../etc')
    with pytest.raises(Exception,match='identifier'):
        storage.owned_snapshot(repo,1,'latest')


def test_ssh_arguments_pin_host_and_disable_ambient_credentials(repo,tmp_path):
    for name in ('key','known_hosts'):
        p=tmp_path/name;p.write_text('test');p.chmod(0o600)
    spec=replace(repo,kind='ssh',ssh_host='127.0.0.1',ssh_port=22222,ssh_user='backup',
        ssh_key_file=str(tmp_path/'key'),ssh_known_hosts_file=str(tmp_path/'known_hosts'))
    args=spec.arguments()
    command=next(a for a in args if a.startswith('sftp.command='))
    assert 'StrictHostKeyChecking=yes' in command
    assert 'IdentityAgent=none' in command
    assert '-F /dev/null' in command
    assert 'test-only-encryption-key' not in ' '.join(args)


def test_ssh_password_uses_askpass_environment_not_arguments(repo,tmp_path,monkeypatch):
    known=tmp_path/'known_hosts';known.write_text('host key');known.chmod(0o600)
    askpass=tmp_path/'askpass';askpass.write_text('#!/bin/sh\nexit 1\n');askpass.chmod(0o700)
    spec=replace(repo,kind='sftp',ssh_host='backup.example.test',ssh_user='boron',
        ssh_auth='password',ssh_password='ONLY-IN-ENV',ssh_askpass_file=str(askpass),
        ssh_known_hosts_file=str(known))
    args=spec.arguments();assert 'ONLY-IN-ENV' not in ' '.join(args)
    command=next(a for a in args if a.startswith('sftp.command='))
    assert 'PreferredAuthentications=password' in command and 'PubkeyAuthentication=no' in command
    captured={}
    def fake_run(command,**kwargs):
        captured.update(command=command,env=kwargs['env'])
        return type('Result',(),{'ok':True,'stdout':'','stderr':''})()
    monkeypatch.setattr(storage,'run',fake_run);storage._execute(spec,['check'])
    assert captured['env']['BORON_SSH_PASSWORD']=='ONLY-IN-ENV'
    assert 'ONLY-IN-ENV' not in ' '.join(captured['command'])


def test_drive_uses_private_rclone_config(repo,tmp_path,monkeypatch):
    config=tmp_path/'rclone.conf';config.write_text('[boron_drive]\ntype = drive\ntoken = private\n');config.chmod(0o600)
    spec=replace(repo,kind='drive',path='Boron backups',rclone_config_file=str(config),rclone_remote_name='boron_drive')
    assert 'rclone:boron_drive:Boron backups' in spec.arguments()
    captured={}
    def fake_run(command,**kwargs):
        captured.update(command=command,env=kwargs['env'])
        return type('Result',(),{'ok':True,'stdout':'','stderr':''})()
    monkeypatch.setattr(storage,'run',fake_run);storage._execute(spec,['check'])
    assert captured['env']['RCLONE_CONFIG']==str(config)
    assert 'private' not in ' '.join(captured['command'])


def test_s3_arguments_and_environment_keep_credentials_off_command_line(repo, monkeypatch):
    spec = replace(repo, kind='s3', path='daily/site-a',
        s3_endpoint='https://objects.example.test', s3_bucket='hosting-backups', s3_region='eu-test-1',
        s3_credentials={'access_key':'ACCESS-ONLY-IN-ENV','secret_key':'SECRET-ONLY-IN-ENV','session_token':'TOKEN-ONLY-IN-ENV'})
    args = spec.arguments()
    assert 's3:https://objects.example.test/hosting-backups/daily/site-a' in args
    assert not any(secret in ' '.join(args) for secret in spec.s3_credentials.values())
    captured = {}
    def fake_run(command, **kwargs):
        captured.update(command=command, env=kwargs['env'])
        return type('Result', (), {'ok':True, 'stdout':'', 'stderr':''})()
    monkeypatch.setattr(storage, 'run', fake_run)
    storage._execute(spec, ['check'])
    assert captured['env']['AWS_ACCESS_KEY_ID'] == 'ACCESS-ONLY-IN-ENV'
    assert captured['env']['AWS_SECRET_ACCESS_KEY'] == 'SECRET-ONLY-IN-ENV'
    assert captured['env']['AWS_SESSION_TOKEN'] == 'TOKEN-ONLY-IN-ENV'
    assert captured['env']['AWS_REGION'] == 'eu-test-1'


@pytest.mark.parametrize('changes,match', [
    ({'s3_endpoint':'http://objects.example.test'}, 'HTTPS'),
    ({'s3_endpoint':'https://user:pass@objects.example.test'}, 'HTTPS'),
    ({'s3_bucket':'Bad_Bucket'}, 'bucket'),
    ({'path':'../escape'}, 'prefix'),
    ({'s3_credentials':{}}, 'access key'),
])
def test_s3_rejects_unsafe_configuration(repo, changes, match):
    values = dict(kind='s3', path='boron', s3_endpoint='https://objects.example.test',
        s3_bucket='hosting-backups', s3_region='us-east-1',
        s3_credentials={'access_key':'access','secret_key':'secret'})
    values.update(changes)
    with pytest.raises(Exception, match=match):
        replace(repo, **values)


@pytest.fixture
def ssh_environment(repo,tmp_path):
    import signal
    import socket
    import subprocess
    import time
    import tempfile
    if os.geteuid()!=0 or not Path('/usr/sbin/sshd').exists():
        pytest.skip('isolated SSH server test requires root and sshd')
    for name in ('host_key','client_key'):
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(tmp_path/name)],check=True)
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    host_key=(tmp_path/'host_key.pub').read_text().split()
    known=tmp_path/'known_hosts'
    known.write_text(f'[127.0.0.1]:{port} {host_key[0]} {host_key[1]}\n');known.chmod(0o600)
    auth_directory=tempfile.TemporaryDirectory(prefix='boron-ssh-proof-',dir='/root')
    authorized=Path(auth_directory.name)/'authorized_keys'
    authorized.write_text((tmp_path/'client_key.pub').read_text());authorized.chmod(0o600)
    config=tmp_path/'sshd_config'
    config.write_text(f'''Port {port}
ListenAddress 127.0.0.1
HostKey {tmp_path}/host_key
PidFile {tmp_path}/sshd.pid
AuthorizedKeysFile {authorized}
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
UsePAM no
AllowUsers root
Subsystem sftp internal-sftp
''')
    logfile=open(tmp_path/'sshd.log','w')
    process=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(config)],stderr=logfile,start_new_session=True)
    try:
        for _ in range(100):
            if process.poll() is not None:pytest.fail((tmp_path/'sshd.log').read_text())
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):break
            except OSError:time.sleep(.05)
        else:pytest.fail('Isolated SSH server did not start')
        spec = replace(repo,kind='ssh',ssh_host='127.0.0.1',ssh_port=port,ssh_user='root',
            ssh_key_file=str(tmp_path/'client_key'),ssh_known_hosts_file=str(known))
        yield spec, authorized
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            process.wait(timeout=10)
        logfile.close()
        auth_directory.cleanup()


@pytest.fixture
def ssh_repo(ssh_environment):
    return ssh_environment[0]


def test_ssh_incremental_filter_and_verified_restore(ssh_repo,tmp_path):
    exercise_repository(ssh_repo,tmp_path)


def test_ssh_refuses_unrecognized_host_key(ssh_repo):
    import shlex
    import subprocess
    Path(ssh_repo.ssh_known_hosts_file).write_text('')
    command = next(arg.removeprefix('sftp.command=') for arg in ssh_repo.arguments()
                   if arg.startswith('sftp.command='))
    rejected = subprocess.run(shlex.split(command), input='', capture_output=True,
                              text=True, timeout=20, env={**os.environ, 'LC_ALL': 'C'})
    assert rejected.returncode != 0
    assert 'Host key verification failed' in rejected.stderr
    # Restic versions may replace SSH's stderr with a generic SFTP error.
    # Verify the exact transport's cause above, and the storage refusal here.
    with pytest.raises(storage.SnapshotStorageError):
        storage.initialize(ssh_repo)
    assert not (Path(ssh_repo.path) / 'config').exists()


def test_retention_cannot_remove_another_account_snapshot(repo,tmp_path):
    source=tmp_path/'file';source.write_text('backup content')
    storage.initialize(repo)
    first=storage.backup(repo,1,[str(source)])
    other=storage.backup(repo,2,[str(source)])
    with pytest.raises(Exception,match='not found for this account'):
        storage.forget(repo,1,[first['snapshot_id'],other['snapshot_id']])
    assert len(storage.snapshots(repo,1))==1
    storage.forget(repo,1,[first['snapshot_id']],prune=True)
    assert storage.snapshots(repo,1)==[]
    restored=storage.restore_to(repo,2,other['snapshot_id'],str(tmp_path/'restore'))
    assert (restored/str(source).lstrip('/')).read_text()=='backup content'


def test_wrong_encryption_key_cannot_read_snapshots(repo,tmp_path):
    storage.initialize(repo)
    wrong=tmp_path/'wrong-key';wrong.write_text('different-key');wrong.chmod(0o600)
    with pytest.raises(storage.SnapshotStorageError,match='wrong password|no key'):
        storage.snapshots(replace(repo,password_file=str(wrong)),1)


def test_repository_and_encryption_key_are_excluded_from_parent_backup(repo,tmp_path):
    (tmp_path/'wanted.txt').write_text('keep me')
    storage.initialize(repo)
    snapshot=storage.backup(repo,1,[str(tmp_path)])
    target=storage.restore_to(repo,1,snapshot['snapshot_id'],str(tmp_path/'restore'))/str(tmp_path).lstrip('/')
    assert (target/'wanted.txt').read_text()=='keep me'
    assert not (target/'password').exists()
    assert not (target/'repository').exists()
    assert not (target/'cache').exists()


def test_granular_restore_uses_literal_paths_and_rejects_missing_paths(repo,tmp_path):
    source=tmp_path/'source';source.mkdir()
    selected=source/r'file[1]*?\name.txt'
    selected.write_text('literal filename')
    (source/'file1-othername.txt').write_text('do not restore')
    folder=source/'folder';folder.mkdir();(folder/'nested.txt').write_text('nested content')
    storage.initialize(repo)
    snapshot=storage.backup(repo,1,[str(source)])['snapshot_id']
    restored=storage.restore_to(repo,1,snapshot,str(tmp_path/'literal'),selected_paths=[str(selected)])
    assert (restored/str(selected).lstrip('/')).read_text()=='literal filename'
    assert not (restored/str(source/'file1-othername.txt').lstrip('/')).exists()
    restored=storage.restore_to(repo,1,snapshot,str(tmp_path/'folder-restore'),selected_paths=[str(folder)])
    assert (restored/str(folder/'nested.txt').lstrip('/')).read_text()=='nested content'
    for path,message in [(source/'absent','not found'),(tmp_path/'outside','outside')]:
        with pytest.raises(Exception,match=message):
            storage.restore_to(repo,1,snapshot,str(tmp_path/'invalid'),selected_paths=[str(path)])
        assert not (tmp_path/'invalid').exists()
