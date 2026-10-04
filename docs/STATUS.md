# PixelFlow v3 当前状态

更新日期：2026-10-03
版本：`3.0.0-beta.1`

## 当前结论

- Renderer 独立化技术退出条件已通过，当前收口验收已完成。
- Worker 测试、鞋类 Pose/Seg 证据和 macOS arm64 `.app` 启停证据已存在。
- 当前正式 onefile Worker 哈希为 `027a624b...`；旧 `6c5fadabe...` 仅保留为历史证据。最新本地修复包 `dist/PixelFlow-v3-beta-model-fix.app` 的 onedir Worker 哈希为 `e6f28644...`，使用 Python 3.14.5；Python 3.12 正式重建仍待 CI/可用环境。`17f6ef32...` 和 `627ebbff...` 为此前测试构建。
- `.app` 分类、完整生成、增量复用和 UI 结果页已由用户人工验收通过；旧版像素差异报告已移入历史归档，不再作为当前状态依据。
- 设置页已提供版本/Worker 哈希/规则版本、Pose/Seg 文件名与 SHA-256 状态；桌面端可导出包含诊断 JSON、失败报告、最近 Worker 日志和最近结果的 ZIP 诊断包。
- 已修复模型检测的 `file_version(include_digest=True)` 参数错误；最新修复包在宿主设置页显示 Pose、Seg 均“已就绪”且 SHA-256 匹配。
- v3 同名输出默认复用固定路径，不自动改名或覆盖；显式强制重跑才覆盖，生成前会检查输出磁盘空间并在低于 200 MB 时预警。
- 最新提交 `2f39ae1` 已为 `socks`、`hat`、`gloves`、`knee_pads`、`bag` 接入锚点构图；帽子、手套、长袜已有真实素材渲染验收。本轮已使用 `05_测试素材与输出/护膝` 的 5 个真实图片文件（含 2 个 WebP）完成 v3 Renderer 验证：27 个输出通过、0 个失败、4 个来源需要人工复核，证据见 [`baselines/v3/knee-pads-real-20261003.json`](../baselines/v3/knee-pads-real-20261003.json)。
- 当前工作区的后续修复扩大了手套检测框安全边界，并让带锚点构图的帽子/手套特写保留为模特图；Python 3.14 下 Worker 全量测试为 192 项，191 项通过、1 项既有跳过。

## 进行中

| 项目 | 状态 | 证据 |
|---|---|---|
| T1 仓库整理 | 进行中 | `.gitignore`、模型 SHA、未跟踪文件清单 |
| T2 `.app` 完整回归 | ✅ 用户人工验收通过 | 当前收口报告：[`baselines/v3/current-20261003/acceptance-report.json`](../baselines/v3/current-20261003/acceptance-report.json) |
| T3 当前 legacy 基线 | ✅ 用户人工兼容验收通过 | 旧版机器报告已归档；当前报告不再把历史像素差异作为阻塞项 |
| T4 预览与安全 | ✅ 已实现，性能待验 | Worker `read_preview` 有界缓存、canonical scope；300 张/300 响应证据见 `preview-300-audit.json`；桌面首屏交互仍待补 |
| T5 Host 稳健性 | ✅ 已实现，平台待验 | stderr 滚动日志、60 秒 3 次重启上限、退避、任务 ID、版本握手和 Windows 无控制台已接入；`tools/check_host_fault_contract.py` 与 Rust 单测可运行，Windows 真实故障注入仍待 Windows 环境 |
| T6 功能对齐 | 进行中 | `docs/v2.6-feature-parity.md`；五个剩余品类已接入锚点构图，护膝真实素材验证见 `baselines/v3/knee-pads-real-20261003.json` |
| T7 onedir/Python 3.12 | 进行中 | 当前本机 `.app`/Worker 使用 Python 3.14.5；`tools/build_worker.sh` 和 `PIXELFLOW_PYTHON` 选择器已固定构建解释器，CI 仍使用 Python 3.12，正式 3.12 本机重建未伪称完成 |
| T8 复核与回退可见性 | ✅ 已实现，真实批次追踪可登记 | 结果页展示 `requires_review`、`review_hint`、保护框、接受/保守重跑、fallback/skip/failure 统计；`tools/record_legacy_batch.py` 追加可审计真实批次 |
| T9 前端和 CI | 进行中 | schema 运行时校验、协议检查和 CI 已完成；Worker CI job 已固定 `PYTHONPATH=worker`，已将进度状态和关于页抽出；完整 views 拆分与 Vitest 仍待处理（当前环境无法安装新 npm 依赖） |
| T10 发布 | 受环境阻塞 | 最新 arm64 `.app` 已完成 ad-hoc 签名验证、宿主启动/退出和 Worker ping；Developer ID 签名、公证、DMG 和 Windows 安装仍待完成 |
| T11 legacy 周期 | 🔄 观察中 | 技术验收已完成；legacy 仍保留一个兼容观察周期后再评估删除 |

