from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates_ui"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _human_bytes(n) -> str:
    """Phase 2 feature 5: usage numbers are stored/transmitted as raw
    bytes (matches what du/information_schema report -- no lossy unit
    conversion baked into the data itself); this filter is presentation
    only, applied at render time."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


templates.env.filters["human_bytes"] = _human_bytes
