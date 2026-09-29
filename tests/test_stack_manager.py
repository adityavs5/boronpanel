import pytest

from daemon import stack_manager
from shared.validation import ValidationError


def test_stack_preview_accepts_only_catalog_targets(monkeypatch):
    monkeypatch.setattr(stack_manager.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 10 * 1024**3})())
    original = stack_manager._package_catalog
    monkeypatch.setattr(stack_manager, "_package_catalog", lambda component, action, target: (["lsphp83"], []) if target == "8.3" else original(component, action, target))
    result = stack_manager.preview({"component": "php", "action": "install", "target": "8.3"})
    assert result["packages"][0] == "lsphp83"
    with pytest.raises(ValidationError, match="Unsupported PHP version"):
        stack_manager.preview({"component": "php", "action": "install", "target": "../../evil"})
    with pytest.raises(ValidationError, match="Unsupported stack operation"):
        stack_manager.preview({"component": "shell", "action": "run", "target": "id"})


def test_stack_preview_blocks_low_disk(monkeypatch):
    monkeypatch.setattr(stack_manager.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 100})())
    monkeypatch.setattr(stack_manager, "_package_catalog", lambda component, action, target: (["mariadb-server"], []))
    result = stack_manager.preview({"component": "mariadb", "action": "update", "target": "10.11"})
    assert result["blockers"]


def test_php_catalog_skips_packages_missing_from_repository(monkeypatch):
    monkeypatch.setattr(stack_manager, "_apt_policy", lambda package: {
        "package": package, "installed": None,
        "candidate": None if package.endswith("-zip") else "1.0",
        "available": not package.endswith("-zip"), "update_available": False,
    })
    packages, skipped = stack_manager._package_catalog("php", "install", "8.1")
    assert "lsphp81" in packages
    assert "lsphp81-zip" not in packages
    assert skipped == ["lsphp81-zip"]


def test_update_preview_blocks_when_component_is_current(monkeypatch):
    monkeypatch.setattr(stack_manager.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 10 * 1024**3})())
    monkeypatch.setattr(stack_manager, "_package_catalog", lambda component, action, target: ([], []))
    result = stack_manager.preview({"component": "openlitespeed", "action": "update", "target": "latest-supported"})
    assert result["packages"] == []
    assert "No package updates" in result["blockers"][0]
