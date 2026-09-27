# PixelFlow v3 当前状态

更新日期：2026-09-27  
版本：`3.0.0-beta.1`

## 当前结论

- Renderer 独立化技术退出条件已通过。
- Worker 测试、鞋类 Pose/Seg 证据和 macOS arm64 `.app` 启停证据已存在。
- 当前正式 onefile Worker 哈希为 `027a624b...`；旧 `6c5fadabe...` 仅保留为历史证据。当前 onedir `.app` Worker 哈希为 `627ebbff...`，该构建使用 Python 3.9.13，Python 3.12 正式重建仍待 CI/可用环境。
- `.app` 分类、完整生成和增量复用首轮回归已通过；第二次 UI 复用因宿主自动锁定待重跑。正式 legacy/v3 基线仍保留 52 个差异；最新候选 renderer 在固定 16 张素材上达到 0 个像素差异，但尚未改写正式基线，需人工视觉复核后再收口。
- 设置页已提供版本/Worker 哈希/规则版本、Pose/Seg 文件名与 SHA-256 状态；桌面端可导出包含诊断 JSON、失败报告、最近 Worker 日志和最近结果的 ZIP 诊断包。
- v3 同名输出默认复用固定路径，不自动改名或覆盖；显式强制重跑才覆盖，生成前会检查输出磁盘空间并在低于 200 MB 时预警。

## 进行中

| 项目 | 状态 | 证据 |
|---|---|---|
| T1 仓库整理 | 进行中 | `.gitignore`、模型 SHA、未跟踪文件清单 |
| T2 `.app` 完整回归 | ⚠️ 首轮通过，增量复用待宿主复跑 | `desktop-app-ui-audit-host-rerun-20260927.json`：36 张素材首轮生成 264 个输出并完成手动分类；前端已增加确认窗口 stale label 恢复、分类无响应超时和取消 idle/超时收敛，仍需宿主复跑确认 264→264 |
| T3 当前 legacy 基线 | ✅ 基线完成，候选修复待视觉复核 | 正式报告仍为 120/120 配对、52 个需修复；candidate2 固定 16 张素材为 120/120、0 个差异，见 `v3-t3-candidate2-compare-report-20260927.json`，未改写正式基线 |
| T4 预览与安全 | ✅ 已实现，性能待验 | Worker `read_preview` 有界缓存、canonical scope；300 张/300 响应证据见 `preview-300-audit.json`；桌面首屏交互仍待补 |
| T5 Host 稳健性 | ✅ 已实现，平台待验 | stderr 滚动日志、60 秒 3 次重启上限、退避、任务 ID、版本握手和 Windows 无控制台已接入；macOS/Windows 故障注入待补 |
| T6 功能对齐 | 进行中 | `docs/v2.6-feature-parity.md` |
| T7 onedir/Python 3.12 | 进行中 | onedir app 已重建并完成 ping/生成双跑/退出；当前 Python 3.9.13，需在 Python 3.12 环境重跑 |
| T8 复核与回退可见性 | ✅ 已实现，真实批次追踪待补 | 结果页展示 `requires_review`、`review_hint`、保护框、接受/保守重跑、fallback/skip/failure 统计；`apps/desktop/src/App.vue` |
| T9 前端和 CI | 进行中 | schema 运行时校验、协议检查和 CI 已完成；Worker CI job 已固定 `PYTHONPATH=worker`，已将进度状态和关于页抽出；完整 views 拆分与 Vitest 仍待处理（当前环境无法安装新 npm 依赖） |
| T10 发布 | 受环境阻塞 | 未签名 arm64 `.app` 已完成启动/Worker 验证；签名、公证、DMG 和 Windows 安装仍依赖对应环境 |
| T11 legacy 周期 | 🔄 已定义，周期进行中 | [`docs/legacy-cycle-beta.2.md`](legacy-cycle-beta.2.md) 与 [`docs/legacy-cycle-beta.2.json`](legacy-cycle-beta.2.json)；尚未满足 14 天或 2 个真实批次 |

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
- Worker 全量：`PYTHONPATH=worker /opt/homebrew/bin/python3.14 -m unittest discover -s worker/tests -p 'test_*.py'` 93/93 通过。
- `tools/run_acceptance.py --skip-desktop`：通过，报告位于 `baselines/v2.6/acceptance-20260927/acceptance.json`。
- `tools/run_acceptance.py --skip-desktop --legacy-cycle-record docs/legacy-cycle-beta.2.json`：通过（Python 3.14），报告位于 `baselines/v2.6/acceptance-t8-t11-20260927/acceptance.json`；周期仍为 active，真实批次为 0。
- `tools/run_acceptance.py --output baselines/v2.6/acceptance-t2-t11-final-20260927`：通过（Python 3.14，Worker 89/89、协议检查、Vue 构建、`cargo check`）；T10 签名、公证和 Windows 安装仍待对应环境。
- `tools/run_acceptance.py --skip-desktop --full-evidence --output baselines/v2.6/acceptance-full-evidence-20260927-final`：通过（Worker 93/93、四类输出/冻结 Worker/哈希清单引用、candidate2 v3 基线报告）；生成图片目录按仓库规则忽略。
- `cargo test --bin pixelflow-desktop`：通过（4/4，包含重启预算、任务 ID 和诊断 ZIP 结构校验）。
- T3 candidate2 compare：120/120 配对、`different_files=0`、`max_diff=0`、`MAE=0`、PSNR 无穷；正式报告保持 52，未用候选结果替代正式基线。
- onedir `.app` Worker：ping、分类/生成双跑、模型探测和 shutdown 通过；第二次生成 `incremental_reused=1`、`skipped_by_scope=0`。
- `npm run protocol:check`：通过；契约解析、请求 ID、错误事件和超时 self-check 通过。
- `useTaskProgress` self-check：通过（进度上下限和 reduced-motion 立即更新）；未伪造 Vitest/CI 平台结果。
