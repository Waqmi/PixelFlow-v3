# PixelFlow v3 beta desktop UI

当前是 Vue 3 + TypeScript + Vite 的前端壳，已包含 v2.6 核心设置、浅色/夜间/跟随系统主题和减少动态效果。

安装依赖后运行：

```bash
npm install
npm run dev
```

页面通过 `window.__PIXELFLOW_WORKER__` 接收 Tauri 注入的 JSONL bridge；当前环境没有 Tauri Host 时会明确显示“Worker 未连接”，不会伪造生成结果。连接后会先发送 `ping`，开始任务后支持进度、完成、取消和错误状态。

Tauri 开发模式需要在 `apps/desktop` 下运行 `npm run tauri:dev`。Host 通过
`PIXELFLOW_PYTHON`（可选）和 `PIXELFLOW_WORKER_DIR`（可选）定位 Python 与
Worker 目录。

Host 已可用 `cargo check` 和 `npm run tauri:build` 构建验证。构建脚本优先使用
`PIXELFLOW_PYTHON`，否则使用 `worker/.venv/bin/python`；两者都不存在时会明确失败，
不会静默切换到系统解释器。CI 仍固定 Python 3.12；本机当前 venv 事实以脚本输出为准。
Tauri 会把 `worker/dist-ondir/pixelflow-worker` 映射为
`Contents/Resources/pixelflow-worker`；当前 Python Worker 是 arm64 构建，签名与
公证仍需按发布环境配置。
