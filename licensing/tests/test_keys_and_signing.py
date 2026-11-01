import base64

import pytest
from boron_license import keys
from boron_license.signing import Signer, verify_document
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def test_generated_keys_are_canonical_and_unique():
    generated = {keys.generate_key() for _ in range(500)}
    assert len(generated) == 500
    for key in generated:
        assert keys.normalize_key(key) == key
        assert not set(key[4:].replace("-", "")) & set("01IO")


def test_normalize_accepts_spacing_and_case_and_rejects_garbage():
    key = keys.generate_key()
    assert keys.normalize_key(f"  {key.lower()} ") == key
    for bad in ("", "BRN-AAAA", "XYZ-AAAA-AAAA-AAAA-AAAA", "BRN-AAAA-AAAA-AAAA-AAA0", key + "-AAAA"):
        assert keys.normalize_key(bad) is None


def test_document_signature_round_trip_and_tamper_detection():
    signer = Signer(Ed25519PrivateKey.generate())
    document = signer.sign({"status": "active", "max_accounts": 30, "ip": "203.0.113.10"})
    assert verify_document(signer.public_key, document)["max_accounts"] == 30

    tampered = {**document, "payload": {**document["payload"], "max_accounts": None}}
    with pytest.raises(ValueError):
        verify_document(signer.public_key, tampered)
    other = Signer(Ed25519PrivateKey.generate())
    with pytest.raises(ValueError):
        verify_document(other.public_key, document)
    with pytest.raises(ValueError):
        verify_document(signer.public_key, {**document, "signature": "not base64!"})
    with pytest.raises(ValueError):
        verify_document(signer.public_key, {"signature": base64.b64encode(b"x" * 64).decode()})
