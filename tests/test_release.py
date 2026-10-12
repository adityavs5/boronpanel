"""scripts/release.sh -- static checks + an offline functional dry-run.

Mirrors tests/test_installer.py's structure. The functional test runs the
real pipeline with --skip-tests (running pytest from inside pytest would
recurse) and --skip-build (no npm in CI), which still exercises staging,
tarball creation, checksumming and self-verification for real.
"""
from __future__ import annotations

import shutil
import subprocess
import tarfile
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE = REPO_ROOT / "scripts" / "release.sh"

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/tmp"}
if os.environ.get("BORON_RELEASE_SIGNING_KEY_FILE"):
    ENV["BORON_RELEASE_SIGNING_KEY_FILE"] = os.environ["BORON_RELEASE_SIGNING_KEY_FILE"]


def _run(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(RELEASE), *args],
        capture_output=True, text=True, env=ENV, cwd=cwd, timeout=300,
    )


def test_release_exists_and_is_executable():
    assert RELEASE.exists()
    assert RELEASE.stat().st_mode & 0o111


def test_release_bash_syntax_ok():
    r = subprocess.run(["bash", "-n", str(RELEASE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_release_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(RELEASE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_release_help_exits_zero():
    r = _run("--help")
    assert r.returncode == 0
    assert "Usage:" in r.stdout
    assert "--dry-run" in r.stdout
    assert "--prerelease" in r.stdout


def test_release_supports_prepared_prerelease_without_skipping_gates():
    source = RELEASE.read_text()
    assert 'PREPARED_VERSION=true' in source
    assert 'release_flags+=(--prerelease)' in source
    assert '$PREPARED_VERSION || bump_version "$new"' in source
    assert 'published releases require both the full test suite' in source


def test_release_rejects_unknown_flag():
    r = _run("--bogus")
    assert r.returncode != 0
    assert "unknown option" in r.stderr


@pytest.mark.parametrize("case", ["valid", "wrong", "dirty", "version", "short"])
def test_tested_commit_is_bound_to_clean_prepared_head(tmp_path, case):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    script = repo / "scripts/release.sh"
    script.write_text(RELEASE.read_text())
    (repo / "version.py").write_text('BORON_VERSION = "3.2.0"\n')
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    head = git("rev-parse", "HEAD")
    if case == "dirty":
        (repo / "untracked.txt").write_text("dirty")
    supplied = "0" * 40 if case == "wrong" else head[:8] if case == "short" else head
    version = "3.2.1" if case == "version" else "3.2.0"
    # Load the real functions without running publication, build or signing.
    body = script.read_text().rsplit('main "$@"', 1)[0]
    script.write_text(body + '\nparse_args "$@"\nvalidate_tested_commit\n')
    # The script alteration itself is committed so cleanliness is meaningful.
    if case != "dirty":
        git("add", "scripts/release.sh")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "harness")
        if case in {"valid", "version"}:
            supplied = git("rev-parse", "HEAD")
    result = subprocess.run(["bash", str(script), "--tested-commit", supplied, version],
                            capture_output=True, text=True, timeout=10)
    assert (result.returncode == 0) == (case == "valid"), result.stderr


def test_release_rejects_malformed_version():
    r = _run("1.2")
    assert r.returncode != 0
    assert "x.y.z" in r.stderr


@pytest.mark.parametrize("flag", ["--skip-tests", "--skip-build"])
def test_published_release_rejects_skipped_gates(flag):
    r = _run(flag)
    assert r.returncode != 0
    assert "published releases require both" in r.stderr


@pytest.mark.skipif(not (REPO_ROOT / ".git").exists(), reason="release builds require a source checkout")
@pytest.mark.skipif(not os.environ.get("BORON_RELEASE_SIGNING_KEY_FILE"), reason="release signing key unavailable")
def test_release_dry_run_builds_verified_artifacts(tmp_path):
    from version import BORON_VERSION

    out = tmp_path / "artifacts"
    r = _run("--dry-run", "--skip-tests", "--skip-build", "--output-dir", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "artifacts verified" in r.stdout

    tarball = out / f"boron-{BORON_VERSION}.tar.gz"
    checksum = out / f"boron-{BORON_VERSION}.sha256"
    signature = out / f"boron-{BORON_VERSION}.tar.gz.sig"
    assert tarball.exists() and checksum.exists() and signature.exists()

    # The checksum file must verify against the tarball (the update daemon
    # runs exactly this check before extracting anything).
    v = subprocess.run(["sha256sum", "-c", checksum.name], cwd=out,
                       capture_output=True, text=True, env=ENV)
    assert v.returncode == 0, v.stdout + v.stderr

    prefix = f"boron-{BORON_VERSION}"
    with tarfile.open(tarball) as tf:
        names = tf.getnames()
        # Everything under the version prefix, no traversal.
        assert all(n == prefix or n.startswith(prefix + "/") for n in names)
        assert not any(n.startswith("/") or ".." in n.split("/") for n in names)
        # Runtime essentials present.
        for member in ("version.py", "api/main.py", "daemon/server.py",
                       "requirements.txt", "static/dist/index.html",
                       "frontend/package.json", "frontend/package-lock.json",
                       "tests/conftest.py", "scripts/install.sh"):
            assert f"{prefix}/{member}" in names, f"missing {member}"
        # Never packaged: secrets, databases, logs, dev trees. Suffix match
        # for file kinds (".log" as a substring would false-positive on the
        # tracked deploy/boron-api.logrotate), component match for dirs.
        for suffix in ("secrets.env", ".env", ".db", ".db-wal", ".db-shm", ".log"):
            assert not any(n.endswith(suffix) for n in names), f"tarball leaked *{suffix}"
        for component in (".git", "node_modules", ".venv"):
            assert not any(component in n.split("/") for n in names), f"tarball leaked {component}"
        # Release archives also support a fresh install.  The installer
        # rebuilds the shipped UI from the tracked source and lock file.
        assert f"{prefix}/frontend/src" in names


@pytest.mark.skipif(not (REPO_ROOT / ".git").exists(), reason="release builds require a source checkout")
@pytest.mark.skipif(not os.environ.get("BORON_RELEASE_SIGNING_KEY_FILE"), reason="release signing key unavailable")
def test_release_dry_run_does_not_touch_version_py(tmp_path):
    before = (REPO_ROOT / "version.py").read_text()
    output = tmp_path / "release-nochange"
    r = _run("--dry-run", "--skip-tests", "--skip-build", "--output-dir",
             str(output))
    after = (REPO_ROOT / "version.py").read_text()
    assert r.returncode == 0
    assert before == after


@pytest.mark.skipif(not (REPO_ROOT / ".git").exists(), reason="release builds require a source checkout")
def test_release_without_signing_key_fails_closed(tmp_path):
    env = dict(ENV)
    env.pop("BORON_RELEASE_SIGNING_KEY_FILE", None)
    result = subprocess.run(
        ["bash", str(RELEASE), "--dry-run", "--skip-tests", "--skip-build",
         "--output-dir", str(tmp_path / "unsigned")],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=300,
    )
    assert result.returncode != 0
    assert "BORON_RELEASE_SIGNING_KEY_FILE" in result.stderr
