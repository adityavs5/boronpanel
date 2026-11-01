import json

import pytest
from boron_license import cli


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BORON_LICENSE_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("BORON_LICENSE_SIGNING_KEY", str(tmp_path / "keys" / "signing.key"))
    return tmp_path


def run(capsys, *args):
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def test_full_cli_workflow(env, capsys):
    assert run(capsys, "init-db")[0] == 0
    code, out, _ = run(capsys, "gen-signing-key")
    assert code == 0 and "BEGIN PUBLIC KEY" in out
    assert (env / "keys" / "signing.key").stat().st_mode & 0o777 == 0o600
    code, _, err = run(capsys, "gen-signing-key")
    assert code == 1 and "refusing to replace" in err

    code, out, _ = run(capsys, "issue", "--tier", "admin", "--name", "Acme", "--email", "a@acme.example",
                       "--expires", "2031-06-30")
    issued = json.loads(out)
    assert issued["license_key"].startswith("BRN-")
    assert issued["license"]["expires_at"].startswith("2031-06-30T23:59:59")
    license_id = str(issued["license"]["id"])

    assert json.loads(run(capsys, "suspend", license_id, "--reason", "test")[1])["status"] == "suspended"
    assert json.loads(run(capsys, "rebind", license_id, "--ip", "203.0.113.4")[1])["bound_ip"] == "203.0.113.4"
    assert json.loads(run(capsys, "update", license_id, "--no-expiry")[1])["expires_at"] is None
    assert [row["id"] for row in json.loads(run(capsys, "list", "acme")[1])] == [int(license_id)]

    code, out, _ = run(capsys, "tier", "set", "enterprise", "--unlimited")
    assert {"name": "enterprise", "max_accounts": None} in json.loads(out)
    token = json.loads(run(capsys, "token", "create", "billing")[1])["token"]
    assert token.startswith("blt_")
    assert json.loads(run(capsys, "token", "revoke", "billing")[1])[0]["revoked"] is True


def test_cli_reports_errors(env, capsys):
    run(capsys, "init-db")
    code, _, err = run(capsys, "issue", "--tier", "nope", "--name", "A", "--email", "a@b.example")
    assert code == 1 and "unknown_tier" in err
