from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates_ui"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
