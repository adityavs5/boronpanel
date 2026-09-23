"""Names shared by the unprivileged proxy and root service provisioner."""
from pathlib import Path

from shared.config import settings
from shared.validation import validate_username


def account_socket(username: str) -> str:
    return str(Path(settings.filebrowser_runtime_dir) / validate_username(username) / "backend.sock")


def frontend_socket() -> str:
    return str(Path(settings.filebrowser_runtime_dir) / '_frontend' / 'backend.sock')
