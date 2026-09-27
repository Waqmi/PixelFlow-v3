# Progress Log

## Session: 2026-09-27

### Current Status
- **Phase:** T2 `.app` 完整回归
- **Started:** 2026-09-27
- **Plan ID:** `2026-09-27-pixelflow-v3-t2-t11-sequential-maintenan`

### Actions Taken
- 恢复 planning-with-files；未发现旧计划，已初始化新计划。
- 读取现有 `docs/STATUS.md`、T2 计划和既有证据位置。
- 确认当前代码和证据基线，准备从 T2 开始。

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| Planning state | selected plan exists | plan files created | PASS |
| Existing baseline | HEAD and evidence readable | HEAD 6489633, evidence present | PASS |

### Errors
| Error | Resolution |
|-------|------------|
| No planning directory found | Initialized a new task plan; no prior plan state was available |

### T2 discovery update
- Host UI opened successfully through CUA.
- Located 36-source fixture and existing prior output directory.
- Next: create fresh output root and drive classification/generation through the app.

### T2 live UI update
- First run passed classification, manual edit, generation, output-folder action, and report rendering.
- Second run displayed incremental reuse, but output count changed 264 -> 252; record as a T2 discrepancy.
- Next: run cancellation on a fresh output directory, then inspect process/output residue.

### T2 completion decision
- Wrote `baselines/v2.6/v3-recheck-20260927/desktop-app-ui-audit-manual-t2-20260927.json`.
- Live UI path passed through cancellation and clean app close; 97 partial outputs were preserved.
- T2 remains partial: second run reported 252 vs first 264 outputs, and the checkout has no 20-image shoe fixture for the requested Pose/Seg UI run.

### T3 complete
- Revalidated legacy/v3 reports and compare classification.
- 120/120 paired outputs, zero failures, 52 needs_fix.
- Compare and engine tests: 35 passed.

### T4 verification
- Rechecked preview audit, cache key, Host canonical scope validation, lazy queue, and CSP.
- T4 Worker/security/performance evidence is complete; desktop first-paint measurement remains pending.

### T5 discovery
- Rechecked restart budget, backoff, rolling logs, Windows hidden console, and task tracking.
- Found task ID loss on restart failure/exhaustion; delegated a minimal propagation fix before cargo verification.

### T5 complete/partial
- Added `host-supervisor-audit-20260927.json`.
- Task ID propagation fix completed; cargo check passed.
- Platform fault injection and dialog plugin remain pending.

### Continued run: T6/T7/T8/T11
- Re-resolved the selected planning directory and verified shared worktree state.
- `tools/run_acceptance.py --skip-desktop --legacy-cycle-record docs/legacy-cycle-beta.2.json` passed with Worker 89/89; latest source/onedir hashes recorded as 027a624b…/627ebbff….
- T8 result-page review/fallback controls and T11 cycle record are present; T11 remains active with 0 real batches and T3 needs_fix=52.
- T6 parity remains partial because full desktop UI evidence is incomplete; no status was upgraded based on Worker-only evidence.
- T7 remains partial: onedir artifact is present, but the checked-in build used Python 3.9.13; Python 3.12 rebuild is deferred to CI/available environment.

### T9 verification
- `npm run protocol:check` passed.
- `npm run build` passed after extracting `useTaskProgress` from `App.vue`.
- First combined command invoked `cargo check` from `apps/desktop` and failed because Cargo.toml is under `apps/desktop/src-tauri`; reran from the correct directory and it passed. This was a command-directory error, not a source failure.

### T6–T10 closeout
- T6 parity document now records JOB-03 as passed from the host-permission cancellation audit and keeps JOB-06 plus remaining UI-only items explicitly pending.
- T7 onedir hashes and runtime validation remain valid; no Python 3.12 binary or `worker/.venv` exists on this host, so rebuild is deferred without fabricating it.
- T8 result UI controls are present in `App.vue`; T11 cycle evidence remains active rather than being treated as complete.
- T9 extracted `useTaskProgress` into a composable. Protocol check, Vue build and Cargo check passed. Vitest remains deferred because no new npm dependency can be installed in this environment.
- T10 release gate is blocked by missing signing/notarization credentials and Windows runner; current unsigned arm64 app has only startup/Worker evidence.
- Final full acceptance report: `baselines/v2.6/acceptance-t2-t11-final-20260927/acceptance.json`.
- Attempted an ad-hoc onefile/onedir cold-start timing probe; the standalone subprocess did not produce a bounded result and the command timed out while reading stderr. Existing package evidence remains authoritative for startup/runtime checks; no timing claim was added.

