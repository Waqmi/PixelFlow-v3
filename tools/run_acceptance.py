#!/usr/bin/env python3
"""Run the repeatable v3 checks and write one dated evidence report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = os.environ.get("PIXELFLOW_PYTHON", sys.executable)


def run(name: str, command: list[str], cwd: Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "worker")
    completed = subprocess.run(command, cwd=cwd, env=environment, text=True, capture_output=True)
    return {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "passed": completed.returncode == 0,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_path(value: Path) -> Path:
    """Resolve a CLI path relative to the repository for reproducible records."""
    return value.expanduser() if value.is_absolute() else ROOT / value


def desktop_ui_evidence() -> str:
    """Point the report at the latest checked-in manual audit when available."""
    candidates = sorted(
        (ROOT / "baselines" / "v2.6" / "v3-recheck-20260927").glob(
            "desktop-app-ui-audit-manual-*.json"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return str(candidates[0].relative_to(ROOT))
    return "pending: run T2 host-permission regression and attach desktop-app-ui-audit-manual.json"


def evidence_inventory() -> dict[str, object]:
    """Collect checked-in evidence references without rerunning expensive jobs."""
    base = ROOT / "baselines" / "v2.6" / "v3-recheck-20260927"
    names = {
        "four_kind_outputs": "v3-output-kind-report-20260927.json",
        "frozen_worker": "frozen-worker-ondir-evidence.json",
        "hash_manifest": "worker-artifact-hashes.json",
        "baseline_compare": "../v3-current-20260927-compare-report.json",
    }
    return {
        key: {
            "path": str((base / name).resolve().relative_to(ROOT)) if (base / name).resolve().is_relative_to(ROOT) else str((base / name).resolve()),
            "exists": (base / name).is_file(),
        }
        for key, name in names.items()
    }


def run_renderer_evidence(output: Path) -> dict[str, object]:
    """Generate the fixed v3 output tree and compare it with the legacy tree."""
    baseline_root = output / "v3-baseline"
    report = output / "v3-baseline-report.json"
    return run(
        "renderer_baseline",
        [
            PYTHON,
            "tools/run_renderer_baseline.py",
            str(baseline_root),
            "--mode",
            "v3",
            "--compare-root",
            str(ROOT / "baselines" / "v2.6" / "legacy-current-20260927"),
            "--report",
            str(report),
        ],
        ROOT,
    )


def update_legacy_cycle(record_path: Path, acceptance_report: Path) -> dict[str, object]:
    """Refresh observable cycle evidence without inferring real batches or success."""
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
    else:
        record = {
            "schema_version": "v3-legacy-compatibility-cycle-2",
            "cycle_id": "v3-beta.2-legacy-compatibility",
            "status": "active",
            "minimum_duration_days": 14,
            "minimum_real_batches": 2,
            "completion_gates": {
                "natural_fallback_count": 0,
                "t3_needs_fix_count": 0,
                "t8_traceability": True,
            },
            "observations": {
                "elapsed_natural_days": 0,
                "real_batch_count": 0,
                "natural_fallback_count": None,
                "t3_needs_fix_count": None,
                "t8_traceability": "pending",
            },
            "real_batches": [],
            "excluded_runs": [],
            "evidence": {},
        }
    observations = record.setdefault("observations", {})
    now = datetime.now(timezone.utc)
    observations["last_checked_at"] = now.isoformat()
    started_at = record.get("started_at")
    if started_at:
        try:
            started = datetime.fromisoformat(str(started_at))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            observations["elapsed_natural_days"] = max(0, (now - started).days)
        except ValueError:
            observations["elapsed_natural_days"] = None
    observations["real_batch_count"] = len(record.get("real_batches") or [])

    compare_path = ROOT / "baselines" / "v2.6" / "v3-current-20260927-compare-report.json"
    if compare_path.is_file():
        try:
            compare = json.loads(compare_path.read_text(encoding="utf-8"))
            summary = compare.get("classification_summary") or {}
            if isinstance(summary.get("needs_fix"), int):
                observations["t3_needs_fix_count"] = summary["needs_fix"]
        except (OSError, TypeError, ValueError):
            pass

    evidence = record.setdefault("evidence", {})
    report_ref = str(acceptance_report.relative_to(ROOT)) if acceptance_report.is_relative_to(ROOT) else str(acceptance_report)
    reports = evidence.setdefault("acceptance_reports", [])
    if report_ref not in reports:
        reports.append(report_ref)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-desktop", action="store_true")
    parser.add_argument(
        "--full-evidence",
        action="store_true",
        help="include checked-in four-kind, frozen-worker, baseline and hash evidence references",
    )
    parser.add_argument(
        "--legacy-cycle-record",
        type=Path,
        help="refresh a legacy compatibility cycle JSON without inferring real batches",
    )
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = args.output or ROOT / "baselines" / "v2.6" / f"acceptance-{stamp}"
    output.mkdir(parents=True, exist_ok=True)
    checks = [
        run(
            "worker_tests",
            [PYTHON, "-m", "unittest", "discover", "-s", "worker/tests", "-p", "test_*.py"],
            ROOT,
        ),
    ]
    if not args.skip_desktop:
        checks.append(run("protocol_schema", ["npm", "run", "protocol:check"], ROOT / "apps" / "desktop"))
        checks.append(run("desktop_build", ["npm", "run", "build"], ROOT / "apps" / "desktop"))
        checks.append(run("tauri_cargo_check", ["cargo", "check"], ROOT / "apps" / "desktop" / "src-tauri"))
    generated_evidence: dict[str, object] | None = None
    if args.full_evidence:
        renderer_check = run_renderer_evidence(output)
        checks.append(renderer_check)
        generated_evidence = {
            "renderer_baseline_report": str((output / "v3-baseline-report.json").resolve().relative_to(ROOT))
            if (output / "v3-baseline-report.json").resolve().is_relative_to(ROOT)
            else str((output / "v3-baseline-report.json").resolve()),
            "renderer_baseline_output": str((output / "v3-baseline").resolve().relative_to(ROOT))
            if (output / "v3-baseline").resolve().is_relative_to(ROOT)
            else str((output / "v3-baseline").resolve()),
        }

    payload = {
        "schema_version": "v3-acceptance-1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(ROOT),
        "checks": checks,
        "artifacts": {
            "bundled_worker": {
                "path": "dist/PixelFlow-v3-beta.app/Contents/Resources/pixelflow-worker",
                "sha256": sha256(ROOT / "dist/PixelFlow-v3-beta.app/Contents/Resources/pixelflow-worker"),
            },
            "source_worker": {
                "path": "worker/dist/pixelflow-worker",
                "sha256": sha256(ROOT / "worker/dist/pixelflow-worker"),
            },
            "ondir_worker": {
                "path": "worker/dist-ondir/pixelflow-worker/pixelflow-worker",
                "sha256": sha256(ROOT / "worker/dist-ondir/pixelflow-worker/pixelflow-worker"),
            },
        },
        "manual": {
            "desktop_ui": desktop_ui_evidence(),
            "release": "pending: signing, notarization, Windows packaging",
        },
    }
    if args.full_evidence:
        payload["evidence"] = {**evidence_inventory(), **(generated_evidence or {})}
    report = output / "acceptance.json"
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    cycle_record = None
    if args.legacy_cycle_record:
        cycle_record = update_legacy_cycle(_repo_path(args.legacy_cycle_record), report)
    result = {"report": str(report), "passed": all(item["passed"] for item in checks)}
    if cycle_record is not None:
        result["legacy_cycle_record"] = str(_repo_path(args.legacy_cycle_record))
        result["legacy_cycle_status"] = cycle_record.get("status", "active")
        result["legacy_real_batch_count"] = len(cycle_record.get("real_batches") or [])
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(item["passed"] for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
