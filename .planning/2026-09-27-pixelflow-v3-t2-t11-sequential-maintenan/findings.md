# Findings & Decisions

## Requirements
- 用户要求按 T2→T11 顺序继续，直到可完成项完成；必须保留真实未完成项和证据限制。
- 所有代码修改使用 Ponytail：最小改动、标准库优先、不新增无必要依赖。

## Research Findings
- 当前 HEAD：`6489633 v3: close review cycle and onedir acceptance`，工作区在任务开始时干净。
- 已有 T3/T8/T11/T7 onedir 证据；T3 compare 仍有 52 项 `needs_fix`。
- 正式 onefile Worker 实际 hash：`027a624bb13bd9a739acfca2a5e53e2723bf777c93d1ab72141251469bee49bd`。
- 当前 onedir `.app` Worker 实际 hash：`627ebbffb0f07e173963809de6dff7e8f851fd532cea58f8c7ca0bfb9a3bbde2`。
- 当前完整 Worker 测试为 89/89；`npm run build`、`cargo check`、`npm run protocol:check` 已通过。
- T10 所需签名、公证、Windows runner/安装环境不可由当前宿主证明。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 先做 T2 真实 UI 检查，再顺序推进 | 用户明确要求按顺序；后续阶段应引用前一阶段证据 |
| 优先复用已有 `.app` 和审计脚本 | 避免重建和新增依赖，符合 Ponytail |
| 每阶段结束更新 task_plan/progress/findings | planning-with-files 强制要求持久化进度和错误 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| No planning directory found | Created `.planning/2026-09-27-pixelflow-v3-t2-t11-sequential-maintenan/` |

## Resources
- `docs/STATUS.md`
- `docs/v3-后续更新与修复计划-2026-09-27.md`
- `tools/run_acceptance.py`
- `baselines/v2.6/v3-recheck-20260927/`

## T2 discovery
- Existing 36-source temporary directory exists at `/private/tmp/pixelflow-v3-sources-36` with exactly 36 files.
- Existing output directory `/private/tmp/pixelflow-v3-output-20260927` exists and must be replaced by a fresh empty output root for a clean UI run.
- `.app` launches under CUA as `PixelFlow-v3-beta`; main window exposes source/output fields, size buttons, logo/VIP/material toggles, and start button.
- CUA can set text fields and invoke buttons; no prior completed incremental skip is present in the old manual audit.

## T2 live UI observations
- First host-permission run: classification completed for 36 files; manual classification changed the first detail image to 商品白底图; confirmation and generation completed with 264 outputs, 0 failures, 0 review, 0 fallback.
- `打开输出文件夹` opened Finder successfully.
- Second same-directory run completed with 252 outputs and report text `检测到素材和设置均未变化，本次已复用上次输出。` The count differs from the first run because the persisted manual classification changes the output set; this is an evidence inconsistency to preserve and investigate, not mark as perfect parity.
- Current report window has no failure/review entries.

## T2 completion decision
- T2 general UI path is evidenced by a live host-permission run and cancellation output.
- The requested 20-image shoe UI run cannot be performed from this checkout: no shoe image directory or 20 shoe source files are present; only `fixtures/shoe-matrix.json` and prior JSON evidence exist.
- T2 remains partial because of the 264 -> 252 incremental count discrepancy and missing shoe source assets.

## T3 verification
- `legacy-current-20260927-report.json` and `v3-current-20260927-report.json` both exist with 120 paired outputs and zero failures.
- Compare report contains pixel metrics and classification summary: `version_difference=0`, `acceptable_implementation_difference=0`, `needs_fix=52`.
- `worker.tests.test_compare` and `worker.tests.test_engine` passed (35 tests).
- T3 is complete as a baseline; the 52 `needs_fix` items remain an explicit T11 gate.

## T4 verification
- `preview-300-audit.json` records 300/300 responses in 6.03 seconds with canonical temporary scope and no stderr.
- Worker preview cache key includes path, mtime_ns, size, max_width and max_height; cache is bounded.
- Tauri Host validates canonical preview path and allowed roots against selected roots; frontend has lazy IntersectionObserver queue and no `read_file_bytes` fallback.
- Tauri CSP is present and restricts default/script/connect sources.
- T4 remains partial only because desktop first-paint interactivity for 300 assets was not separately measured; Worker/security evidence passes.

