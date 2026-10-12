"""Deterministically split the backend suite across isolated CI runners.

The complete suite is still collected on every runner.  A stable digest of
each node id assigns every test to exactly one shard, so adding or renaming a
test cannot silently omit it from CI.
"""
from __future__ import annotations

import hashlib
import os

import pytest


def shard_for(nodeid: str, total: int) -> int:
    if total < 1:
        raise ValueError("total must be positive")
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % total


def _configuration() -> tuple[int, int] | None:
    raw_index = os.environ.get("BORON_TEST_SHARD")
    raw_total = os.environ.get("BORON_TEST_SHARDS")
    if raw_index is None and raw_total is None:
        return None
    if raw_index is None or raw_total is None:
        raise pytest.UsageError(
            "BORON_TEST_SHARD and BORON_TEST_SHARDS must be set together"
        )
    try:
        index = int(raw_index)
        total = int(raw_total)
    except ValueError as exc:
        raise pytest.UsageError("test shard values must be integers") from exc
    if total < 1 or index < 0 or index >= total:
        raise pytest.UsageError(
            f"invalid test shard {index!r} of {total!r}; index is zero-based"
        )
    return index, total


def pytest_collection_modifyitems(config, items):
    shard = _configuration()
    if shard is None:
        return
    index, total = shard
    selected = [item for item in items if shard_for(item.nodeid, total) == index]
    deselected = [item for item in items if shard_for(item.nodeid, total) != index]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected
    config._boron_shard_summary = (index, total, len(selected), len(deselected))


def pytest_report_header(config):
    shard = _configuration()
    if shard is None:
        return None
    index, total = shard
    return f"Boron deterministic test shard: {index + 1}/{total}"
