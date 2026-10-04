#!/usr/bin/env python3
"""Measure large-batch classification and cancellation without touching inputs."""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def max_rss_mb() -> float:
    value = float(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.
    if sys.platform == "darwin":
        return value / (1024 * 1024)
    return value / 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--cancel-after", type=int, default=128)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
    if not files:
        raise SystemExit(f"no supported images under {root}")

    request_id = "stress-classify"
    cancel_id = "stress-cancel"
    request = {
        "protocol": 1,
        "request_id": request_id,
        "type": "classify_materials",
        "payload": {
            "product_root": str(root),
            "output_root": str(root.parent / ".pixelflow-stress-output"),
            "workers": max(1, args.workers),
        },
    }
    cancel = {
        "protocol": 1,
        "request_id": cancel_id,
        "type": "cancel_generation",
        "payload": {"request_id": request_id},
    }
    shutdown = {"protocol": 1, "request_id": "stress-shutdown", "type": "shutdown", "payload": {}}

    started = time.perf_counter()
    progress_completed = 0
    cancel_sent = False
    terminal: dict[str, object] | None = None
    with tempfile.TemporaryDirectory(prefix="pixelflow-stress-") as output_dir:
        request["payload"]["output_root"] = output_dir
        process = subprocess.Popen(
            [sys.executable, "-m", "pixelflow_core.worker"],
            cwd=Path(__file__).resolve().parents[1] / "worker",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            event = json.loads(line)
            if event.get("request_id") == request_id and event.get("type") == "progress":
                progress_completed = max(progress_completed, int(event.get("payload", {}).get("completed", 0)))
                if not cancel_sent and progress_completed >= args.cancel_after:
                    process.stdin.write(json.dumps(cancel) + "\n")
                    process.stdin.flush()
                    cancel_sent = True
            if event.get("request_id") == request_id and event.get("type") in {"done", "cancelled", "error"}:
                terminal = event
                break
        process.stdin.write(json.dumps(shutdown) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            event = json.loads(line)
            if event.get("request_id") == "stress-shutdown":
                break
        process.stdin.close()
        process.stdout.close()
        process.wait(timeout=30)

    report = {
        "root": str(root),
        "input_count": len(files),
        "cancel_after": args.cancel_after,
        "cancel_sent": cancel_sent,
        "progress_completed": progress_completed,
        "terminal_type": terminal.get("type") if terminal else "missing",
        "terminal_payload": terminal.get("payload", {}) if terminal else {},
        "duration_seconds": round(time.perf_counter() - started, 3),
        "child_max_rss_mb": round(max_rss_mb(), 1),
        "returncode": process.returncode,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["terminal_type"] == "cancelled" and process.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
