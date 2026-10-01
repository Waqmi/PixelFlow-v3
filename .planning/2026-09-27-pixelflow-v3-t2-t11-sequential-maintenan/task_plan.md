# Task Plan: PixelFlow v3 T2–T11 sequential maintenance

## Goal
按既定计划依次完成 T2 到 T11；每阶段只在有真实实现和可复核证据时标记完成，受环境或真实批次约束的阶段保留明确延期记录。

## Next Step
运行 Worker 全量测试、素材回归和 Vue/Tauri 构建，然后重新打包当前模型裁剪修复后的 `dist/PixelFlow.app`；T2 宿主第二次同目录复用仍需真实宿主操作确认。

## Current Phase
本轮模特逻辑校对、回归和 `.app` 打包已完成；T2 宿主第二次同目录复用与跨平台发布验证仍待真实环境

## Phases

### T2 `.app` 完整回归
- [ ] 分类、手动修改、生成、打开输出目录、增量复用
- [ ] 安全取消和进程残留检查
- [ ] 20 张鞋类素材 Pose/Seg、复核提示、模型缺失回退
- **Status:** partial — first host-permission rerun produced 264 outputs; latest app has been rebuilt and launch/quit verified; host rerun is still required to confirm 264→264; missing shoe fixture remains

### T3 legacy/v3 基线
- [x] 双报告、逐像素指标、差异分类和 contact sheet
- **Status:** complete（正式基线仍有 52 项 needs_fix；candidate2 固定 16 张素材已达 0 差异，待人工视觉复核后决定是否更新正式基线）

### T4 预览与安全
- [x] 预览缓存、canonical scope、CSP、300 张性能证据
- [x] 修复确认窗口缺失 inputRoot 导致所有卡片预览 scope 为空
- **Status:** partial（确认页数据流修复并完成源码验证；桌面打包 app 首屏人工复跑仍待补）

### T5 Tauri Host
- [x] 重启上限、日志滚动、任务 ID、ready 版本、平台行为
- [ ] 故障注入不无限重启/不留孤儿
- **Status:** partial — supervisor and pure fault-boundary tests verified; native platform injection and dialog plugin pending

### T6 功能对齐
- [x] 输入输出校验、主体比例、取消/失败报告、设置项和状态的可自动验证项
- [x] parity 清单已更新，未完成 UI 项保留明确延期原因
- **Status:** partial（完整桌面人工审计、主题/动态/Logo 联调仍待宿主操作）

### T7 onedir 与独立 Python
- [x] onedir 资源、哈希清单、Worker 测试和启动/双跑/退出证据
- [ ] Python 3.12 重建
- **Status:** partial（最新本地测试包使用 `worker/.venv`/Python 3.14.5；本机无 Python 3.12，正式锁定依赖构建仍未完成）

### T8 复核与回退可见性
- [ ] review_hint、保护框、接受/保守重跑、统计
- **Status:** complete（已有 force_rerender 修复）

### T9 前端与 CI
- [x] 进度状态抽出 composable、schema 校验和 CI 配置
- [ ] 完整 views 拆分、Vitest
- **Status:** partial（Worker CI job 已补 `PYTHONPATH=worker`；Vitest 依赖无法在当前无网络环境安装）

### T10 发布
- [ ] macOS DMG/签名/公证/首次启动
- [ ] Windows x64 安装和中文/长路径验证
- **Status:** blocked by platform credentials/runners; ad-hoc 签名 arm64 `.app` 已完成启动、Worker 和真实模型探测证据，Developer ID 签名/公证/DMG/Windows 仍未完成

### T11 legacy 周期
- [ ] beta.2 至少 14 天或 2 个真实批次
- [ ] fallback=0、T3 needs_fix 清零、T8 可追溯
- **Status:** active（真实批次 0，natural fallback 未知，T3 needs_fix=52）

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 继续保留 legacy_engine | T3 仍有 52 项 needs_fix，T11 周期尚未满足删除条件 |
| 不伪造 T2/T10/T11 证据 | UI、签名、公证、Windows 和真实批次需要真实环境或用户运行 |
| 当前正式 onefile hash 以 027a624b… 为准 | 用户计划明确要求；6c5fad… 仅保留为历史，当前 onedir app 为 627ebb… |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| planning directory not found | Initialized selected plan with init-session.sh |
| Baseline probe command typo/timeout | Corrected the command; kept the independently generated candidate evidence and did not alter the formal baseline |
| `npm run tauri:build` failed because Python 3.9 has no PyInstaller | Kept existing onedir artifact authoritative; did not fabricate a rebuilt `.app`; Python 3.12/venv rebuild remains a T7 dependency |
| Focused Worker test command referenced missing `worker.tests.test_process` | Listed `worker/tests`; reran the existing `test_worker_process` module successfully (33/33 focused tests) |
| First feedback app build used a relative `PIXELFLOW_PYTHON` path from `apps/desktop` | Reran with the absolute `worker/.venv/bin/python`; app-only bundle completed and was codesign-verified |
| CUA test host already had two PixelFlow instances, so child-window frame timing could not be isolated reliably | Kept the source-level hidden-until-ready fix and recorded no frame-by-frame pass claim |
| Hidden-until-ready could leave a window invisible if the ready event were lost | Added a bounded 3-second reveal fallback; normal ready event remains the primary path |
| First frozen Worker check used zsh read-only variable `status` | Reran with `rc`; no source impact |
| First frozen Worker check used unavailable `timeout` command | Reran with a direct JSONL lifecycle; Worker exited 0 |
| Picker implementation subtask exceeded its quota | Took the scoped implementation back in the main agent; Cargo checks passed |


