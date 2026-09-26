import pytest

from daemon import stack_manager
from shared.validation import ValidationError


def test_stack_preview_accepts_only_catalog_targets(monkeypatch):
    monkeypatch.setattr(stack_manager.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 10 * 1024**3})())
    result = stack_manager.preview({"component": "php", "action": "install", "target": "8.3"})
    assert result["packages"][0] == "lsphp83"
    with pytest.raises(ValidationError, match="Unsupported PHP version"):
        stack_manager.preview({"component": "php", "action": "install", "target": "../../evil"})
    with pytest.raises(ValidationError, match="Unsupported stack operation"):
        stack_manager.preview({"component": "shell", "action": "run", "target": "id"})


def test_stack_preview_blocks_low_disk(monkeypatch):
    monkeypatch.setattr(stack_manager.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 100})())
    result = stack_manager.preview({"component": "mariadb", "action": "update", "target": "10.11"})
    assert result["blockers"]

