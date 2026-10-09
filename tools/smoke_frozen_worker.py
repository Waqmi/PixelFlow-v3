#!/usr/bin/env python3
"""Check pose ONNX session startup through the packaged Windows Worker."""

from __future__ import annotations

import argparse
import base64
from collections import deque
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

from PIL import Image


# ONNX ModelProto, IR 8 / default opset 13: float input [1,3,640,640],
# Constant + Expand float pose output [1,56,57], and an input echo to retain the input.
# It contains 57 duplicate, confident COCO-like pose rows; no accuracy is tested.
POSE_FIXTURE = base64.b64decode(
    "CAgSG1BpeGVsRmxvdyBGcm96ZW4gUG9zZSBTbW9rZTr6AgqcAhIGb3V0cHV0IghDb25zdGFudCqHAgoFdmFsdWUq+gEIAQg5EAFCC3Bvc2Vfb3V0cHV0SuQBAACgQwAAoEMAAABEAAAQRDMzcz8AAKBDAAAgQzMzcz8AAKBDAAAvQzMzcz8AAKBDAAA+QzMzcz8AAKBDAABNQzMzcz8AAKBDAABcQzMzcz8AAKBDAABrQzMzcz8AAKBDAAB6QzMzcz8AAKBDAICEQzMzcz8AAKBDAACMQzMzcz8AAKBDAICTQzMzcz8AAKBDAACbQzMzcz8AAKBDAICiQzMzcz8AAKBDAACqQzMzcz8AAKBDAICxQzMzcz8AAKBDAAC5QzMzcz8AAKBDAIDAQzMzcz8AAKBDAADIQzMzcz8AAAAAoAEEEhtQaXhlbEZsb3cgRnJvemVuIFBvc2UgU21va2VaIgoGaW1hZ2VzEhgKFggBEhIKAggBCgIIAwoDCIAFCgMIgAViGAoGb3V0cHV0Eg4KDAgBEggKAggBCgIIOUIECgAQDQ==",
    validate=True,
)
if len(POSE_FIXTURE) != 418:
    raise RuntimeError(f"unexpected frozen pose fixture length: {len(POSE_FIXTURE)}")
TIMEOUT_SECONDS = 90
CLEANUP_SECONDS = 5
REQUEST_ID = "frozen-pose-smoke"
SHUTDOWN_ID = "frozen-pose-smoke-shutdown"


class SmokeError(RuntimeError):
    pass


def _safe(value: object, temp_root: Path, worker_dir: Path) -> str:
    text = str(value)
    for path, label in ((temp_root, "<smoke-temp>"), (worker_dir, "<worker-dir>"), (os.getenv("GITHUB_WORKSPACE", ""), "<workspace>")):
        if path:
            text = text.replace(str(path), label).replace(str(path).replace("\\", "/"), label)
    text = re.sub(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n\"']+", "<path>", text)
    return re.sub(r"(?<![\w.])/(?:[^/\s:]+/)+[^/\s:]*", "<path>", text).strip()


def _read_event(lines: queue.Queue[object], deadline: float) -> dict[str, object]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SmokeError(f"Worker exceeded the {TIMEOUT_SECONDS}s end-to-end timeout")
    try:
        line = lines.get(timeout=remaining)
    except queue.Empty as error:
        raise SmokeError(f"Worker exceeded the {TIMEOUT_SECONDS}s end-to-end timeout") from error
    if isinstance(line, tuple) and len(line) == 2 and line[0] == "reader_error":
        raise SmokeError(f"Worker {line[1]} reader failed")
    if line is None:
        raise SmokeError("Worker closed JSONL stdout unexpectedly")
    if not isinstance(line, str):
        raise SmokeError("Worker output reader returned invalid data")
    try:
        event = json.loads(line)
    except json.JSONDecodeError as error:
        raise SmokeError("Worker wrote invalid JSONL") from error
    if not isinstance(event, dict):
        raise SmokeError("Worker emitted a non-object JSONL event")
    return event


