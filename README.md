# PixelFlow v3 beta

PixelFlow v3 beta is a clean architecture rewrite of the v2.6 desktop app.
The first milestone freezes v2.6 behavior before any Vue/Tauri UI migration.

## Current milestone

This repository currently contains the phase-one baseline and Worker protocol
scaffold. The production UI is intentionally not started yet.

- v2.6 feature parity checklist: `docs/v2.6-feature-parity.md`
- output baseline plan: `docs/v2.6-output-baseline.md`
- architecture decisions: `docs/architecture.md`
- Worker protocol draft: `docs/worker-protocol.md`
- phase-one acceptance: `docs/phase1-acceptance.md`
- fixed fixture manifest: `fixtures/manifest.json`

## Planned architecture

```text
Tauri 2 + Vue 3 + TypeScript
              |
       versioned JSONL protocol
              |
       persistent Python Worker
              |
 Pillow / NumPy / ONNX Runtime / SQLite
```

The v2.6 source remains in `../PixelFlow-App-v2.0` and is not modified by
this scaffold.

## Phase-one self-check

```bash
cd worker
python3 -m unittest tests/test_contracts.py
```
