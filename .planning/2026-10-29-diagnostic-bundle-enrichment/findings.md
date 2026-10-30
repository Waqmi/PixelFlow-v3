# Findings

## Current behavior
- Desktop Host appends Worker stderr to `~/Library/Logs/PixelFlow/worker.log` on macOS. At 5 MiB it deletes `.log.1` and renames the current file to `.log.1`.
- ZIP export currently contains `diagnostics.json`, `failures.txt`, `last-result.json`, `resume-task.json`, and the last 256 KiB of only the current `worker.log`.
- Worker stdout carries the JSONL protocol and is forwarded to the frontend, but is not persisted by the Host.
- V3 Worker writes per-source generation evidence under `~/Library/Caches/PixelFlow/diagnostics/`; result JSON contains `diagnostics_path`, but the archive does not include the file.
- `diagnostics.json` and `resume-task.json` include absolute input/output/model paths. `last-result.json` and failure reports may also include full source/output paths.

## Risks and design constraints
- Keep event and ZIP payload bounded; do not record arbitrary base64 preview payloads.
- Diagnostics may contain user names and filesystem locations. Export should default to redacted paths while retaining useful basenames/relative names.
- Hash only generated files listed by the current task result, not an unrestricted scan of the output tree.
- Preserve enough event fields to diagnose stage/progress rollback: timestamp, request id, type, phase, completed, total, state, and error code/message.

## Delivered behavior
- ZIP now includes current and rotated stderr logs, current and rotated bounded Worker event JSONL, sanitized app/result/resume snapshots, and the per-source diagnostics artifact when its path passes the cache-root allowlist.
- Worker event records retain timestamp, request id, type, phase/state, progress counters, errors, source basename, and result counts; large result arrays are summarized.
- V3 result includes task start/end timestamps, per-output relative path/size/bytes/SHA-256 inventory, duplicate hash groups, omission counters, and a diagnostics artifact SHA-256.
- Per-source diagnostics uses schema version 2 with relative input/output paths. Missing or inaccessible artifacts produce an explanatory text entry instead of failing export.