def _summary(event: dict[str, object]) -> str:
    payload = event.get("payload")
    fields = []
    if isinstance(payload, dict):
        fields = [f"{key}={payload[key]}" for key in ("phase", "purpose", "status", "error_code", "failed_stage") if key in payload]
    return f"{event.get('type', '?')} request_id={event.get('request_id', '?')} " + " ".join(fields)


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                timeout=max(0.1, cleanup_deadline - time.monotonic()),
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        process.kill()
    try:
        process.wait(timeout=max(0.1, cleanup_deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()


def _diagnostics(log_path: Path, stderr_tail: deque[str], temp_root: Path, worker_dir: Path) -> list[str]:
    lines = [f"stderr: {_safe(line, temp_root, worker_dir)}" for line in stderr_tail if line.strip()]
    try:
        records = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
    except OSError:
        records = []
    for line in records:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            fields = [f"{key}={_safe(record[key], temp_root, worker_dir)}" for key in ("stage", "phase", "purpose", "error_code", "failed_stage", "exception", "message") if record.get(key) is not None]
            lines.append("worker-log: " + " ".join(fields))
    return lines[-20:]


def smoke(worker: Path) -> None:
    if os.name != "nt":
        raise SmokeError("frozen Worker smoke must run on Windows")
    if not worker.is_file():
        raise SmokeError("packaged Worker executable is missing")

    # Reserve a bounded cleanup window so the entire step stays within 90s.
    deadline = time.monotonic() + TIMEOUT_SECONDS - CLEANUP_SECONDS
    with tempfile.TemporaryDirectory(prefix="pixelflow-frozen-smoke-") as temp_name:
        temp_root = Path(temp_name)
        product_root = temp_root / "input"
        model_root = temp_root / "models"
        product_root.mkdir()
        (model_root / "pose").mkdir(parents=True)
        with Image.new("RGB", (128, 96), (128, 128, 128)) as image:
            image.save(product_root / "model-photo.png", format="PNG")
        (model_root / "pose" / "smoke.onnx").write_bytes(POSE_FIXTURE)
        log_path = temp_root / "worker.log"
        env = {**os.environ, "PIXELFLOW_ENGINE_MODE": "v3", "PIXELFLOW_WORKER_LOG": str(log_path)}
        process = subprocess.Popen(
            [str(worker)], cwd=worker.parent, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        lines: queue.Queue[object] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=30)

        def read_stdout() -> None:
            try:
                for line in process.stdout:
                    lines.put(line)
            except Exception as error:  # wake the main thread instead of leaving it to timeout
                lines.put(("reader_error", f"stdout/{type(error).__name__}"))
            finally:
                lines.put(None)

        def read_stderr() -> None:
            try:
                for line in process.stderr:
                    stderr_tail.append(line.rstrip())
            except Exception as error:  # stdout wait also observes stderr reader failures
                lines.put(("reader_error", f"stderr/{type(error).__name__}"))

        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        seen: deque[str] = deque(maxlen=30)
        ready = session_ready = fallback = False
        stopped = False
        terminal: dict[str, object] | None = None
        try:
            request = {
                "protocol": 1, "request_id": REQUEST_ID, "type": "classify_materials",
                "payload": {
                    "product_root": str(product_root), "category": "long_pants",
                    "local_model_enabled": True, "local_model_path": str(model_root),
                },
            }
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            while terminal is None:
                event = _read_event(lines, deadline)
                seen.append(_safe(_summary(event), temp_root, worker.parent))
                if event.get("type") == "ready" and event.get("request_id") == "worker":
                    ready = True
                if event.get("request_id") != REQUEST_ID:
                    continue
                payload = event.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                phase = str(payload.get("phase", "")).lower().replace("-", "_")
                purpose = str(payload.get("purpose", "")).lower()
                status = str(payload.get("status", "")).lower()
                if event.get("type") == "progress" and phase.replace("_", "") in {"warmupready", "sessionready"} and purpose == "pose" and status == "ready":
                    session_ready = True
                if purpose == "pose" and (phase.replace("_", "") == "modelfallback" or status == "fallback"):
                    fallback = True
                if event.get("type") in {"done", "error", "cancelled"}:
                    terminal = event

            process.stdin.write(json.dumps({"protocol": 1, "request_id": SHUTDOWN_ID, "type": "shutdown", "payload": {}}) + "\n")
            process.stdin.flush()
            shutdown_done = False
            while not shutdown_done:
                event = _read_event(lines, deadline)
                seen.append(_safe(_summary(event), temp_root, worker.parent))
                shutdown_done = event.get("request_id") == SHUTDOWN_ID and event.get("type") == "done"
            process.stdin.close()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SmokeError(f"Worker exceeded the {TIMEOUT_SECONDS}s end-to-end timeout")
            process.wait(timeout=remaining)
            if process.returncode != 0:
                raise SmokeError(f"Worker exited with code {process.returncode}")
            if not ready:
                raise SmokeError("Worker did not emit its protocol ready event")
            if terminal.get("type") != "done":
                payload = terminal.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                raise SmokeError(f"classification failed ({payload.get('error_code') or payload.get('code') or terminal.get('type')})")
            if fallback:
                raise SmokeError("pose model emitted model_fallback")
            if not session_ready:
                raise SmokeError("no request-correlated pose warmup_ready/session_ready event was observed")
        except Exception as error:
            _stop(process)
            stopped = True
            for line in seen:
                print(f"event: {line}", file=sys.stderr)
            for line in _diagnostics(log_path, stderr_tail, temp_root, worker.parent):
                print(line, file=sys.stderr)
            if isinstance(error, SmokeError):
                raise
            raise SmokeError(_safe(error, temp_root, worker.parent) or type(error).__name__) from error
        finally:
            if not stopped:
                _stop(process)
    print("PASS: pose ONNX session initialized in frozen Worker spawn process; synthetic pose output does not test accuracy")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, type=Path, help="PyInstaller onedir Worker executable")
    try:
        smoke(parser.parse_args().worker.expanduser().resolve())
    except SmokeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print(f"ERROR: Worker exceeded the {TIMEOUT_SECONDS}s timeout", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"ERROR: could not start packaged Worker ({error.strerror or type(error).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
