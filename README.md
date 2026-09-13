# PixelFlow v3 beta

PixelFlow v3 beta is a clean architecture rewrite of the v2.6 desktop app.
The first milestone freezes v2.6 behavior before completing the Vue/Tauri UI migration.

## Current milestone

This repository contains the phase-one baseline, Worker protocol, Vue desktop
UI, and a Tauri Host bridge. macOS arm64 app/DMG bundling is now wired to the
packaged Worker; signing/notarization and Windows packaging remain release work.

- v2.6 feature parity checklist: `docs/v2.6-feature-parity.md`
- output baseline plan: `docs/v2.6-output-baseline.md`
- architecture decisions: `docs/architecture.md`
- Worker protocol v1: `docs/worker-protocol.md`
- phase-one acceptance: `docs/phase1-acceptance.md`
- fixed fixture manifest: `fixtures/manifest.json`
- model fixture manifest: `fixtures/model-manifest.json`
- desktop app: `apps/desktop`

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
this rewrite.

前端开发：

```bash
cd apps/desktop
npm run build
```

Tauri Host 验证：

```bash
cd apps/desktop
CARGO_HOME=/private/tmp/pixelflow-cargo-home \
CARGO_TARGET_DIR=/private/tmp/pixelflow-cargo-target \
CARGO_NET_OFFLINE=true npm run tauri:build
```

## Phase-one self-check

```bash
cd worker
python3 -m unittest tests/test_contracts.py
# 需要 v2.6 依赖环境时：
../../PixelFlow-App-v2.0/.venv/bin/python -m unittest tests/test_engine.py
```

真实 Worker 运行示例：

```bash
cd worker
../../PixelFlow-App-v2.0/.venv/bin/python -m pixelflow_core.worker
```

生成 macOS 当前架构的独立 Worker：

```bash
cd worker
../../PixelFlow-App-v2.0/.venv/bin/python build_worker.py
```

输出位于 `worker/dist/pixelflow-worker`（构建目录已忽略）。该 Worker 已内置
v2.6 Python 模块和 `resources`，Pose/Seg 模型仍通过外部目录配置。
