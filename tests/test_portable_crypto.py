from pathlib import Path

import pytest

from daemon.portable_crypto import PortableEncryptionError, decrypt, encrypt


def test_streaming_portable_encryption_round_trip_and_tamper_detection(tmp_path: Path):
    source=tmp_path/'source.tar';source.write_bytes((b'boron portable backup\0'*100000)+b'end')
    encrypted=tmp_path/'source.tar.enc';restored=tmp_path/'restored.tar'
    key='independently-exported-recovery-key-value'
    encrypt(source,encrypted,key)
    assert encrypted.read_bytes()[:8]!=source.read_bytes()[:8]
    decrypt(encrypted,restored,key)
    assert restored.read_bytes()==source.read_bytes()

    damaged=bytearray(encrypted.read_bytes());damaged[-20]^=1
    tampered=tmp_path/'tampered.enc';tampered.write_bytes(damaged)
    with pytest.raises(PortableEncryptionError,match='modified'):
        decrypt(tampered,tmp_path/'should-not-exist.tar',key)
    assert not (tmp_path/'should-not-exist.tar').exists()


def test_portable_encryption_rejects_wrong_key_without_partial_output(tmp_path: Path):
    source=tmp_path/'source.tar';source.write_bytes(b'content')
    encrypted=tmp_path/'source.tar.enc';output=tmp_path/'output.tar'
    encrypt(source,encrypted,'correct-recovery-key-value')
    with pytest.raises(PortableEncryptionError,match='incorrect'):
        decrypt(encrypted,output,'different-recovery-key-value')
    assert not output.exists()
