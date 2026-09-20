#!/usr/bin/env python3
"""Certbot deploy hook for the panel's own TLS listener."""
import argparse
import datetime as dt
import ftplib
import grp
import pwd
import os
from pathlib import Path
import re
import subprocess
import socket
import ssl
import time
import tomllib
import tempfile
from cryptography import x509
from cryptography.hazmat.primitives import serialization


LINEAGE_ROOT=Path('/etc/letsencrypt/live')
TLS_DIRECTORY=Path('/etc/boron/ssl/api')
OLS_ADMIN_DIRECTORY=Path('/usr/local/lsws/admin/conf')
FTP_CERT_PATH=Path('/etc/ssl/private/pure-ftpd.pem')
CONFIG_PATH=Path('/etc/boron/boron.toml')

def validate_pair(hostname,certificate,key):
    if not hostname or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in hostname.split('.')):
        raise ValueError('Invalid panel hostname')
    cert=x509.load_pem_x509_certificate(certificate)
    private=serialization.load_pem_private_key(key,password=None)
    names=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    if hostname not in names:raise ValueError('Certificate does not cover the panel hostname')
    now=dt.datetime.now(dt.timezone.utc)
    if not cert.not_valid_before_utc<=now<cert.not_valid_after_utc:raise ValueError('Certificate is not currently valid')
    def public_bytes(public):return public.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
    if public_bytes(cert.public_key())!=public_bytes(private.public_key()):raise ValueError('Certificate and private key do not match')


def _replace(path,content,uid,gid,mode=0o640):
    fd,temporary=tempfile.mkstemp(prefix='.panel-tls-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(content);handle.flush();os.fsync(handle.fileno())
            os.fchown(handle.fileno(),uid,gid);os.fchmod(handle.fileno(),mode)
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)


def _wait_for_certificate(hostname,certificate):
    with CONFIG_PATH.open('rb') as handle:config=tomllib.load(handle)
    admin=int(config.get('api_bind_port',2222))
    pending={admin,int(config.get('api_customer_port') or admin),7080}
    expected=x509.load_pem_x509_certificate(certificate).public_bytes(serialization.Encoding.DER)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        for port in tuple(pending):
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=2) as connection:
                    with context.wrap_socket(connection,server_hostname=hostname) as tls:
                        if tls.getpeercert(binary_form=True)==expected:pending.remove(port)
            except (OSError,ssl.SSLError):pass
        if not pending:return
        time.sleep(.25)
    raise RuntimeError('Panel did not serve the newly installed certificate')


def _wait_for_ftps_certificate(hostname,certificate):
    """Verify the STARTTLS certificate without sending FTP credentials."""
    expected=x509.load_pem_x509_certificate(certificate).public_bytes(serialization.Encoding.DER)
    context=ssl._create_unverified_context()
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        try:
            ftp=ftplib.FTP_TLS(context=context,timeout=3)
            try:
                ftp.connect('127.0.0.1',21)
                ftp.auth()
                if ftp.sock.getpeercert(binary_form=True)==expected:
                    return
            finally:
                ftp.close()
        except (OSError,ftplib.Error):
            pass
        time.sleep(.25)
    raise RuntimeError('FTP did not serve the newly installed certificate')


def deploy(hostname,lineage):
    lineage=Path(lineage)
    expected=LINEAGE_ROOT/hostname
    if lineage!=expected:raise ValueError('Unexpected panel certificate lineage')
    certificate=(lineage/'fullchain.pem').read_bytes()
    key=(lineage/'privkey.pem').read_bytes()
    validate_pair(hostname,certificate,key)
    destination=TLS_DIRECTORY
    gid=grp.getgrnam('boron-api').gr_gid
    lsadm=pwd.getpwnam('lsadm')
    certpath=destination/'panel.crt';keypath=destination/'panel.key'
    olscert=OLS_ADMIN_DIRECTORY/'webadmin.crt';olskey=OLS_ADMIN_DIRECTORY/'webadmin.key'
    ftpfile=FTP_CERT_PATH
    oldcert=certpath.read_bytes();oldkey=keypath.read_bytes()
    oldolscert=olscert.read_bytes();oldolskey=olskey.read_bytes()
    oldftp=ftpfile.read_bytes() if ftpfile.exists() else None
    try:
        _replace(keypath,key,0,gid);_replace(certpath,certificate,0,gid)
        _replace(olskey,key,lsadm.pw_uid,lsadm.pw_gid,0o400);_replace(olscert,certificate,lsadm.pw_uid,lsadm.pw_gid,0o400)
        _replace(ftpfile,certificate+b'\n'+key,0,0,0o600)
        subprocess.run(['systemctl','restart','boron-api.service'],check=True,timeout=45)
        subprocess.run(['systemctl','restart','lshttpd.service'],check=True,timeout=45)
        subprocess.run(['systemctl','restart','pure-ftpd.service'],check=True,timeout=45)
        subprocess.run(['systemctl','is-active','--quiet','boron-api.service'],check=True,timeout=10)
        subprocess.run(['systemctl','is-active','--quiet','lshttpd.service'],check=True,timeout=10)
        subprocess.run(['systemctl','is-active','--quiet','pure-ftpd.service'],check=True,timeout=10)
        _wait_for_certificate(hostname,certificate)
        _wait_for_ftps_certificate(hostname,certificate)
    except Exception:
        _replace(keypath,oldkey,0,gid);_replace(certpath,oldcert,0,gid)
        _replace(olskey,oldolskey,lsadm.pw_uid,lsadm.pw_gid,0o400);_replace(olscert,oldolscert,lsadm.pw_uid,lsadm.pw_gid,0o400)
        if oldftp is None:ftpfile.unlink(missing_ok=True)
        else:_replace(ftpfile,oldftp,0,0,0o600)
        subprocess.run(['systemctl','restart','boron-api.service'],check=False,timeout=45)
        subprocess.run(['systemctl','restart','lshttpd.service'],check=False,timeout=45)
        subprocess.run(['systemctl','restart','pure-ftpd.service'],check=False,timeout=45)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--hostname',required=True)
    args=parser.parse_args()
    lineage=os.environ.get('RENEWED_LINEAGE','')
    if args.hostname not in os.environ.get('RENEWED_DOMAINS','').split():
        raise SystemExit('Panel hostname missing from renewed certificate domains')
    deploy(args.hostname,lineage)
