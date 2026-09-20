import datetime as dt
import os
from types import SimpleNamespace
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from scripts import panel_ssl_deploy as hook
from daemon import ols
from shared.config import settings


def pair(host='panel.example',expired=False):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,host)])
    now=dt.datetime.now(dt.timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(days=2))
        .not_valid_after(now+dt.timedelta(days=-1 if expired else 30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]),critical=False).sign(key,hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM),key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())


def test_certificate_hostname_validity_and_key_pair():
    cert,key=pair()
    hook.validate_pair('panel.example',cert,key)
    with pytest.raises(ValueError,match='cover'):hook.validate_pair('wrong.example',cert,key)
    with pytest.raises(ValueError,match='match'):hook.validate_pair('panel.example',cert,pair()[1])
    with pytest.raises(ValueError,match='valid'):hook.validate_pair('panel.example',*pair(expired=True))


@pytest.mark.parametrize('failed_listener',['panel','ftp'])
def test_failed_listener_verification_restores_previous_pair(tmp_path,monkeypatch,failed_listener):
    root=tmp_path/'live';source=root/'panel.example';source.mkdir(parents=True)
    cert,key=pair();(source/'fullchain.pem').write_bytes(cert);(source/'privkey.pem').write_bytes(key)
    destination=tmp_path/'api';destination.mkdir()
    (destination/'panel.crt').write_bytes(b'old certificate');(destination/'panel.key').write_bytes(b'old key')
    ols_destination=tmp_path/'ols';ols_destination.mkdir()
    (ols_destination/'webadmin.crt').write_bytes(b'old OLS certificate');(ols_destination/'webadmin.key').write_bytes(b'old OLS key')
    ftp_destination=tmp_path/'pure-ftpd.pem';ftp_destination.write_bytes(b'old FTP certificate')
    monkeypatch.setattr(hook,'LINEAGE_ROOT',root);monkeypatch.setattr(hook,'TLS_DIRECTORY',destination)
    monkeypatch.setattr(hook,'OLS_ADMIN_DIRECTORY',ols_destination)
    monkeypatch.setattr(hook,'FTP_CERT_PATH',ftp_destination)
    monkeypatch.setattr(hook.grp,'getgrnam',lambda name:SimpleNamespace(gr_gid=os.getgid()))
    monkeypatch.setattr(hook.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    monkeypatch.setattr(hook.os,'fchown',lambda *args:None)
    calls=[];monkeypatch.setattr(hook.subprocess,'run',lambda argv,**kwargs:calls.append(argv))
    def fail(*args):raise RuntimeError('listener failed')
    monkeypatch.setattr(hook,'_wait_for_certificate',fail if failed_listener=='panel' else lambda *_:None)
    monkeypatch.setattr(hook,'_wait_for_ftps_certificate',fail if failed_listener=='ftp' else lambda *_:None)
    with pytest.raises(RuntimeError,match='listener failed'):hook.deploy('panel.example',source)
    assert (destination/'panel.crt').read_bytes()==b'old certificate'
    assert (destination/'panel.key').read_bytes()==b'old key'
    assert (ols_destination/'webadmin.crt').read_bytes()==b'old OLS certificate'
    assert (ols_destination/'webadmin.key').read_bytes()==b'old OLS key'
    assert ftp_destination.read_bytes()==b'old FTP certificate'
    assert [call for call in calls if 'restart' in call]==[
        ['systemctl','restart','boron-api.service'],
        ['systemctl','restart','lshttpd.service'],
        ['systemctl','restart','pure-ftpd.service'],
        ['systemctl','restart','boron-api.service'],
        ['systemctl','restart','lshttpd.service'],
        ['systemctl','restart','pure-ftpd.service'],
    ]
    assert not list(destination.glob('.panel-tls-*'))
    assert not list(ols_destination.glob('.panel-tls-*'))


def test_panel_acme_route_is_static_and_http_only(monkeypatch):
    monkeypatch.setattr(settings,'panel_hostname','panel.example')
    monkeypatch.setattr(settings,'panel_acme_webroot','/var/www/panel-acme')
    rendered=ols.render_httpd_config([],[],cloudflare_ranges=[])
    assert 'virtualHost boron_panel_acme{' in rendered
    http,https=rendered.split('listener HTTPS{')
    assert 'map                      boron_panel_acme panel.example' in http
    assert 'boron_panel_acme' not in https


def test_issue_uses_stable_renewal_hook_and_dedicated_webroot(monkeypatch):
    from daemon import panel_tls,procutil
    monkeypatch.setattr(panel_tls,'bootstrap_challenge',lambda:{'hostname':'panel.example','webroot':'/var/www/panel-acme'})
    calls=[]
    monkeypatch.setattr(procutil,'run',lambda argv,**kw:(calls.append(argv) or SimpleNamespace(raise_if_failed=lambda message:None)))
    panel_tls.issue_certificate('admin@example.com')
    assert '--keep-until-expiring' in calls[0]
    assert calls[0][calls[0].index('-w')+1]=='/var/www/panel-acme'
    assert '/opt/boron/scripts/panel_ssl_deploy.py' in calls[0][-1]
    assert '--hostname panel.example' in calls[0][-1]


def test_certificate_verification_checks_both_ports(tmp_path,monkeypatch):
    from contextlib import nullcontext
    cert,_=pair()
    expected=x509.load_pem_x509_certificate(cert).public_bytes(serialization.Encoding.DER)
    path=tmp_path/'boron.toml';path.write_text('api_bind_port=2222\napi_customer_port=3333\n')
    monkeypatch.setattr(hook,'CONFIG_PATH',path)
    connections=[]
    monkeypatch.setattr(hook.socket,'create_connection',lambda address,timeout:(connections.append(address) or nullcontext(object())))
    context=SimpleNamespace(wrap_socket=lambda connection,server_hostname:nullcontext(SimpleNamespace(getpeercert=lambda binary_form:expected)))
    monkeypatch.setattr(hook.ssl,'SSLContext',lambda protocol:context)
    hook._wait_for_certificate('panel.example',cert)
    assert {address[1] for address in connections}=={2222,3333,7080}


@pytest.mark.parametrize('verify_fails',[False,True])
def test_ftps_reconciliation_is_atomic_and_recovers(tmp_path,monkeypatch,verify_fails):
    from scripts import reconcile_ftps_tls
    cert,key=pair()
    panel=tmp_path/'api';panel.mkdir()
    (panel/'panel.crt').write_bytes(cert);(panel/'panel.key').write_bytes(key)
    ftp=tmp_path/'pure-ftpd.pem';ftp.write_bytes(b'old FTP certificate')
    monkeypatch.setattr(hook,'TLS_DIRECTORY',panel)
    monkeypatch.setattr(hook,'FTP_CERT_PATH',ftp)
    monkeypatch.setattr(hook.os,'fchown',lambda *args:None)
    calls=[]
    monkeypatch.setattr(reconcile_ftps_tls.subprocess,'run',lambda argv,**kwargs:calls.append(argv))
    if verify_fails:
        monkeypatch.setattr(hook,'_wait_for_ftps_certificate',lambda *_:(_ for _ in ()).throw(RuntimeError('bad FTP cert')))
        with pytest.raises(RuntimeError,match='bad FTP cert'):
            reconcile_ftps_tls.reconcile('panel.example')
        assert ftp.read_bytes()==b'old FTP certificate'
        assert [call for call in calls if 'restart' in call]==[
            ['systemctl','restart','pure-ftpd.service'],
            ['systemctl','restart','pure-ftpd.service'],
        ]
    else:
        monkeypatch.setattr(hook,'_wait_for_ftps_certificate',lambda *_:None)
        assert reconcile_ftps_tls.reconcile('panel.example') is True
        assert ftp.read_bytes()==cert+b'\n'+key
        assert reconcile_ftps_tls.reconcile('panel.example') is False
        assert [call for call in calls if 'restart' in call]==[['systemctl','restart','pure-ftpd.service']]


def test_challenge_owner_is_dedicated_non_login_service(monkeypatch):
    from daemon import panel_tls,procutil
    owner=SimpleNamespace(pw_uid=990,pw_gid=990,pw_shell='/usr/sbin/nologin')
    calls=[]
    def lookup(name):
        assert name=='boron-acme'
        if not calls:raise KeyError(name)
        return owner
    monkeypatch.setattr(panel_tls.pwd,'getpwnam',lookup)
    monkeypatch.setattr(procutil,'run',lambda args,**kwargs:calls.append(args))
    assert panel_tls._challenge_owner() is owner
    assert '--no-create-home' in calls[0] and '--system' in calls[0]
    assert panel_tls._challenge_owner() is owner
    assert len(calls)==1


def test_challenge_owner_rejects_root_or_login_shell(monkeypatch):
    from daemon import panel_tls
    from shared.validation import ValidationError
    for uid,gid,shell in ((0,0,'/usr/sbin/nologin'),(990,990,'/bin/bash')):
        monkeypatch.setattr(panel_tls.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=uid,pw_gid=gid,pw_shell=shell))
        with pytest.raises(ValidationError):panel_tls._challenge_owner()
