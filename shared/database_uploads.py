"""Location shared by the API and helper despite systemd PrivateTmp isolation."""
from pathlib import Path

from shared.config import settings

MAX_SQL_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
SQL_UPLOAD_PREFIX = "boron-db-upload-"


def database_upload_directory() -> Path:
    # The database parent is root-owned; API cannot replace this directory.
    return Path(settings.db_path).parent / "database-imports"
