"""Write a bounded, machine-readable and human-readable pytest skip report.

Loaded from pytest.ini so local runs and CI shards use the same accounting.
No test parameters or captured output are stored: only node ids, phases,
normalized reasons and broad feature labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re

import pytest


_SKIPS: list[dict[str, object]] = []


def pytest_load_initial_conftests(early_config, parser, args) -> None:
    """Refuse an unbounded direct invocation before conftest creates /tmp data."""
    if os.environ.get("BORON_TEST_RUN_DIR"):
        return
    if os.environ.get("BORON_TEST_ALLOW_DIRECT_PYTEST") == "1":
        return
    raise pytest.UsageError(
        "Boron tests require the bounded workspace. Use scripts/test.sh "
        "targeted|quick|full (or scripts/ci_backend.sh)."
    )


def pytest_sessionstart(session) -> None:
    _SKIPS.clear()


def _reason(report) -> str:
    value = report.longrepr
    if isinstance(value, tuple) and len(value) >= 3:
        value = value[2]
    value = re.sub(r"^Skipped:\s*", "", str(value)).strip()
    # UID/GID values vary by runner and add no useful distinction.
    return re.sub(
        r"runner cannot map disposable uid/gid \d+:\d+",
        "runner cannot map disposable uid/gid <uid>:<gid>",
        value,
    )[:500]


def feature_labels(nodeid: str) -> list[str]:
    """Return stable, intentionally broad labels for CI skip triage."""
    text = nodeid.lower()
    labels: set[str] = set()
    rules = {
        "migration": ("migration", "schema_migration"),
        "rollback": ("rollback", "undo"),
        "update": ("test_update", "test_release", "update_"),
        "install": ("installer", "install_", "_install"),
        "restore": ("restore", "recovery"),
        "isolation": ("isolation", "namespace", "cgroup", "inode_quota"),
        "mail": ("mail", "dovecot", "postfix", "imap"),
        "backup": ("backup", "snapshot", "restic"),
        "waf": ("waf", "modsecurity"),
        "network": ("socket", "port", "ssh"),
    }
    for label, terms in rules.items():
        if any(term in text for term in terms):
            labels.add(label)
    return sorted(labels or {"other"})


def _record(report) -> None:
    if not report.skipped:
        return
    nodeid = str(report.nodeid)
    item = {
        "nodeid": nodeid,
        "phase": str(getattr(report, "when", "collection")),
        "reason": _reason(report),
        "features": feature_labels(nodeid),
    }
    if item not in _SKIPS:
        _SKIPS.append(item)


def pytest_runtest_logreport(report) -> None:
    _record(report)


def pytest_collectreport(report) -> None:
    _record(report)


def _report_directory() -> Path:
    explicit = os.environ.get("BORON_TEST_REPORT_DIR", "").strip()
    if explicit:
        return Path(explicit)
    run_dir = os.environ.get("BORON_TEST_RUN_DIR", "").strip()
    if run_dir:
        return Path(run_dir) / "artifacts"
    return Path(".test-artifacts")


def pytest_sessionfinish(session, exitstatus) -> None:
    output = _report_directory()
    output.mkdir(parents=True, exist_ok=True)
    by_reason: dict[str, list[str]] = defaultdict(list)
    by_feature: dict[str, list[str]] = defaultdict(list)
    for item in sorted(_SKIPS, key=lambda row: str(row["nodeid"])):
        by_reason[str(item["reason"])].append(str(item["nodeid"]))
        for feature in item["features"]:
            by_feature[str(feature)].append(str(item["nodeid"]))
    payload = {
        "schema": 1,
        "exit_status": int(exitstatus),
        "total_skipped": len(_SKIPS),
        "by_reason": {
            reason: {"count": len(nodes), "tests": nodes}
            for reason, nodes in sorted(by_reason.items())
        },
        "by_feature": {
            feature: {"count": len(nodes), "tests": sorted(set(nodes))}
            for feature, nodes in sorted(by_feature.items())
        },
        "skips": _SKIPS,
    }
    (output / "skip-report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Pytest skip report",
        "",
        f"Total skipped: **{len(_SKIPS)}**",
        "",
        "## By reason",
        "",
        "| Count | Reason |",
        "|---:|---|",
    ]
    for reason, nodes in sorted(by_reason.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        safe = reason.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {len(nodes)} | {safe} |")
    lines += ["", "## By feature", "", "| Count | Feature |", "|---:|---|"]
    for feature, nodes in sorted(by_feature.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        lines.append(f"| {len(set(nodes))} | {feature} |")
    lines += ["", "## Tests", ""]
    for item in sorted(_SKIPS, key=lambda row: str(row["nodeid"])):
        labels = ", ".join(item["features"])
        lines.append(f"- `{item['nodeid']}` — {item['reason']} ({labels})")
    (output / "skip-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
