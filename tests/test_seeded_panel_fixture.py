from sqlalchemy import func, select

from testsupport.seeded_panel import seed_panel_database


def test_seeded_panel_fixture_is_complete_and_idempotent(tmp_path, monkeypatch):
    from shared.config import settings

    db_path = tmp_path / "fixture.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    first = seed_panel_database(
        db_path, admin_password="Admin-ci-password-123!",
        customer_password="Customer-ci-password-123!",
    )
    second = seed_panel_database(
        db_path, admin_password="Admin-ci-password-123!",
        customer_password="Customer-ci-password-123!",
    )

    from shared.db import write_session
    from shared.models import Account, Domain, PanelUser, Plan
    from shared.passwords import verify_password

    assert first == second
    with write_session() as db:
        assert db.scalar(select(func.count()).select_from(Account)) == 1
        assert db.scalar(select(func.count()).select_from(Domain)) == 1
        assert db.scalar(select(func.count()).select_from(Plan)) == 1
        assert db.scalar(select(func.count()).select_from(PanelUser)) == 2
        admin = db.scalar(select(PanelUser).where(PanelUser.username == first.admin_username))
        assert verify_password("Admin-ci-password-123!", admin.password_hash)
        account = db.scalar(select(Account).where(Account.username == first.account_username))
        assert account.primary_domain == first.primary_domain