## Verification note
- T9 progress composable extraction is implemented and build-verified; Vitest remains explicitly deferred because the current environment has no installable npm dependency path.

## 2026-09-27 maintenance additions
- [x] 设置页版本、Worker 哈希、规则版本和模型文件/SHA-256 状态
- [x] 桌面诊断 ZIP：版本与模型诊断、失败报告、最近结果、最近 Worker 日志
- [x] `tools/run_acceptance.py --full-evidence`：Worker 测试、四类输出引用、冻结 Worker/哈希清单引用和 v3 基线报告
- [x] 同名输出复用/强制覆盖策略、确认页说明和 200 MB 磁盘预警
- **Verification:** Worker 93/93；`npm run protocol:check`；`npm run build`；`cargo test --bin pixelflow-desktop` 4/4；`cargo check`；最新 full evidence report `baselines/v2.6/acceptance-full-evidence-20260927-final/acceptance.json`

## 2026-09-27 feedback maintenance
- [x] Window first-paint background mitigation
- [x] Pose/Seg status visibility in main progress and confirmation summary
- [x] Generation/preview concurrency and slowdown cause recorded with source evidence; renderer parallelization intentionally deferred pending timing baseline
- **Verification:** Vue build, protocol check, Cargo check, focused Worker tests 33/33

## 2026-09-28 settings regression repair
- [x] Remove unreliable hidden-until-ready settings handshake
- [x] Restore immediate settings window creation/show/focus path
- [x] Rebuild and verify app-only arm64 artifact
- **Verification:** Vue build, protocol check, Cargo check, git diff check, ad-hoc codesign, bundled Worker ready/ping/shutdown
- **Known limitation:** Tauri DMG bundling still fails in `bundle_dmg.sh`; the `.app` itself is built and verified.

## 2026-09-28 Pose/Seg TCC prompt repair
- [x] macOS model requests run in the existing Worker process; non-macOS keeps the spawn boundary
- [x] Add regression coverage for direct Pose/Seg probing and no model subprocess
- [x] Rebuild, ad-hoc sign, and verify the final arm64 `.app` and frozen Worker
- **Verification:** focused model-cache/classification tests 25/25; frozen Worker probe reports Pose/Seg `已就绪`, `fallback=false`; Worker ready/probe/shutdown exit 0; `codesign --verify --deep --strict` passed
- **Artifact:** `dist/PixelFlow-v3-beta-no-pose-seg-tcc-prompt.app`; Worker SHA-256 `90c69e079846bdf98489bac49fe6d7aa5ae9be258ac071574b5e8e0125c534c1`
- **Known limitation:** DMG bundling still fails in `bundle_dmg.sh`; no Developer ID/notarization claim.

## 2026-09-28 host-app TCC follow-up
- [x] Replace macOS `osascript` folder/file picker with in-process `NSOpenPanel`
- [x] Compile and test the Tauri host; rebuild and ad-hoc sign the `.app`
- **Verification:** `cargo check`; `cargo test --bin pixelflow-desktop` 4/4; Vue build; protocol check; final app Worker ready/shutdown 0; final binary no longer contains `choose folder`/`choose file` picker strings
- **Artifact:** `dist/PixelFlow.app`
- **Important limitation:** macOS may still show one permission prompt for a newly copied/ad-hoc-signed app identity; the fix removes the separate `osascript` authorization gap and makes the user-selected folder authorization belong to the host app.

## 2026-09-28 report and VIP model-image repair
- [x] Let the report summary grid grow for its second row instead of overlapping report details
- [x] Include model/model-detail sources in v3 `唯品专享1440` output
- **Verification:** Vue build passed; focused VIP regression + override tests 2/2; `worker.tests.test_engine` 35/35; `git diff --check` passed
