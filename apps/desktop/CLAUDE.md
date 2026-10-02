[根目录](../../CLAUDE.md) > apps > **desktop**

# apps/desktop

## 变更记录

- 2026-10-02：初始化文档。

## 职责

PixelFlow 桌面宿主：Vue 3 + TypeScript + Vite 前端（`src/`）与 Tauri 2 Rust Host（`src-tauri/`）。前端负责设置、素材分类确认、任务进度、结果复核、主题；Host 负责拉起/监督 Python Worker、转发 JSONL、原生文件对话框、诊断包导出。包名 `pixelflow-desktop`，版本 `3.0.0-beta.1`，Tauri `identifier` 为 `com.pixelflow.desktop`。

## 入口

- 前端：`index.html` -> `src/main.ts` -> `src/App.vue`
- Host：`src-tauri/src/main.rs` 的 `main()`
- Tauri 配置：`src-tauri/tauri.conf.json`（`beforeBuildCommand` = `npm run worker:build && npm run build`；`frontendDist` = `../dist`；资源映射 `worker/dist-ondir/pixelflow-worker` -> `pixelflow-worker`；窗口 1120x848，最小 900x768）

## 关键文件

| 文件 | 职责 |
|---|---|
| `src/App.vue`（约 1700 行） | 主界面与大部分逻辑；`expectedRulesVersion`（当前 `v3-composition-10`）在 ready 事件中与 Worker 的 `rules_version` 比对，不一致则提示 |
| `src/workerProtocol.ts` | 协议常量与类型：`WORKER_REQUEST_TYPES`、`WORKER_EVENT_TYPES`、`GenerationPayload`、`ClassificationPayload`、`MaterialType`、`parseWorkerMessage`、`createRequest` |
| `src/workerClient.ts` | `WorkerClient`（按 request_id 发送/订阅）、`WorkerBridge` 接口、`windowWorkerBridge()`（读 `window.__PIXELFLOW_WORKER__`） |
| `src/tauriBridge.ts` | `installTauriWorkerBridge()`：监听 Tauri 事件 `worker-line`、`worker-exit`，通过 `invoke('worker_send')` 发送 |
| `src/composables/useTaskProgress.ts` | 任务进度状态 |
| `src/components/SettingsAboutView.vue` | 设置页“关于”（版本、Worker 哈希、规则版本、模型状态） |
| `src-tauri/src/main.rs`（约 780 行） | 见下 |
| `src-tauri/capabilities/main.json` | Tauri 权限 |
| `src-tauri/Cargo.toml` / `Cargo.lock` | Rust 依赖 |

`src-tauri/gen/`、`dist/`、`node_modules/` 为生成物，不要改。

## Rust Host（main.rs）

Tauri commands：`worker_send`、`pick_directory`、`pick_file`、`choose_save_path`、`save_text_file`、`export_diagnostics`（生成含 CRC32 的 ZIP，校验条目名防路径穿越）、`open_path`。
- `spawn_worker`：优先使用 App 资源目录下的 `pixelflow-worker`（文件或同名目录内可执行文件）；否则开发模式用 Python，目录取环境变量 `PIXELFLOW_WORKER_DIR`（默认 `../../worker`），解释器可由 `PIXELFLOW_PYTHON` 指定。
- 重启策略：`reserve_restart_slot` 限制 60 秒内最多 3 次重启并带退避；Worker 退出通过 `worker-exit` 事件通知前端（含 `task_id`、`retryable`）。
- 预览安全：`validate_preview_request` / `path_within` 对 `read_preview` 做 canonical 路径范围校验。
- Worker stderr 写滚动日志（`worker_log_path`），诊断包会带最近日志。
- 单元测试在文件末尾（重启预算、退出载荷、ZIP 记录与穿越拒绝），`cargo test` 运行。

## 与 Worker 的通信

UI -> `WorkerClient.send` -> `worker_send` -> Worker stdin；Worker stdout 每行 -> `worker-line` 事件 -> `parseWorkerMessage`。没有 Tauri Host（纯 `npm run dev`）时界面显示“Worker 未连接”，不伪造结果。请求/事件类型见 [packages/protocol/CLAUDE.md](../../packages/protocol/CLAUDE.md)。`GenerationPayload.force_rerender` 对应“强制重跑”。

## 运行 / 构建

```bash
cd apps/desktop
npm ci                 # 或 npm install
npm run dev            # Vite 开发服务器 localhost:5173
npm run build          # vue-tsc --noEmit && vite build（类型检查即前端“测试”）
npm run protocol:check # python3 ../../tools/check_protocol_schema.py
npm run worker:build   # python3 ../../worker/build_worker.py
npm run tauri:dev
npm run tauri:build    # 先构建 Worker（onedir）再打包
cd src-tauri && cargo check && cargo test
```

README 中沙箱构建示例：`CARGO_HOME=/private/tmp/pixelflow-cargo-home CARGO_TARGET_DIR=/private/tmp/pixelflow-cargo-target CARGO_NET_OFFLINE=true npm run tauri:build`。前端目前没有单元测试框架（`docs/STATUS.md` 记录 Vitest 待加）。

## 注意事项

- 打包后的 `.app` 若用 `ditto` 复制，需重新 ad-hoc 签名（`codesign --force --deep --sign - <app>`，再 `--verify --deep --strict`）。默认 DMG bundler 在 STATUS 中记录为失败；Developer ID 签名/公证未完成。
- 修改规则版本时同步 `expectedRulesVersion`（见根文档四处同步）。
- 修改协议类型时同步 `schema.json` 与 `workerProtocol.ts`，并跑 `npm run protocol:check`。
- CI（`.github/workflows/v3.yml`）desktop job：`npm ci`、`protocol:check`、`build`、创建占位 `worker/dist-ondir/pixelflow-worker`、`cargo check`。
- 测试 App 时用“强制重跑”，否则同名输出被复用；给用户看的图放 `~/Desktop/PixelFlow/05_测试素材与输出/`。
