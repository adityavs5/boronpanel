import pyotp
import pytest

from daemon import totp
from daemon import handlers_auth
from shared.db import write_session
from shared.models import PanelUser, TotpCredential, TotpRecoveryCode
from shared.validation import ValidationError


@pytest.fixture()
def panel_user_id(isolated_db):
    with write_session() as session:
        user = PanelUser(username="admin1", password_hash="x", role="admin")
        session.add(user)
        session.flush()
        return user.id


def test_get_status_disabled_by_default(panel_user_id):
    assert totp.get_status({"panel_user_id": panel_user_id}) == {"enabled": False}


def test_setup_creates_pending_unverified_credential(panel_user_id):
    result = totp.setup_totp({"panel_user_id": panel_user_id})
    assert "secret" in result
    assert result["otpauth_uri"].startswith("otpauth://totp/")
    with write_session() as session:
        row = session.query(TotpCredential).filter_by(panel_user_id=panel_user_id).first()
        assert row.enabled is False
        assert row.secret == result["secret"]


def test_setup_rejects_when_already_enabled(panel_user_id):
    result = totp.setup_totp({"panel_user_id": panel_user_id})
    totp.verify_totp({"panel_user_id": panel_user_id, "code": pyotp.TOTP(result["secret"]).now()})
    with pytest.raises(ValidationError):
        totp.setup_totp({"panel_user_id": panel_user_id})


def test_verify_enables_and_returns_8_recovery_codes(panel_user_id):
    result = totp.setup_totp({"panel_user_id": panel_user_id})
    code = pyotp.TOTP(result["secret"]).now()
    verify_result = totp.verify_totp({"panel_user_id": panel_user_id, "code": code})
    assert verify_result["enabled"] is True
    assert len(verify_result["recovery_codes"]) == 8
    assert len(set(verify_result["recovery_codes"])) == 8  # all unique
    assert totp.get_status({"panel_user_id": panel_user_id}) == {"enabled": True}


def test_verify_rejects_wrong_code(panel_user_id):
    totp.setup_totp({"panel_user_id": panel_user_id})
    with pytest.raises(ValidationError):
        totp.verify_totp({"panel_user_id": panel_user_id, "code": "000000"})


def test_verify_without_setup_raises(panel_user_id):
    with pytest.raises(ValidationError):
        totp.verify_totp({"panel_user_id": panel_user_id, "code": "123456"})


def test_disable_removes_credential_and_recovery_codes(panel_user_id):
    result = totp.setup_totp({"panel_user_id": panel_user_id})
    totp.verify_totp({"panel_user_id": panel_user_id, "code": pyotp.TOTP(result["secret"]).now()})
    totp.disable_totp({"panel_user_id": panel_user_id})
    assert totp.get_status({"panel_user_id": panel_user_id}) == {"enabled": False}
    with write_session() as session:
        assert session.query(TotpCredential).filter_by(panel_user_id=panel_user_id).count() == 0
        assert session.query(TotpRecoveryCode).filter_by(panel_user_id=panel_user_id).count() == 0


def test_check_login_code_accepts_valid_totp(panel_user_id):
    result = totp.setup_totp({"panel_user_id": panel_user_id})
    totp.verify_totp({"panel_user_id": panel_user_id, "code": pyotp.TOTP(result["secret"]).now()})
    code = pyotp.TOTP(result["secret"]).now()
    check = totp.check_login_code({"panel_user_id": panel_user_id, "code": code})
    assert check == {"valid": True, "used_recovery_code": False}


def test_check_login_code_rejects_wrong_code(panel_user_id):
    result = totp.setup_totp({"panel_user_id": panel_user_id})
    totp.verify_totp({"panel_user_id": panel_user_id, "code": pyotp.TOTP(result["secret"]).now()})
    check = totp.check_login_code({"panel_user_id": panel_user_id, "code": "000000"})
    assert check["valid"] is False


def test_check_login_code_raises_when_not_enabled(panel_user_id):
    with pytest.raises(ValidationError):
        totp.check_login_code({"panel_user_id": panel_user_id, "code": "123456"})


def test_check_login_code_accepts_recovery_code_once(panel_user_id):
    result = totp.setup_totp({"panel_user_id": panel_user_id})
    verify_result = totp.verify_totp({"panel_user_id": panel_user_id, "code": pyotp.TOTP(result["secret"]).now()})
    recovery_code = verify_result["recovery_codes"][0]

    check1 = totp.check_login_code({"panel_user_id": panel_user_id, "code": recovery_code})
    assert check1 == {"valid": True, "used_recovery_code": True}

    # single-use: the exact same code must not work a second time
    check2 = totp.check_login_code({"panel_user_id": panel_user_id, "code": recovery_code})
    assert check2["valid"] is False


def test_verify_regenerates_recovery_codes_invalidating_old_ones(panel_user_id):
    result = totp.setup_totp({"panel_user_id": panel_user_id})
    first_verify = totp.verify_totp({"panel_user_id": panel_user_id, "code": pyotp.TOTP(result["secret"]).now()})
    old_code = first_verify["recovery_codes"][0]

    totp.disable_totp({"panel_user_id": panel_user_id})
    result2 = totp.setup_totp({"panel_user_id": panel_user_id})
    totp.verify_totp({"panel_user_id": panel_user_id, "code": pyotp.TOTP(result2["secret"]).now()})

    check = totp.check_login_code({"panel_user_id": panel_user_id, "code": old_code})
    assert check["valid"] is False
