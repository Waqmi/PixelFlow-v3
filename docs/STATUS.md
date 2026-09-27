# PixelFlow v3 当前状态

更新日期：2026-09-27  
版本：`3.0.0-beta.1`

## 当前结论

- Renderer 独立化技术退出条件已通过。
- Worker 测试、鞋类 Pose/Seg 证据和 macOS arm64 `.app` 启停证据已存在。
- 旧 onefile Worker 哈希 `027a624b...` 保留为历史证据；当前源码重建的 onedir Worker 哈希为 `e9cefbc2...`，待 Python 3.12 正式重建后再固化发布哈希。
- `.app` 分类与完整生成回归已通过；当前已打包 app 的增量复用仍需重建 onedir Worker 后复跑。legacy/v3 基线已完成，52 个像素差异仍需修复或登记。

## 进行中

| 项目 | 状态 | 证据 |
|---|---|---|
| T1 仓库整理 | 进行中 | `.gitignore`、模型 SHA、未跟踪文件清单 |
| T2 `.app` 完整回归 | 进行中 | `v3-recheck-20260927/desktop-app-ui-audit-manual.json`：36 张分类、264 输出、0 失败；旧打包 Worker 的增量跳过待重建后复跑 |
| T3 当前 legacy 基线 | ✅ 基线完成 | `docs/v3-T3-legacy-baseline-2026-09-27.md`；120/120 配对，52 个需修复差异 |
| T4 预览与安全 | ✅ 已实现，性能待验 | Worker `read_preview` 有界缓存和 canonical scope；前端已移除 `read_file_bytes` 回退；Tauri CSP 已设置；300 张首屏实测待补 |
| T5 Host 稳健性 | ✅ 已实现，平台待验 | stderr 滚动日志、60 秒 3 次重启上限、退避、任务 ID、版本握手和 Windows 无控制台已接入；macOS/Windows 故障注入待补 |
| T6 功能对齐 | 进行中 | `docs/v2.6-feature-parity.md` |
| T7 onedir/Python 3.12 | 进行中 | onedir 资源已切换且 `cargo check` 通过；现有 onedir 证据使用旧 Python 环境，需用锁定 Python 3.12 重跑 |
| T8–T11 | 未开始 | T3 已提供差异入口；T7/T2 收尾后继续 |

## 版本与模型

- 目标平台：macOS arm64、Windows x64。
- 当前默认发布策略：暂不提供 Intel，除非出现明确用户需求。
- ONNX 模型不入 Git；SHA-256 记录在 `models/README.md` 和构建证据中。
- 规则版本：`v3-composition-1`。

## 证据约定

- 生成图片和构建目录保持忽略，不进入提交。
- JSON 报告、协议、测试、源码和文档进入版本控制。
- 手工 UI 证据必须使用只含源素材的临时输入目录，不能直接使用包含历史成品的目录。

## 最近验证

- `npm run build`：通过。
- `cargo clean && cargo check`：通过（当前 onedir 资源布局）。
- Worker 协议测试：`worker.tests.test_contracts` 7/7 通过；预览越界拒绝、缓存和 ready 版本握手 smoke 通过。
- Worker 全量：`PYTHONPATH=worker PIXELFLOW_PYTHON=.../.venv/bin/python -m unittest discover` 87/87 通过。
- `tools/run_acceptance.py --skip-desktop`：通过，报告位于 `baselines/v2.6/acceptance-20260927/acceptance.json`。
- `npm run protocol:check`：通过；契约解析、请求 ID、错误事件和超时 self-check 通过。
