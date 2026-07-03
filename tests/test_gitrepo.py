import os
import pwd as real_pwd

import pytest

from daemon import gitrepo
from shared.validation import ValidationError


@pytest.fixture()
def account_with_home(isolated_db, tmp_path, monkeypatch):
    from shared.db import write_session
    from shared.models import Account

    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(gitrepo.filemanager.settings, "home_base", str(home_base))

    account_home = home_base / "demo1"
    account_home.mkdir()

    calls = []
    real_run = gitrepo.run

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["runuser", "-u"] and "init" in args:
            from daemon.procutil import ProcResult

            os.makedirs(args[-1], exist_ok=True)
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        # Everything else (the `bash -n` hook-syntax check in
        # set_deploy_target) is fast, safe, and offline -- run it for
        # real, matching this project's "real command over mock when it's
        # cheap and deterministic" precedent (e.g. real openssl/sievec
        # calls elsewhere in this test suite).
        return real_run(args, **kwargs)

    monkeypatch.setattr(gitrepo, "run", fake_run)

    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(account_home), "/usr/sbin/nologin"))
    monkeypatch.setattr(gitrepo.pwd, "getpwnam", lambda name: fake_pw)

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)

    return {"home": account_home, "calls": calls}


def test_create_repo(account_with_home):
    result = gitrepo.create_repo({"username": "demo1", "name": "my-site"})
    assert result["name"] == "my-site"
    assert result["deploy_target"] is None
    repo_path = account_with_home["home"] / "repos" / "my-site.git"
    assert repo_path.exists()


def test_create_repo_rejects_duplicate(account_with_home):
    gitrepo.create_repo({"username": "demo1", "name": "my-site"})
    with pytest.raises(ValidationError):
        gitrepo.create_repo({"username": "demo1", "name": "my-site"})


def test_create_repo_rejects_invalid_name(account_with_home):
    with pytest.raises(ValidationError):
        gitrepo.create_repo({"username": "demo1", "name": "My-Site"})
    with pytest.raises(ValidationError):
        gitrepo.create_repo({"username": "demo1", "name": "-leading-hyphen"})


def test_list_repos(account_with_home):
    gitrepo.create_repo({"username": "demo1", "name": "site-a"})
    gitrepo.create_repo({"username": "demo1", "name": "site-b"})
    names = {r["name"] for r in gitrepo.list_repos({"username": "demo1"})["repos"]}
    assert names == {"site-a", "site-b"}


def test_set_deploy_target_writes_valid_hook(account_with_home):
    gitrepo.create_repo({"username": "demo1", "name": "my-site"})
    (account_with_home["home"] / "public_html").mkdir()

    result = gitrepo.set_deploy_target({"username": "demo1", "name": "my-site", "deploy_target": "public_html"})
    assert result["deploy_target"] == "public_html"

    hook_path = account_with_home["home"] / "repos" / "my-site.git" / "hooks" / "post-receive"
    assert hook_path.exists()
    assert os.stat(hook_path).st_mode & 0o111  # executable
    content = hook_path.read_text()
    assert str(account_with_home["home"] / "public_html") in content


def test_set_deploy_target_creates_missing_dir(account_with_home):
    gitrepo.create_repo({"username": "demo1", "name": "my-site"})
    gitrepo.set_deploy_target({"username": "demo1", "name": "my-site", "deploy_target": "newdir"})
    assert (account_with_home["home"] / "newdir").is_dir()


def test_set_deploy_target_rejects_unknown_repo(account_with_home):
    with pytest.raises(gitrepo.GitRepoError):
        gitrepo.set_deploy_target({"username": "demo1", "name": "does-not-exist", "deploy_target": "public_html"})


def test_get_push_log_empty_before_any_push(account_with_home):
    gitrepo.create_repo({"username": "demo1", "name": "my-site"})
    result = gitrepo.get_push_log({"username": "demo1", "name": "my-site"})
    assert result["lines"] == []


def test_delete_repo_removes_row_and_directory(account_with_home):
    gitrepo.create_repo({"username": "demo1", "name": "my-site"})
    repo_path = account_with_home["home"] / "repos" / "my-site.git"
    assert repo_path.exists()

    gitrepo.delete_repo({"username": "demo1", "name": "my-site"})
    assert not repo_path.exists()
    assert gitrepo.list_repos({"username": "demo1"})["repos"] == []


def test_delete_repo_rejects_unknown_repo(account_with_home):
    with pytest.raises(gitrepo.GitRepoError):
        gitrepo.delete_repo({"username": "demo1", "name": "does-not-exist"})