## 版本与模型

- 目标平台：macOS arm64、Windows x64。
- 当前默认发布策略：暂不提供 Intel，除非出现明确用户需求。
- ONNX 模型不入 Git；SHA-256 记录在 `models/README.md` 和构建证据中。
- 规则版本：`v3-composition-10`。

## 证据约定

- 生成图片和构建目录保持忽略，不进入提交。
- JSON 报告、协议、测试、源码和文档进入版本控制。
- 手工 UI 证据必须使用只含源素材的临时输入目录，不能直接使用包含历史成品的目录。

## 最近验证

- `npm run build`：通过。
- `cargo clean && cargo check`：通过（当前 onedir 资源布局）。
- Worker 协议测试：`worker.tests.test_contracts` 7/7 通过；预览越界拒绝、缓存和 ready 版本握手 smoke 通过。
- Worker 全量（最新本地验证）：`PYTHONPATH="$PWD/worker" worker/.venv/bin/python -m unittest discover -s worker/tests -p 'test_*.py'` 192 项，191 通过、1 项既有跳过；无警告（已迁移至 Pillow `get_flattened_data()` API）。
- `tools/run_acceptance.py --skip-desktop`：通过，报告位于 `baselines/v2.6/acceptance-20260927/acceptance.json`。
- `tools/run_acceptance.py --skip-desktop --legacy-cycle-record docs/legacy-cycle-beta.2.json`：通过（Python 3.14），报告位于 `baselines/v2.6/acceptance-t8-t11-20260927/acceptance.json`；周期仍为 active，真实批次为 0。真实批次使用 `tools/record_legacy_batch.py --real-batch` 登记，smoke/fault injection 不计入。
- `tools/run_acceptance.py --output baselines/v2.6/acceptance-t2-t11-final-20260927`：通过（Python 3.14，Worker 89/89、协议检查、Vue 构建、`cargo check`）；T10 签名、公证和 Windows 安装仍待对应环境。
- `tools/run_acceptance.py --skip-desktop --full-evidence --output baselines/v2.6/acceptance-full-evidence-20260927-final`：通过（Worker 93/93、四类输出/冻结 Worker/哈希清单引用、candidate2 v3 基线报告）；生成图片目录按仓库规则忽略。
- `cargo test --bin pixelflow-desktop`：通过（4/4，包含重启预算、任务 ID 和诊断 ZIP 结构校验）。
- T3 candidate2 compare：120/120 配对、`different_files=0`、`max_diff=0`、`MAE=0`、PSNR 无穷；正式报告保持 52，未用候选结果替代正式基线。
- onedir `.app` Worker：ping、分类/生成双跑、模型探测和 shutdown 通过；第二次生成 `incremental_reused=1`、`skipped_by_scope=0`。
- `npm run protocol:check`：通过；契约解析、请求 ID、错误事件和超时 self-check 通过。
- `useTaskProgress` self-check：通过（进度上下限和 reduced-motion 立即更新）；未伪造 Vitest/CI 平台结果。
- 最新本地 `.app`：`dist/PixelFlow-v3-beta.app`，源码提交 `0097bd9`，Worker 哈希 `17f6ef32...`；证据见 `baselines/archive/2026-09-27-v3-recheck/mac-app-package-latest-20260927.json`。Tauri app-only bundle、ad-hoc codesign 校验、宿主启动/退出和 Worker ready/ping/shutdown 通过；默认 DMG bundler 仍失败。
- 模型检测修复包：`dist/PixelFlow-v3-beta-model-fix.app`，源码提交 `3359911`，Worker 哈希 `e6f28644...`；Worker 94/94 通过，源码/冻结 Worker 对真实 Pose/Seg 模型均返回“已就绪”和完整匹配 SHA-256，宿主设置页点击“测试模型”得到相同结果。见 `baselines/archive/2026-09-27-v3-recheck/model-probe-fix-20260927.json`。旧 `dist/PixelFlow-v3-beta.app` 仍在运行，未覆盖。
- 当前收口 app：`dist/PixelFlow-v3-beta.app`，使用 Python 3.14.5 onedir Worker；Worker 哈希 `8fb8f4b972415187e2a41ceb61f58655961eb2b81bcdcb84c039121f1c314d51`，规则版本 `v3-composition-10`。前端构建、协议检查、Worker 191 项测试、Rust 4 项测试、ad-hoc 签名和 Worker `ready/ping/shutdown` 冒烟均通过；用户人工验收通过。详见 [`baselines/v3/current-20261003/acceptance-report.json`](../baselines/v3/current-20261003/acceptance-report.json)。