### Continued maintenance after annotation review
- T5 added pure restart-budget helpers and three Rust unit tests covering the 60-second/three-attempt limit, expiry cleanup, shutdown suppression, and task ID preservation. `cargo test --bin pixelflow-desktop` passed 3/3; `cargo check` passed.
- T2 result summary now prefers `validation.passed` and includes `model_images` in the compatibility fallback count. This aligns the UI's success count with validated output files and addresses the observed 264→252 reporting mismatch without changing rendering.
- `npm run build`, `npm run protocol:check`, and focused Worker engine/process tests passed (37/37).
- Dialog plugin migration remains deferred: `tauri-plugin-dialog` is not available in the local Cargo cache and cannot be safely introduced offline.

### T3 needs_fix candidate audit
- Inspected all 52 persisted differences and compared metric behavior against the documented thresholds; classification is not a counting or tolerance artifact.
- Added the edge-safe light-background branch in `renderer.py` and reran the full 16-source candidate in `/private/tmp/pixelflow-t3-candidate` without changing the formal baseline.
- Candidate evidence: 120/120 paired outputs, 0 failures, 28 remaining `needs_fix`; 24 baseline differences became exact matches. The remaining 28 are recorded by cause in `t3-edge-path-candidate-analysis-20260927.json`.
- Worker tests after the candidate: 91/91 passed. Formal T3 status remains 52 until the candidate is reviewed and the remaining two composition paths are addressed.

### T2/T3 corrective maintenance
- Worker incremental reuse bug fixed: the VIP reuse branch no longer skips PNG transparent reuse/counting. Regression test covers 4 transparent outputs plus VIP reuse; Worker suite now passes 91/91.
- The historical T2 UI audit is preserved unchanged as an observation, with diagnosis and post-fix Worker reproduction appended; host-permission UI rerun remains pending.
- T3 edge-safe renderer candidate persisted without rewriting the formal baseline. Candidate report shows 120/120 paired, 0 failures, needs_fix 28 (down from 52), MAE 2.3226 and PSNR 21.8691. Remaining 28 are explicitly grouped and not reclassified as acceptable.
- A narrow source-filter experiment for the RGBA path produced contaminated naming/output scope and was reverted; it is not evidence and is not retained.

### Product maintenance suggestions
- 设置页补充 `3.0.0-beta.1`、Worker 版本/哈希前 8 位、`v3-composition-1` 和 Worker 状态；模型页显示 Pose/Seg 期望文件名、实际文件及 SHA-256 匹配状态，缺失时提示保守构图。
- 桌面 Host 增加无第三方依赖的标准 store ZIP 导出命令，条目校验、大小上限和路径防穿越均有 Rust 单测；导出内容包括诊断 JSON、失败报告、最近结果和滚动 Worker 日志。
- v3 输出策略固定为同名复用；`force_rerender=true` 才覆盖，分类预检检查输出磁盘并在低于 200 MB 时警告；补充了复用/覆盖/低磁盘回归测试。
- `tools/run_acceptance.py --full-evidence` 首次运行因 `Path.relative_to(ROOT)` 对相对输出路径抛错；已改为先 resolve 并安全记录仓库内/外路径，随后成功生成 full evidence 报告。
- 本轮最终验证：Worker 93/93；协议检查、Vue 构建、Cargo 检查通过；Rust 桌面单测 4/4；full evidence report 已持久化。全量 runner 生成的图片保持忽略，不纳入提交。

