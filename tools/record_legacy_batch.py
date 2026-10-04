#!/usr/bin/env python3
"""Append one explicitly confirmed user batch to the legacy-cycle record.

The command intentionally requires ``--real-batch`` so smoke and fault
injection runs cannot be counted by accident.  It stores counts and source
paths without copying image data into the repository.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _number(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _report_counts(report: dict[str, Any]) -> dict[str, int]:
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    return {
        "v3_success": _number(validation.get("passed", report.get("success", report.get("main_images", 0)))),
        "v3_fallback": _number(report.get("fallback_count", len(report.get("fallback_items", []))))
        or int(bool(report.get("fallback"))),
        "review": _number(report.get("review_count", len(report.get("requires_review", [])))),
        "failure": _number(validation.get("failed", report.get("failure_count", len(report.get("failures", []))))),
    }


def _record_path(value: Path) -> Path:
    return value.expanduser() if value.is_absolute() else ROOT / value


def append_batch(record_path: Path, batch: dict[str, Any]) -> dict[str, Any]:
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
    else:
        record = {
            "schema_version": "v3-legacy-compatibility-cycle-2",
            "cycle_id": "v3-beta.2-legacy-compatibility",
            "status": "active",
            "minimum_duration_days": 14,
            "minimum_real_batches": 2,
            "real_batches": [],
            "observations": {},
        }
    if not isinstance(record, dict):
        raise ValueError("legacy cycle record must be a JSON object")
    batches = record.setdefault("real_batches", [])
    if not isinstance(batches, list):
        raise ValueError("real_batches must be a list")
    if any(isinstance(item, dict) and item.get("batch_id") == batch["batch_id"] for item in batches):
        raise ValueError(f"batch_id already exists: {batch['batch_id']}")
    batches.append(batch)
    observations = record.setdefault("observations", {})
    observations["real_batch_count"] = len(batches)
    observations["natural_fallback_count"] = sum(_number(item.get("v3_fallback")) for item in batches if isinstance(item, dict))
    observations["last_real_batch_at"] = batch["completed_at"]
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def self_check() -> None:
    with tempfile.TemporaryDirectory(prefix="pixelflow-legacy-record-") as directory:
        path = Path(directory) / "cycle.json"
        batch = {
            "batch_id": "self-check",
            "completed_at": "2026-10-03T00:00:00+08:00",
            "run_kind": "real_user_batch",
            "real_batch": True,
            "source_paths": ["/tmp/source.png"],
            "v3_success": 2,
            "v3_fallback": 0,
            "review": 0,
            "failure": 0,
            "legacy_fallback_reasons": [],
        }
        result = append_batch(path, batch)
        assert result["observations"]["real_batch_count"] == 1
        assert json.loads(path.read_text(encoding="utf-8"))["real_batches"][0]["real_batch"] is True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-cycle-record", type=Path, default=ROOT / "docs/legacy-cycle-beta.2.json")
    parser.add_argument("--batch-id")
    parser.add_argument("--completed-at", help="ISO-8601 timestamp; defaults to current UTC time")
    parser.add_argument("--source-path", action="append", required=False, default=[])
    parser.add_argument("--v3-success", type=int)
    parser.add_argument("--v3-fallback", type=int)
    parser.add_argument("--review", type=int)
    parser.add_argument("--failure", type=int)
    parser.add_argument("--legacy-fallback-reason", action="append", default=[])
    parser.add_argument("--result-report", type=Path, help="optional Worker result JSON to derive counts")
    parser.add_argument("--real-batch", action="store_true", help="required acknowledgement that this was a user batch")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("record_legacy_batch self-check: passed")
        return 0
    if not args.real_batch:
        parser.error("--real-batch is required; smoke and fault-injection runs are excluded")
    if not args.batch_id:
        parser.error("--batch-id is required")
    if not args.source_path:
        parser.error("at least one --source-path is required")
    report: dict[str, Any] = {}
    if args.result_report:
        report = json.loads(_record_path(args.result_report).read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            parser.error("--result-report must contain a JSON object")
    counts = _report_counts(report)
    for key, value in (("v3_success", args.v3_success), ("v3_fallback", args.v3_fallback), ("review", args.review), ("failure", args.failure)):
        if value is not None:
            counts[key] = max(0, value)
    completed_at = args.completed_at or datetime.now(timezone.utc).isoformat()
    batch = {
        "batch_id": args.batch_id,
        "completed_at": completed_at,
        "run_kind": "real_user_batch",
        "real_batch": True,
        "source_paths": args.source_path,
        **counts,
        "legacy_fallback_reasons": args.legacy_fallback_reason,
        "result_report": str(_record_path(args.result_report).relative_to(ROOT)) if args.result_report and _record_path(args.result_report).is_relative_to(ROOT) else (str(_record_path(args.result_report)) if args.result_report else None),
    }
    record = append_batch(_record_path(args.legacy_cycle_record), batch)
    print(json.dumps({"record": str(_record_path(args.legacy_cycle_record)), "real_batch_count": len(record["real_batches"]), "batch_id": args.batch_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
