"""Release tooling -- scripts/release.sh and scripts/dist_release.sh.

Mirrors tests/test_installer.py's structure. The release.sh functional test
runs the real pipeline with --skip-tests (running pytest from inside pytest
would recurse) and --skip-build (no npm in CI), which still exercises staging,
tarball creation, checksumming and self-verification for real.

The dist_release.sh tests build a throwaway git repository from this tree
with a throwaway signing key, then run the real script: --dry-run for the
artifacts, and a full publication through a fake ``ssh`` that applies the
upload to a local directory standing in for the distribution server.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE = REPO_ROOT / "scripts" / "release.sh"
DIST_RELEASE = REPO_ROOT / "scripts" / "dist_release.sh"
SHELL_SCRIPTS = [RELEASE, DIST_RELEASE, REPO_ROOT / "scripts" / "dist_publish.sh",
                 REPO_ROOT / "scripts" / "release_lib.sh", REPO_ROOT / "scripts" / "dist_common.sh"]

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


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_release_tooling_bash_syntax_ok(script):
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_release_tooling_shellcheck_clean(script):
    r = subprocess.run(["shellcheck", "-x", str(script)], capture_output=True, text=True,
                       cwd=REPO_ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_release_help_exits_zero():
    r = _run("--help")
    assert r.returncode == 0
    assert "Usage:" in r.stdout
    assert "--dry-run" in r.stdout
    assert "dist_release.sh" in r.stdout


def test_release_supports_prepared_version_without_skipping_gates():
    source = RELEASE.read_text()
    assert 'PREPARED_VERSION=true' in source
    assert '$PREPARED_VERSION || bump_version "$new"' in source
    assert 'published releases require both the full test suite' in source
    # Both tools share one implementation of the artifact steps.
    assert '. "${REPO_ROOT}/scripts/release_lib.sh"' in source
    assert '. "${REPO_ROOT}/scripts/release_lib.sh"' in DIST_RELEASE.read_text()
    for name in ("stage_tree", "build_artifacts", "sign_artifacts", "verify_artifacts"):
        assert f"{name}() {{" not in source
        assert f"{name}() {{" not in DIST_RELEASE.read_text()


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_release_tooling_never_uses_github(script):
    """Releases leave GitHub entirely: no gh CLI, no push, no GitHub hosts."""
    lines = [line for line in script.read_text().splitlines()
             if not line.lstrip().startswith("#")]
    code = "\n".join(lines)
    assert "github" not in code.lower()
    assert "gh release" not in code and "gh auth" not in code
    assert "require_tool gh" not in code
    assert "git push" not in code and " push origin" not in code


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
    (repo / "scripts/release_lib.sh").write_text((REPO_ROOT / "scripts/release_lib.sh").read_text())
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
        git("add", "scripts/release.sh", "scripts/release_lib.sh")
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


# --- scripts/dist_release.sh ---------------------------------------------------

_ROOT_SKIP = {".git", ".venv", "dist", "secrets", "master", "master.pub", ".claude", "backup"}
_ANY_SKIP = {"node_modules", "__pycache__", ".pytest_cache"}
_GIT_ID = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}


def _copy_ignore(directory, names):
    here = Path(directory)
    skipped = {n for n in names if n in _ANY_SKIP}
    if here == REPO_ROOT:
        skipped |= {n for n in names if n in _ROOT_SKIP}
    if here == REPO_ROOT / "static":
        skipped.add("dist")
    return skipped


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                   env={**ENV, **_GIT_ID}).strip()


@pytest.fixture(scope="module")
def dist_template(tmp_path_factory):
    """This tree as a committed throwaway repo, re-keyed to a throwaway key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    base = tmp_path_factory.mktemp("dist")
    repo = base / "template"
    shutil.copytree(REPO_ROOT, repo, ignore=_copy_ignore, symlinks=True)
    key = Ed25519PrivateKey.generate()
    key_file = base / "release-signing.key"
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    public = key.public_key()
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    (repo / "deploy/release-ed25519-public.hex").write_text(raw.hex() + "\n")
    pem = public.public_bytes(serialization.Encoding.PEM,
                              serialization.PublicFormat.SubjectPublicKeyInfo).decode().strip()
    launcher = (repo / "install.sh").read_text()
    start = launcher.index("readonly SIGNING_PUBLIC_KEY='")
    end = launcher.index("-----END PUBLIC KEY-----'", start) + len("-----END PUBLIC KEY-----'")
    (repo / "install.sh").write_text(launcher[:start] + f"readonly SIGNING_PUBLIC_KEY='{pem}'" + launcher[end:])
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture")
    version = subprocess.check_output(
        ["sed", "-n", r's/^BORON_VERSION = "\(.*\)"$/\1/p', str(repo / "version.py")], text=True).strip()
    static = base / "static-dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Boron</title>\n")
    (static / "assets/index-test.js").write_text(f'const version="{version}";\n')
    return {"base": base, "repo": repo, "key": key_file, "public_key": raw, "pem": pem,
            "version": version, "static": static}