## T5 verification
- Host code has a 60-second restart window, max 3 attempts, backoff `[250ms, 1s, 2s]`, rolling 5 MiB log, Windows `CREATE_NO_WINDOW`, task ID capture, and ready metadata support.
- Static review found restart failure/exhaustion events dropped the active task ID by passing `None`; this is being fixed before marking T5 evidence complete.
- No native Tauri test harness or platform fault-injection runner exists in this checkout, so macOS/Windows failure injection remains evidence-limited.

## T5 completion decision
- Fixed `restart_worker` to propagate the active task ID through first exit, restart failure, and restart exhaustion events.
- `cargo check` passed after the fix.
- T5 is partial: supervisor behavior is implemented and statically audited, but native platform fault injection and Tauri dialog-plugin migration remain pending.

## 2026-09-27 continued verification
- The acceptance runner currently reports Worker tests and artifact hashes, while its desktop UI field is a hardcoded pending string; this is accurate for release gating but does not link the existing T2 audit.
- Existing shoe evidence is real and persisted (`v3-native-model-evidence.json`, `shoe-composition-evidence.json`); the current checkout lacks the original 20 image fixture, so no new `.app` shoe run should be claimed.
- The T11 JSON is intentionally active: `real_batch_count=0`, `t3_needs_fix_count=52`, and natural fallback remains unknown.

## Annotation follow-up findings
- The T2 count discrepancy is in the UI aggregation layer: it omitted `model_images` and did not prefer the Worker validation total. The fix is limited to `App.vue` output summary aggregation.
- T5 restart policy can be tested without a platform runner by extracting its pure state transition; native process fault injection and dialog plugin packaging still need platform/dependency support.
- `cargo fmt --check` is not a useful gate for this checkout because existing unrelated formatting differences span the file; the new Rust logic passed targeted tests and `cargo check`.

## T3 needs_fix audit (continued)
- The 52 baseline differences are not caused by missing files, dimensions, modes, or a tolerance/statistics bug: all 120 outputs are paired, transparent outputs are exact, and differing files have max pixel differences up to 255 with baseline MAE 6.3367 / PSNR 17.70 dB.
- A minimal candidate in `worker/pixelflow_core/renderer.py` detects light-background foreground touching the source edge and routes that case through the legacy-compatible cover path. It was tested in an ephemeral output tree only; the formal baseline was not rewritten.
- Candidate compare result: `needs_fix` 52 → 28; product-main differences 48 → 24; VIP differences remain 4; transparent differences remain 0. Candidate MAE/PSNR improved to 2.3226 / 21.87 dB.
- The remaining 28 are grouped by reproducible code path: 14 `_07` outputs require opaque light-background extension alignment; 14 `_08` outputs require RGBA main-image category-template alignment. They remain `needs_fix` pending a separate renderer change and visual review.
- Persisted evidence: `baselines/v2.6/v3-recheck-20260927/t3-edge-path-candidate-analysis-20260927.json`.

## T2/T3 corrective verification
- The 264→252 discrepancy had a concrete Worker cause: VIP reuse used `continue` before PNG transparent reuse/counting. The fix preserves the output tree and reports `main=216`, `transparent=12`, `vip=36`, `incremental_reused=264`, `incremental_skipped=true` on the retained 36-source output.
- The safe T3 candidate is limited to the light-background edge detector and legacy-compatible cover branch. It reduces 24 files to exact pixel parity while leaving the formal 52-item baseline untouched.
- An attempted narrow source-filter experiment for RGBA template alignment was invalidated because the fixture's naming/source scope was contaminated; it was reverted and excluded from evidence.

