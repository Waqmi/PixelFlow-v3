#!/usr/bin/env python3
"""Self-check the cross-platform Host/Worker fault contract statically.

This does not claim to execute Windows.  It catches accidental removal of the
Rust contract pieces that the macOS and Windows CI jobs exercise at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "apps" / "desktop" / "src-tauri" / "src" / "main.rs"


def main() -> int:
    source = MAIN.read_text(encoding="utf-8")
    checks = {
        "restart_budget": "const MAX_RESTARTS: usize = 3;" in source and "RESTART_WINDOW" in source,
        "task_id": '"task_id": task_id' in source and "active_task_id" in source,
        "shutdown_timeout": "Duration::from_secs(10)" in source and ('\\"type\\":\\"shutdown\\"' in source or '"type":"shutdown"' in source),
        "nonzero_exit_restart": "should_restart" in source and "retryable" in source,
        "windows_no_console": "target_os = \"windows\"" in source and "creation_flags(0x08000000)" in source,
    }
    payload = {
        "schema_version": "pixelflow-host-fault-contract-1",
        "checks": checks,
        "macos_execution": "static source check only; cargo test is the executable runtime check",
        "windows_execution": "not executed on this host; run cargo test and fault injection in windows-2022 CI",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
