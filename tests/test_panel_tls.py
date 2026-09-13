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


def test_failed_listener_verification_restores_previous_pair(tmp_path,monkeypatch):
    root=tmp_path/'live';source=root/'panel.example';source.mkdir(parents=True)
    cert,key=pair();(source/'fullchain.pem').write_bytes(cert);(source/'privkey.pem').write_bytes(key)
    destination=tmp_path/'api';destination.mkdir()
    (destination/'panel.crt').write_bytes(b'old certificate');(destination/'panel.key').write_bytes(b'old key')
    monkeypatch.setattr(hook,'LINEAGE_ROOT',root);monkeypatch.setattr(hook,'TLS_DIRECTORY',destination)
    monkeypatch.setattr(hook.grp,'getgrnam',lambda name:SimpleNamespace(gr_gid=os.getgid()))
    monkeypatch.setattr(hook.os,'fchown',lambda *args:None)
    calls=[];monkeypatch.setattr(hook.subprocess,'run',lambda argv,**kwargs:calls.append(argv))
    def fail(*args):raise RuntimeError('listener failed')
    monkeypatch.setattr(hook,'_wait_for_certificate',fail)
    with pytest.raises(RuntimeError,match='listener failed'):hook.deploy('panel.example',source)
    assert (destination/'panel.crt').read_bytes()==b'old certificate'
    assert (destination/'panel.key').read_bytes()==b'old key'
    assert len([call for call in calls if 'restart' in call])==2
    assert not list(destination.glob('.panel-tls-*'))


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
    assert {address[1] for address in connections}=={2222,3333}