@pytest.fixture
def dist_repo(dist_template, tmp_path):
    repo = tmp_path / "repo"
    subprocess.check_call(["git", "clone", "-q", str(dist_template["repo"]), str(repo)], env=ENV)
    # A real ssh is never reachable from tests: this stand-in runs the remote
    # command locally, so the server-side steps are exercised for real.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text('#!/bin/bash\nexec bash -c "${@: -1}"\n')
    (bin_dir / "ssh").chmod(0o755)
    (tmp_path / "ssh-key").write_text("not a key\n")
    server = tmp_path / "srv"
    server.mkdir()
    env = {**ENV, **_GIT_ID, "HOME": str(tmp_path), "PATH": f"{bin_dir}:{ENV['PATH']}",
           "PYTHON": sys.executable, "BORON_RELEASE_SIGNING_KEY_FILE": str(dist_template["key"]),
           "BORON_DIST_SSH_KEY": str(tmp_path / "ssh-key"), "BORON_DIST_ROOT": str(server)}
    return {**dist_template, "repo": repo, "env": env, "server": server, "cwd": tmp_path}


def _dist(ctx, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(ctx["repo"] / "scripts/dist_release.sh"), *args],
                          capture_output=True, text=True, env=ctx["env"], cwd=ctx["cwd"], timeout=300)


def test_dist_release_exists_and_is_executable():
    assert DIST_RELEASE.exists()
    assert DIST_RELEASE.stat().st_mode & 0o111


def test_dist_release_help_exits_zero():
    r = subprocess.run(["bash", str(DIST_RELEASE), "--help"], capture_output=True, text=True, env=ENV)
    assert r.returncode == 0
    for text in ("Usage:", "--dry-run", "--static-dir", "--channel", "BORON_RELEASE_SIGNING_KEY_FILE",
                 "BORON_DIST_HOST", "BORON_DIST_SSH_KEY", "PYTHON"):
        assert text in r.stdout


@pytest.mark.parametrize("args, message", [
    (["--bogus"], "unknown option"),
    (["--channel", "nightly"], "stable or beta"),
    (["--static-dir", "/nonexistent-boron-static"], "not a directory"),
])
def test_dist_release_rejects_bad_arguments(args, message):
    r = subprocess.run(["bash", str(DIST_RELEASE), *args], capture_output=True, text=True, env=ENV)
    assert r.returncode != 0
    assert message in r.stderr