### T2 host-permission rerun (2026-09-27)
- 使用宿主权限启动已构建 `PixelFlow`，输入 `/private/tmp/pixelflow-v3-sources-36`（36 张）和全新输出目录 `/private/tmp/pixelflow-v3-ui-rerun-output-20260927`。
- 分类确认页显示 36 张，手动将首张从“细节图”改为“商品白底图”，统计从商品白底图 8/细节图 28 变为 9/27；确认并生成完成，输出目录实际包含 264 个文件。
- 生成过程中进度从 15/108 到 42/108，随后应用回到“准备就绪”，未观察到 Worker 残留。
- 第二次同目录复用尚未完成：UI 继续操作时宿主 Mac 自动锁定，CUA 返回“Mac is locked”；不把该次标记为通过，保留为当前阻塞。
- 解锁后再次运行第二次分类，进度到 36/36 后未进入确认页；点击“停止”后也长时间停留在“正在安全停止”。已关闭该卡住窗口，不把复用或取消标记为通过；该现象需要单独修复/复现。

### T4/T6/T9 ordered review
- T4 继续保持 partial：Worker 300/300 预览证据和安全边界通过，宿主 300 张首屏交互时间没有实测。
- T6 parity 文档已修正 SET-06，删除未被证据支持的 “.dmg 启停” 表述；CONF/JOB/SET 人工项继续保留延期状态。
- T9 维持最小拆分：关于页已抽出为 `components/SettingsAboutView.vue`，props/emits 保留版本和诊断操作；Vitest 仍因离线依赖条件延期。

### T3 candidate2 renderer review
- 在前一候选 28 个差异基础上，新增 `_07` 前景框/背景延展（含 square 尺寸）和 `_08` RGBA 品类模板分支。
- 固定 16 张素材重新生成 120 个输出，与 legacy 逐像素比较为 0 个差异（`max_diff=0`、`MAE=0`、PSNR=∞、`needs_fix=0`）。
- 正式 `v3-current-20260927` 基线仍为 52，candidate2 仅作为待人工视觉复核的候选证据；Worker 相关测试 47/47、全量 93/93 通过。

### Full evidence refresh after T3 candidate2
- 重新运行 `tools/run_acceptance.py --skip-desktop --full-evidence`，报告 `baselines/v2.6/acceptance-full-evidence-20260927-final/acceptance.json` 通过。
- Worker 93/93 和 renderer baseline 检查均通过；最新候选报告为 120/120 配对、0 个差异。生成图片目录按 `.gitignore` 保持不入 Git。

### T2 rebuild attempt
- 为避免使用 18:02 的旧 `.app`，尝试 `npm run tauri:build` 重建当前包；`beforeBuildCommand` 在 Python 3.9 阶段因缺少 PyInstaller 失败。
- 未将旧包的第二次分类卡住归因到当前源码，也未伪造新 `.app` 证据；该重建继续归入 T7 Python/独立环境阻塞。

### T2/T9 corrective maintenance
- `apps/desktop/src/App.vue` 增加确认窗口 stale label 恢复：已有窗口展示/聚焦失败时关闭并重建，避免分类完成后确认页不可见。
- 分类进度增加 120 秒无进展 watchdog；取消请求记录 request/job ID，处理 Worker `cancelling`/`idle`、错误和 15 秒超时，避免 UI 永久停留在“正在安全停止”。这只改善前端收敛，不替代宿主复跑证据。
- `.github/workflows/v3.yml` 的 Worker job 增加 `PYTHONPATH=worker`，修复从仓库根目录运行 unittest 时导入路径不稳定的问题。
- 验证：Vue `npm run build`、`npm run protocol:check`、`git diff --check`、Tauri `cargo check` 和 `cargo test --bin pixelflow-desktop`（4/4）通过。
- `tools/run_acceptance.py --skip-desktop --full-evidence` 在当前 Python 3.9 环境失败：缺少 Pillow，Worker 13 项进程测试和 renderer baseline 无法启动；该失败已保留为未满足环境证据，未覆盖此前 Python 3.14 的 93/93 报告。
- 使用已配置的 `/opt/homebrew/bin/python3.14` 重跑 Worker 全量测试：93/93 通过（26.4 秒）。本轮 acceptance runner 受 30 秒工具上限中断，未生成新的全量报告，继续引用此前已完成的 `acceptance-full-evidence-20260927-final/acceptance.json`。
- 窄验证确认：源码 Worker 与当前 onedir Worker 在同一 36 张输入、`workers=4`、同一输出目录下连续两次均约 1.1 秒返回 `done`（36 条 progress，最后 36/36）；旧 `.app` 卡住不能归因到分类算法。`test_worker_process` 5/5、Vue build、protocol check 均通过；新的宿主 UI 复用证据仍未伪造。
