"""FileBrowser Quantum integration -- the file manager, replacing the custom
one (daemon/filemanager.py is retired once this is verified).

Design (full reasoning + the empirical verification behind it live in
docs/CHECKPOINT-filebrowser-quantum.md):

- ONE FileBrowser Quantum process (`forgehost-filebrowser.service`), a single
  Go binary, bound to 127.0.0.1 only and reached exclusively through
  forgehost-api's authenticated proxy, which injects the trusted `X-Fb-User`
  header server-side. Never public.

- It runs as **root** on purpose: like the old manager (ARCHITECTURE.md §10),
  the file manager must read/write files across every account home under
  Forgehost's 711-home / 750-docroot perms model, which only root can do from a
  single long-lived process. FB Quantum can't per-request drop to the account
  uid, so files it creates are root-owned; `add_source` compensates by putting
  a default POSIX ACL on the account home granting the account rwX, so
  FB-created files stay fully usable by the account's own PHP/FTP/SSH.

- Isolation is a SINGLE shared source at `settings.home_base` (`/home`) with
  `createUserDir: true` + `defaultUserScope: "/"`: a proxy-authenticated user
  `alice` is auto-created and auto-scoped to `/home/alice`, unable to see
  `/home` or any sibling account. Verified live (listing, `..`-traversal,
  URL-encoded traversal, alternate source names, and search are all
  scope-clamped). Per-account *sources* are deliberately NOT used -- FB Quantum
  can't auto-bind a source to a same-named user without per-user DB writes, and
  its config is not hot-reloaded, so a static single source is both correct and
  restart-free across the account lifecycle.

Ops: fb.bootstrap (install-time), fb.add_source, fb.remove_source, fb.status,
fb.open (the audited "who opened whose files" record used by the launch flow).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

from shared.config import settings
from shared.validation import validate_username

from daemon.procutil import run

logger = logging.getLogger("forgehostd.filebrowser")

SERVICE_NAME = "forgehost-filebrowser.service"
UNIT_PATH = "/etc/systemd/system/forgehost-filebrowser.service"
SOURCE_NAME = "home"


class FileBrowserError(Exception):
    pass


# --- paths / helpers -------------------------------------------------------


def _account_home(username: str) -> str:
    """Resolve + jail-check an account's home dir (same convention as the old
    filemanager._account_home): derived from settings.home_base, realpath'd,
    must exist and stay under home_base."""
    validate_username(username)
    base = os.path.realpath(settings.home_base)
    home = os.path.realpath(os.path.join(base, username))
    if home != base and not home.startswith(base + os.sep):
        raise FileBrowserError("resolved home escapes the home base")
    if not os.path.isdir(home):
        raise FileBrowserError(f"account '{username}' has no home directory")
    return home


def _database_path() -> str:
    return os.path.join(settings.filebrowser_data_dir, "database.db")


def _cache_dir() -> str:
    return os.path.join(settings.filebrowser_data_dir, "cache")


# --- config generation -----------------------------------------------------


def build_config() -> dict:
    """The FileBrowser Quantum config as a plain dict (YAML-serializable).

    Static: it never changes per account (the single /home source + proxy
    auto-provisioning covers every account), so account create/terminate never
    rewrites it -- which also sidesteps FB Quantum having no config hot-reload.
    """
    return {
        "server": {
            "port": settings.filebrowser_bind_port,
            "listen": settings.filebrowser_bind_host,  # loopback only
            "baseURL": settings.filebrowser_base_url,
            "database": _database_path(),
            "cacheDir": _cache_dir(),
            # CRITICAL isolation control (found by live testing on a real
            # 711-home box): FileBrowser Quantum runs as root and, by default,
            # explicitly chmods every file it creates to 0644 — world-readable.
            # Under Forgehost's world-traversable 711 account homes (required so
            # OLS's `nobody` worker can reach public_html), a 0644 file is
            # readable by EVERY other account on the box → a cross-tenant leak
            # the old manager avoided by writing 0640 account-owned files. These
            # two knobs make FB create 0660 files / 0770 dirs instead: `other`
            # has no access (leak closed), while the account's own uid still gets
            # read+write via the default ACL add_source applies (its mask is the
            # group triad, rw/rwx here), and `nobody` still serves public_html
            # files via that dir's own nobody ACL (masked to read).
            "filesystem": {
                "createFilePermission": "660",
                "createDirectoryPermission": "770",
            },
            "logging": [{"levels": "error"}],
            "sources": [
                {
                    "path": os.path.realpath(settings.home_base),
                    "name": SOURCE_NAME,
                    "config": {
                        "defaultEnabled": True,
                        "createUserDir": True,
                        "defaultUserScope": "/",
                    },
                }
            ],
        },
        "auth": {
            "methods": {
                # password login disabled: identity comes only from the trusted
                # proxy header, which forgehost-api sets server-side.
                "password": {"enabled": False},
                "proxy": {
                    "enabled": True,
                    "header": settings.filebrowser_header,
                    "createUser": True,
                },
            }
        },
        "frontend": {"name": settings.filebrowser_brand},
        "userDefaults": {
            "darkMode": True,
            "permissions": {
                "admin": False,
                "api": False,
                "modify": True,
                "create": True,
                "delete": True,
                "download": True,
                # no sharing (multi-tenant box). realtime IS enabled: FB's live
                # updates are SSE (plain HTTP GET /api/events, confirmed in its
                # source -- not a websocket), which streams through the proxy
                # fine; with it disabled the SPA hits a 403 and sits in a 5s
                # reconnect loop showing "connection lost" toasts forever.
                "share": False,
                "realtime": True,
            },
        },
    }


def render_config() -> str:
    """Write /etc/forgehost/filebrowser.yaml (0640 root). Returns the path."""
    path = settings.filebrowser_config
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(build_config(), sort_keys=False, default_flow_style=False)
    Path(path).write_text(text)
    os.chmod(path, 0o640)
    return path


def _unit_content() -> str:
    return (
        "[Unit]\n"
        "Description=Forgehost File Manager (FileBrowser Quantum)\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "User=root\n"
        "Group=root\n"
        f"ExecStart={settings.filebrowser_bin} -c {settings.filebrowser_config}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        # Runs as root by necessity (cross-account home access); it must NOT be
        # confined away from /home, so no ProtectHome. Loopback-only bind is the
        # actual network boundary (see build_config server.listen).
        f"StandardOutput=append:{settings.log_dir}/filebrowser.log\n"
        f"StandardError=append:{settings.log_dir}/filebrowser.log\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _write_unit() -> None:
    content = _unit_content()
    p = Path(UNIT_PATH)
    if p.exists() and p.read_text() == content:
        return
    p.write_text(content)


# --- ownership mitigation --------------------------------------------------


def _apply_account_acl(username: str, home: str) -> None:
    """Grant the account rwX on its own home via a POSIX ACL (access + default),
    so files FileBrowser Quantum creates while running as root stay fully usable
    by the account's own uid (PHP/FTP/SSH). A named-user ACL entry applies even
    when the file owner is root; the default ACL makes newly created files
    inherit it. Idempotent; safe (an account gaining access to its *own* home is
    not an isolation change). Best-effort: logged, not fatal, if setfacl is
    unavailable."""
    entry = f"u:{username}:rwX"
    default_entry = f"d:u:{username}:rwX"
    res = run(["setfacl", "-R", "-m", f"{entry},{default_entry}", home], timeout=120)
    if not res.ok:
        logger.warning("setfacl for %s failed: %s", username, res.stderr.strip() or res.stdout.strip())


# --- ops -------------------------------------------------------------------


def bootstrap(params: dict | None = None) -> dict:
    """Install-time: ensure data dir, config, systemd unit, and a running
    service. Idempotent. Raises if the binary is missing (so an admin-triggered
    fb.bootstrap fails loudly; the startup call in server.amain() catches it)."""
    if not os.path.exists(settings.filebrowser_bin):
        raise FileBrowserError(
            f"FileBrowser Quantum binary not found at {settings.filebrowser_bin}; "
            "run scripts/install_filebrowser.sh first"
        )
    Path(settings.filebrowser_data_dir).mkdir(parents=True, exist_ok=True)
    os.chmod(settings.filebrowser_data_dir, 0o700)
    Path(_cache_dir()).mkdir(parents=True, exist_ok=True)
    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
    render_config()
    _write_unit()
    run(["systemctl", "daemon-reload"], timeout=20, check=True)
    run(["systemctl", "enable", "--now", SERVICE_NAME], timeout=30, check=True)
    active = run(["systemctl", "is-active", SERVICE_NAME], timeout=10)
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "active": active.stdout.strip() or active.stderr.strip(),
        "config": settings.filebrowser_config,
    }


def add_source(params: dict) -> dict:
    """Make an account available in FileBrowser Quantum. Given the single-source
    model the account's scope (`/home/<user>`) is served automatically and the
    proxy user is auto-provisioned on first login; the concrete work here is to
    (a) validate + confirm the account's home exists, and (b) apply the
    ownership-mitigation ACL so FB-created files stay account-usable. Idempotent.
    """
    username = params["username"]
    home = _account_home(username)
    _apply_account_acl(username, home)
    return {"username": username, "source": SOURCE_NAME, "scope": f"/{username}", "path": home}


def remove_source(params: dict) -> dict:
    """Stop serving an account. There is deliberately nothing to delete at the
    FB Quantum layer: the shared /home source covers every account, and the
    account's auto-provisioned FB user record (scoped to its now-removed home)
    is benign -- on terminate `userdel --remove` deletes the home + files, which
    drop from the index on the next reindex, and if the username is later reused
    the record maps to the same path again. Validated + idempotent so the
    lifecycle wiring + audit trail stay symmetric with add_source."""
    username = params["username"]
    validate_username(username)
    return {"username": username, "status": "removed"}


def refresh_all_sources(params: dict | None = None) -> dict:
    """Backfill: apply add_source (the ownership ACL) to every active account.
    Needed once at rollout so accounts that already existed before this feature
    get the ACL too — otherwise FileBrowser-created (root-owned) files in their
    homes would be inaccessible to them. Idempotent; per-account failures are
    collected, never fatal. Not called on every daemon start (setfacl -R over a
    large home isn't free) — run it once as part of the rollout."""
    from sqlalchemy import select

    from shared.db import read_session
    from shared.models import Account

    with read_session() as db:
        # active + suspended: a suspended account keeps its file-manager source
        # (goal: "Suspend: keep source"), so it needs the ownership ACL too;
        # terminated accounts (home removed) are excluded.
        usernames = list(
            db.scalars(
                select(Account.username).where(Account.status.in_(["active", "suspended"]))
            ).all()
        )
    results = []
    for username in usernames:
        try:
            add_source({"username": username})
            results.append({"username": username, "ok": True})
        except Exception as exc:  # noqa: BLE001
            logger.warning("fb refresh_all: %s failed: %s", username, exc)
            results.append({"username": username, "ok": False, "detail": str(exc)})
    return {"results": results, "ok_count": sum(1 for r in results if r["ok"]), "total": len(results)}


def status(params: dict | None = None) -> dict:
    active = run(["systemctl", "is-active", SERVICE_NAME], timeout=10)
    enabled = run(["systemctl", "is-enabled", SERVICE_NAME], timeout=10)
    return {
        "service": SERVICE_NAME,
        "active": active.stdout.strip() or active.stderr.strip(),
        "enabled": enabled.stdout.strip() or enabled.stderr.strip(),
        "binary_installed": os.path.exists(settings.filebrowser_bin),
        "config": settings.filebrowser_config,
        "source_path": os.path.realpath(settings.home_base),
        "header": settings.filebrowser_header,
        "internal_url": settings.filebrowser_internal_url,
    }


def open_access(params: dict) -> dict:
    """Validate that an account's files can be opened and return its scope. The
    launch endpoint calls this so that opening a file manager session is an
    audited RPC (dispatch() records actor/role/target uniformly) -- this is how
    admin file access lands in the audit log."""
    username = params["username"]
    home = _account_home(username)
    return {"username": username, "scope": f"/{username}", "path": home}


# --- lifecycle hook wrappers (registered in server.py) ---------------------


def add_source_for_account(account) -> None:
    add_source({"username": account.username})


def remove_source_for_account(account) -> None:
    remove_source({"username": account.username})