def test_dist_release_dry_run_builds_verified_artifacts(dist_repo):
    from shared.release_metadata import load_release_metadata
    from shared.release_signature import archive_digest, verify_signature

    version = dist_repo["version"]
    r = _dist(dist_repo, "--dry-run", "--static-dir", str(dist_repo["static"]), "--output-dir", "out")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "artifacts verified" in r.stdout and "upload (dry-run)" in r.stdout
    out = dist_repo["cwd"] / "out"
    protected, public = out / "protected", out / "public"
    base = f"boron-{version}"
    names = {f"{base}.tar.gz", f"{base}.sha256", f"{base}.tar.gz.sig", f"{base}.metadata.json",
             f"{base}.metadata.json.sig", "release.json", "SHA256SUMS"}
    assert {p.name for p in protected.iterdir()} == names
    assert not any(dist_repo["server"].iterdir()), "dry-run must not upload"

    archive = protected / f"{base}.tar.gz"
    key = dist_repo["public_key"]
    verify_signature(version, archive_digest(archive), (protected / f"{base}.tar.gz.sig").read_bytes(), key)
    metadata = protected / f"{base}.metadata.json"
    verify_signature(version, archive_digest(metadata), (protected / f"{base}.metadata.json.sig").read_bytes(), key)
    assert load_release_metadata(metadata)["channel"] == "stable"
    v = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=protected, capture_output=True, text=True)
    assert v.returncode == 0, v.stdout + v.stderr

    index = json.loads((protected / "release.json").read_text())
    assert list(index) == ["format", "channel", "version", "published_at", "changelog", "files", "sizes"]
    assert index["format"] == 1 and index["channel"] == "stable" and index["version"] == version
    assert index["published_at"].endswith("Z")
    assert index["files"] == {"archive": f"{base}.tar.gz", "checksum": f"{base}.sha256",
                              "signature": f"{base}.tar.gz.sig", "metadata": f"{base}.metadata.json",
                              "metadata_signature": f"{base}.metadata.json.sig"}
    assert index["sizes"] == {"archive": archive.stat().st_size}
    assert index["changelog"] and not index["changelog"].startswith("## ")
    assert "\n## " not in index["changelog"], "changelog must hold only this version's section"

    with tarfile.open(archive) as tf:
        members = tf.getnames()
        assert all(n == base or n.startswith(base + "/") for n in members)
        bundle = tf.extractfile(f"{base}/static/dist/assets/index-test.js").read().decode()
        assert version in bundle
        assert tf.getmember(f"{base}/static/dist/index.html").mode & 0o022 == 0
        assert not any(m.isfile() and m.name.startswith(f"{base}/licensing/") for m in tf.getmembers()), \
            "licensing/ is export-ignored"
        assert all(m.uid == 0 and m.gid == 0 for m in tf.getmembers())

    manifest = public / f"{base}.manifest"
    fields = dict(line.split("=", 1) for line in manifest.read_text().splitlines())
    assert fields["channel"] == "stable" and fields["version"] == version
    assert fields["commit"] == _git(dist_repo["repo"], "rev-parse", "HEAD")
    pub = dist_repo["cwd"] / "launcher.pub"
    pub.write_text(dist_repo["pem"] + "\n")
    verified = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub), "-rawin",
                               "-in", str(manifest), "-sigfile", str(manifest) + ".sig"], capture_output=True)
    assert verified.returncode == 0, verified.stderr
    assert (public / "install.sh").read_bytes() == (dist_repo["repo"] / "install.sh").read_bytes()


def test_dist_release_rebuild_is_byte_identical(dist_repo):
    version = dist_repo["version"]
    for out in ("one", "two"):
        r = _dist(dist_repo, "--dry-run", "--static-dir", str(dist_repo["static"]), "--output-dir", out)
        assert r.returncode == 0, r.stdout + r.stderr
    for part in ("protected", "public"):
        a = (dist_repo["cwd"] / "one" / part / f"boron-{version}.tar.gz").read_bytes()
        b = (dist_repo["cwd"] / "two" / part / f"boron-{version}.tar.gz").read_bytes()
        assert a == b, f"{part} archive is not reproducible"


