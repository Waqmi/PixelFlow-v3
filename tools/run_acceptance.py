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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-desktop", action="store_true")
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
            "desktop_ui": "pending: run T2 host-permission regression and attach desktop-app-ui-audit-manual.json",
            "release": "pending: signing, notarization, Windows packaging",
        },
    }
    report = output / "acceptance.json"
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report), "passed": all(item["passed"] for item in checks)}, ensure_ascii=False))
    return 0 if all(item["passed"] for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
