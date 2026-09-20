import pytest
from sqlalchemy import select

from daemon import handlers_database, handlers_domain, handlers_ftp, handlers_mail, nodeapps, pythonapps
from shared.db import write_session
from shared.models import (
    Account, AccountResourceLimits, DatabaseGrant, Domain, FtpAccount,
    MailDomain, MailUser, NodeApp,
)
from shared.validation import ValidationError


def test_plan_count_caps_block_each_provisioning_handler(isolated_db, monkeypatch):
    """A full plan's limits must hold at the root handler, not only in UI alerts."""
    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(AccountResourceLimits(
            account_id=account.id, database_limit=1, email_account_limit=1,
            subdomain_limit=1, ftp_account_limit=1, app_limit=1))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html"))
        session.add(Domain(account_id=account.id, domain="one.demo1.example", kind="subdomain", docroot="/home/demo1/one"))
        session.add(Domain(account_id=account.id, domain="node.demo1.example", kind="addon", docroot="/home/demo1/node"))
        session.add(Domain(account_id=account.id, domain="python.demo1.example", kind="addon", docroot="/home/demo1/python"))
        session.add(DatabaseGrant(account_id=account.id, db_name="demo1_one", db_user="demo1_one"))
        session.add(FtpAccount(account_id=account.id, ftp_login="demo1_one", path="/home/demo1"))
        mail_domain = MailDomain(account_id=account.id, domain="demo1.example")
        session.add(mail_domain)
        session.flush()
        session.add(MailUser(mail_domain_id=mail_domain.id, local_part="one", domain="demo1.example"))
        session.add(NodeApp(account_id=account.id, domain="node.demo1.example", name="one",
                            entry_point="index.js", port=19100, node_version="22"))

    monkeypatch.setattr(handlers_database.mariadb, "database_exists", lambda name: False)
    password = "StrongPassword123!"
    actions = [
        lambda: handlers_database.create_database({"username": "demo1", "name": "two"}),
        lambda: handlers_domain.add_domain({"username": "demo1", "domain": "two.demo1.example",
                                            "kind": "subdomain", "parent_domain": "demo1.example"}),
        lambda: handlers_ftp.create_ftp_account({"username": "demo1", "label": "two", "password": password}),
        lambda: handlers_mail.create_mailbox({"domain": "demo1.example", "local_part": "two", "password": password}),
        lambda: nodeapps.create_app({"username": "demo1", "domain": "python.demo1.example",
                                     "name": "two", "entry_point": "index.js"}),
        lambda: pythonapps.create_app({"username": "demo1", "domain": "python.demo1.example",
                                       "name": "two", "entry_point": "app:app"}),
    ]
    for action in actions:
        with pytest.raises(ValidationError, match="limit reached"):
            action()

    with write_session() as session:
        assert len(session.scalars(select(DatabaseGrant)).all()) == 1
        assert len(session.scalars(select(Domain).where(Domain.kind == "subdomain")).all()) == 1
        assert len(session.scalars(select(FtpAccount)).all()) == 1
        assert len(session.scalars(select(MailUser)).all()) == 1
        assert len(session.scalars(select(NodeApp)).all()) == 1