def test_dist_release_publishes_to_server_layout(dist_repo):
    version = dist_repo["version"]
    base = f"boron-{version}"
    server = dist_repo["server"]
    r = _dist(dist_repo, "--static-dir", str(dist_repo["static"]))
    assert r.returncode == 0, r.stdout + r.stderr
    built = dist_repo["repo"] / "dist" / f"release-{version}"

    release_dir = server / "protected/releases" / version
    expected = {f"{base}.tar.gz", f"{base}.sha256", f"{base}.tar.gz.sig", f"{base}.metadata.json",
                f"{base}.metadata.json.sig", "release.json"}
    assert {p.name for p in release_dir.iterdir()} == expected
    for name in expected:
        assert (release_dir / name).read_bytes() == (built / "protected" / name).read_bytes()
        assert (release_dir / name).stat().st_mode & 0o777 == 0o644
    assert release_dir.stat().st_mode & 0o777 == 0o755
    channel = server / "protected/channels/stable.json"
    assert channel.read_bytes() == (release_dir / "release.json").read_bytes()
    assert (server / "releases" / version / f"{base}.tar.gz").read_bytes() == \
        (built / "public" / f"{base}.tar.gz").read_bytes()
    assert (server / "channels/stable.manifest").read_bytes() == \
        (server / "releases" / version / f"{base}.manifest").read_bytes()
    assert (server / "channels/stable.manifest.sig").exists()
    assert (server / "install.sh").read_bytes() == (dist_repo["repo"] / "install.sh").read_bytes()
    assert not list(server.glob(".upload-*")), "the staging directory must be removed"
    assert not list(server.glob("**/.*.new"))

    # Re-running the same commit is safe: identical bytes, original index kept.
    first_index = channel.read_bytes()
    r = _dist(dist_repo, "--static-dir", str(dist_repo["static"]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already published with identical bytes" in r.stdout
    assert channel.read_bytes() == first_index

    # Different bytes under a published version are refused before anything moves.
    (dist_repo["repo"] / "docs/extra.md").write_text("changed\n")
    _git(dist_repo["repo"], "add", "docs/extra.md")
    _git(dist_repo["repo"], "commit", "-qm", "change without a version bump")
    manifest_before = (server / "channels/stable.manifest").read_bytes()
    r = _dist(dist_repo, "--static-dir", str(dist_repo["static"]))
    assert r.returncode != 0
    assert "already published with different bytes" in r.stderr
    assert channel.read_bytes() == first_index
    assert (server / "channels/stable.manifest").read_bytes() == manifest_before
    assert not list(server.glob(".upload-*"))


def test_dist_release_requires_clean_tracked_tree(dist_repo):
    (dist_repo["repo"] / "README.md").write_text("edited\n")
    r = _dist(dist_repo, "--dry-run", "--static-dir", str(dist_repo["static"]))
    assert r.returncode != 0
    assert "uncommitted changes" in r.stderr


def test_dist_release_channel_must_match_signed_metadata(dist_repo):
    r = _dist(dist_repo, "--dry-run", "--channel", "beta", "--static-dir", str(dist_repo["static"]))
    assert r.returncode != 0
    assert "does not match release-metadata.json" in r.stderr


def test_dist_release_refuses_stale_frontend_bundle(dist_repo, tmp_path):
    stale = tmp_path / "stale"
    shutil.copytree(dist_repo["static"], stale)
    (stale / "assets/index-test.js").write_text('const version="0.0.1";\n')
    r = _dist(dist_repo, "--dry-run", "--static-dir", str(stale))
    assert r.returncode != 0
    assert "was not built for" in r.stderr


def test_dist_release_refuses_unpinned_signing_key(dist_repo, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    other = tmp_path / "other.key"
    other.write_bytes(Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    dist_repo["env"]["BORON_RELEASE_SIGNING_KEY_FILE"] = str(other)
    r = _dist(dist_repo, "--dry-run", "--static-dir", str(dist_repo["static"]))
    assert r.returncode != 0
    assert "does not match deploy/release-ed25519-public.hex" in r.stderr


def test_dist_publish_standalone_publishes_source_only(dist_repo):
    version = dist_repo["version"]
    env = {**dist_repo["env"], "BORON_SIGNING_KEY": str(dist_repo["key"])}
    r = subprocess.run(["bash", str(dist_repo["repo"] / "scripts/dist_publish.sh"), "--channel", "stable"],
                       capture_output=True, text=True, env=env, cwd=dist_repo["repo"], timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    server = dist_repo["server"]
    assert (server / "releases" / version / f"boron-{version}.tar.gz").exists()
    assert (server / "channels/stable.manifest").exists()
    assert (server / "install.sh").exists()
    assert not (server / "protected").exists()