## Product suggestions verification
- 诊断导出不依赖新增 Rust crate：Host 写入标准 store ZIP，限制单条目 10 MiB、总计 32 MiB、日志最多 256 KiB，并拒绝绝对路径、`..` 和重复条目。
- `probe_models` 对已发现模型返回 `model_version.sha256`；前端把它与 `models/README.md` 中的期望值比较并展示匹配状态。模型二进制仍被 `.gitignore` 排除。
- 输出目录策略已在协议、确认页和设置页统一为“同名复用；强制重跑覆盖；不自动重命名”；预检沿现有输出路径的已存在父目录检查可写性和剩余空间。
- full evidence runner 失败原因是 CLI 相对输出路径在 `Path.relative_to(ROOT)` 前未 resolve；修复后报告成功生成，正式 T3 报告仍保留 52，runner 生成的 candidate baseline report 记录 28 项差异。

## T2 host-permission rerun findings
- 当前宿主回归实际走通：36 张素材分类、手动分类变更、确认、108 项任务生成，输出文件数为 264。
- 本轮使用的输出目录是全新目录，避免了历史产物污染；可作为宿主权限重新生成的事实证据。
- 第二次增量复用需要解锁宿主 Mac 后才能继续，当前不能据此声称 264→264 UI 复用通过。
- 解锁后复跑暴露出更具体的 UI 问题：分类进度显示 36/36 但没有发出可见的完成/确认状态；安全取消也未在观察窗口内收敛。应优先检查第二次分类请求的 Worker done/error 事件和前端 request id 过滤，确认是否为多实例 Worker 或旧请求残留。
- 当前 `.app` 构建时间为 18:02；重建当前源码时 `npm run tauri:build` 在 `worker/build_worker.py` 阶段因 Python 3.9 缺少 PyInstaller 失败。因此旧包 UI 卡住不能作为当前源码回归结论。

## T2/T9 corrective findings
- `cancel_generation` 的响应使用取消请求自身的 request ID，而分类/生成终态事件使用原任务 request ID；此前前端未处理取消响应为 `idle` 或取消请求错误，因此任务在竞态结束时可能永久显示“正在安全停止”。现已记录取消请求与目标任务 ID，并加入 idle、错误和超时收敛。
- 确认窗口复用依赖 `WebviewWindow.getByLabel('confirm')`；若窗口标签残留但展示/聚焦失败，分类已完成也无法让用户看到确认页。现已在失败时关闭 stale 窗口并重建。
- 分类 watchdog 按“无进度”而非固定总时长重置，120 秒无 Worker 事件后请求取消并明确提示重试；不能替代宿主权限下的正式回归。
- CI Worker job 从仓库根目录执行测试，显式设置 `PYTHONPATH=worker`，避免依赖本地环境隐式导入路径。

## 2026-09-27 acceptance environment failure
- 当前默认 `python3` 为 `/Library/Frameworks/Python.framework/Versions/3.9/bin/python3`，未安装 Pillow；本轮 acceptance runner 的 Worker 进程测试与 renderer baseline 失败，错误为 `ModuleNotFoundError: No module named 'PIL'`。此前 Python 3.14 环境的 93/93 报告仍是有效基线，本轮不覆盖或伪造通过结果。

## T4/T6 review
- T4 现有实现和 Worker 证据一致：IntersectionObserver 懒加载、并发队列、198×138 预览限制、LRU 128 缓存、canonical scope；300/300 响应耗时 6.03 秒。宿主首屏 1 秒交互仍没有可核实数据。
- T6 人工延期项仍合理；JOB-06 仅 Worker 264/264 已通过，宿主增量 UI 仍等待解锁后复跑。
- 修正文档中的 SET-06 过度表述：当前只有 macOS arm64 `.app` 启停/Worker 双跑和 onedir 资源证据；没有可核实的 DMG、签名、公证或 Windows 安装产物。

## T3 candidate2 findings
- candidate2 在固定 16 张素材、120 个输出上把前一候选剩余的 14 个 `_07` 和 14 个 `_08` 差异全部消除：`different_files=0`、`max_diff=0`、`MAE=0`、PSNR 为无穷。
- 改动仅在 `renderer.py`：不透明浅色背景前景框/背景延展（含 1125x1500 square 条件）和 RGBA 主图品类模板分支；相关 compare/analysis JSON 已持久化。
- 这不是正式基线替换：`v3-current-20260927-compare-report.json` 仍保留 52 个 needs_fix，candidate2 需人工视觉复核后才能决定是否更新正式基线。
