"""Narrow runtime-link policy shared by account-UID restore workers."""
from pathlib import Path
import re
import stat


def standard_python_venv_link(relative: str, target: str, home: Path) -> bool:
    """Allow an inert standard interpreter link, never a write through it."""
    if not re.fullmatch(r'pythonapps/[a-zA-Z0-9_-]+/venv/bin/python(?:3(?:\.[0-9]{1,2})?)?', relative):
        return False
    try:
        (home / relative).parent.resolve().relative_to(home.resolve())
    except (ValueError, OSError):
        return False
    if re.fullmatch(r'python(?:3(?:\.[0-9]{1,2})?)?', target):
        return True  # Relative aliases remain in the same validated bin dir.
    if not re.fullmatch(r'/usr/bin/python3(?:\.[0-9]{1,2})?', target):
        return False
    try:
        interpreter = Path(target).resolve(strict=True)
        metadata = interpreter.stat()
    except OSError:
        return False
    return (bool(re.fullmatch(r'/usr/bin/python3(?:\.[0-9]{1,2})?', str(interpreter)))
            and stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
            and not metadata.st_mode & 0o022)
