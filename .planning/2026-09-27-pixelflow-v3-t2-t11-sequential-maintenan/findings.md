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
