#!/usr/bin/env python3
"""Combine pytest shard skip reports into one bounded CI artifact."""
from __future__ import annotations

from collections import defaultdict
import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    reports = sorted(args.input.rglob("skip-report.json"))
    if not reports:
        raise SystemExit(f"no skip-report.json files found below {args.input}")

    skips: dict[tuple[str, str, str], dict] = {}
    shard_statuses: list[dict] = []
    for path in reports:
        data = json.loads(path.read_text(encoding="utf-8"))
        shard_statuses.append({"report": str(path.relative_to(args.input)),
                               "exit_status": int(data.get("exit_status", 1))})
        for item in data.get("skips", []):
            key = (str(item.get("nodeid", "")), str(item.get("phase", "")),
                   str(item.get("reason", "")))
            skips[key] = {
                "nodeid": key[0], "phase": key[1], "reason": key[2],
                "features": sorted({str(value) for value in item.get("features", [])}),
            }

    items = sorted(skips.values(), key=lambda row: (row["nodeid"], row["phase"]))
    by_reason: dict[str, list[str]] = defaultdict(list)
    by_feature: dict[str, list[str]] = defaultdict(list)
    for item in items:
        by_reason[item["reason"]].append(item["nodeid"])
        for feature in item["features"] or ["other"]:
            by_feature[feature].append(item["nodeid"])

    payload = {
        "schema": 1,
        "reports": shard_statuses,
        "total_skipped": len(items),
        "by_reason": {
            reason: {"count": len(nodes), "tests": sorted(nodes)}
            for reason, nodes in sorted(by_reason.items())
        },
        "by_feature": {
            feature: {"count": len(set(nodes)), "tests": sorted(set(nodes))}
            for feature, nodes in sorted(by_feature.items())
        },
        "skips": items,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "skip-report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    lines = ["# Combined pytest skip report", "",
             f"Total skipped: **{len(items)}**", "", "## By reason", "",
             "| Count | Reason |", "|---:|---|"]
    for reason, nodes in sorted(by_reason.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        safe = reason.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {len(nodes)} | {safe} |")
    lines += ["", "## By feature", "", "| Count | Feature |", "|---:|---|"]
    for feature, nodes in sorted(by_feature.items(), key=lambda pair: (-len(set(pair[1])), pair[0])):
        lines.append(f"| {len(set(nodes))} | {feature} |")
    lines += ["", "## Tests", ""]
    for item in items:
        labels = ", ".join(item["features"] or ["other"])
        reason = item["reason"].replace("\n", " ")
        lines.append(f"- `{item['nodeid']}` — {reason} ({labels})")
    (args.output / "skip-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"combined {len(reports)} reports with {len(items)} skips into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
