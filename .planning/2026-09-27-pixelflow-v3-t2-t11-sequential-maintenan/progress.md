# Progress Log

## Session: 2026-09-27

### Current Status
- **Phase:** T2 `.app` 完整回归
- **Started:** 2026-09-27
- **Plan ID:** `2026-09-27-pixelflow-v3-t2-t11-sequential-maintenan`

### Confirmation preview repair (2026-09-27)
- Reproduced the screenshot symptom from the child-window data flow: the confirmation snapshot carried materials but no input root, so every child-window preview request used an empty `allowed_roots` list and was rejected by the Worker scope guard.
- Patched `apps/desktop/src/App.vue` to carry `inputRoot` in `ConfirmSnapshot` and restore it before preview scheduling.
- Pending: Vue build, protocol check, focused Worker preview check, and review of the final diff.
- Verification complete: `npm run build`, `npm run protocol:check`, `cargo check`, `PYTHONPATH=worker /opt/homebrew/bin/python3.14 -m unittest worker.tests.test_contracts` (7/7), and a real JPG `read_preview` request all passed.
- First Worker test attempt omitted `PYTHONPATH=worker` and failed at import; rerun with the repository's documented environment passed. No source failure.
- Built app-only bundle from the repaired source using `worker/.venv` (PyInstaller 6.22.0, Python 3.14.5); default `npm run tauri:build` was blocked by system Python 3.9 lacking PyInstaller. App-only Tauri bundle succeeded, was ad-hoc signed and verified, and copied to `dist/PixelFlow-v3-beta-preview-fix.app`. Embedded Worker hash: `e6f28644e8c41eead737b16ab93bec5992172c301eafa8c8bec3e9ce101055c5`.

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
- 取消窄验证：分类开始后立即发送 `cancel_generation`，约 1.12 秒收到原分类 request 的 `cancelled`，随后 shutdown 正常退出且无 stderr；取消请求自身的 `cancelling` status 可能重复出现，前端按 request ID 幂等处理。

### Latest macOS .app packaging
- 在 `worker/.venv` 安装 PyInstaller 6.22.0、Pillow 12.3.0 和可用于 Python 3.14 的 onnxruntime 1.30.0；本机没有 Python 3.12。默认 `npm run tauri:build` 成功构建 Worker、Vue 和 `.app`，随后 DMG bundler 失败；app-only Tauri bundle 成功。
- 对 `.app` 做 ad-hoc 签名并通过 `codesign --verify --deep --strict`；将已验证包复制到 `dist/PixelFlow-v3-beta.app`。包内 Worker 哈希 `17f6ef32...`，ready/ping/shutdown、exit code 0、stderr empty 均通过。
- 使用宿主权限打开新 `.app`，主窗口显示“准备就绪”；通过菜单退出后，新 App 与其子 Worker 进程均消失。证据 `baselines/v2.6/v3-recheck-20260927/mac-app-package-latest-20260927.json`。
- 这是本地 arm64 测试包；T2 的 264→264 UI 复用、模型推理与 T10 Developer ID 签名/公证/DMG/Windows 均未因此变为通过。

### Model probe regression from latest .app
- 用户截图报告 Pose/Seg 均“不可用”，错误 `file_version() got an unexpected keyword argument 'include_digest'`。
- 包内 Worker `probe_models` 已独立复现同一错误；本地两个 ONNX 文件存在，SHA-256 均与 `models/README.md` 一致。
- 已将模型层最小修复和回归测试交给 `luna-worker`；主线程负责重新打包、冻结 Worker 现场检测和宿主 UI 验收。
- 根因修复提交 `3359911`：公开 `file_version` 接受并转发 `include_digest`；新增直接覆盖 Pose/Seg 探测和 SHA-256 的回归测试。Worker 全量 94/94 通过。
- 使用 `worker/.venv`/Python 3.14.5 重建 onedir Worker 和 app-only `.app`；`dist/PixelFlow-v3-beta-model-fix.app` 已通过 ad-hoc codesign 校验，Worker 哈希 `e6f28644...`。
- 重新打包后的冻结 Worker 对真实 Pose/Seg ONNX 均返回 `state=已就绪`、`fallback=false` 和正确 SHA-256；宿主设置页点击“测试模型”也显示“Pose 已就绪；Seg 已就绪”，两项均“SHA-256 匹配”。
- 旧 `dist/PixelFlow-v3-beta.app` 当时仍在运行，为避免覆盖用户正在使用的应用，修复包采用独立路径；旧包状态未变。证据 `baselines/v2.6/v3-recheck-20260927/model-probe-fix-20260927.json`。

### v3 feedback follow-up (2026-09-27)
- Added native/background bootstrap colors to the static Tauri window, inline `index.html`, and settings/report/confirm `WebviewWindow` options to cover the pre-CSS white flash.
- Added explicit `Pose/Seg` phase rendering and a confirmation-page model status summary. Local model remains opt-in; the UI now says when it is not enabled instead of appearing silent.
- Confirmed generation slowdown cause from source: v3 typed rendering is currently serial, while v2.6/legacy uses the existing auto render pool (up to 3 workers here). Deferred parallel renderer changes pending a comparable timing baseline; no claim of a speed fix was made.
- Verification: `npm run build` passed; `npm run protocol:check` passed; `cargo check` passed; focused Worker tests `33/33` passed with Python 3.14.5. An attempted module name `worker.tests.test_process` was invalid (actual file is `test_worker_process.py`) and was rerun successfully.
- Built `dist/PixelFlow-v3-beta-feedback-fix.app` with `worker/.venv` Python 3.14.5, ad-hoc codesign verification, and embedded Worker hash `e6f28644…`; packaged Worker ping/shutdown returned cleanly with empty stderr. The first build attempt incorrectly resolved `PIXELFLOW_PYTHON` relative to `apps/desktop`; reran with the absolute path.

### Settings flash correction (2026-09-28)
- Replaced immediate settings-window visibility with hidden-until-ready sequencing. Existing hidden labels are closed before recreation.
- `npm run build`, `npm run protocol:check`, `cargo check`, and `git diff --check` passed after the change.
- Rebuilt and ad-hoc-signed `dist/PixelFlow-v3-beta-settings-flash-fix.app`; embedded Worker hash `e6f28644…`. Native CUA inspection was limited by two already-running PixelFlow instances; source and bundle verification are complete, but no reliable frame-by-frame screenshot claim is recorded.
- Rebuilt once more after the stale-hidden-window guard: final artifact is `dist/PixelFlow-v3-beta-settings-flash-fix.app` (152 MB), ad-hoc codesign verified; embedded Worker ping/shutdown passed with empty stderr and hash `e6f28644…`.
- Added a 3-second reveal fallback for a lost `settings-window-ready` event, reran Vue build/protocol/Cargo checks, and rebuilt the same final app artifact.
- Final bundled Worker ping/shutdown returned `ready`/`done` with empty stderr; hash `e6f28644e8c41eead737b16ab93bec5992172c301eafa8c8bec3e9ce101055c5`.

### Settings-open regression repair (2026-09-28)
- User reported that the settings page no longer opened after the hidden-until-ready change.
- Removed the cross-window ready handshake, hidden initial state, 3-second reveal fallback, and opening guard from `apps/desktop/src/App.vue`.
- Restored immediate settings Webview creation while retaining stale hidden-window cleanup and background color.
- Verification passed: `npm run build`, `npm run protocol:check`, `cargo check`, `git diff --check`.
- Built and ad-hoc-signed `dist/PixelFlow-v3-beta-settings-open-fix.app`; embedded Worker ready/ping/shutdown passed with exit code 0 and empty stderr.
- `tauri build` reached the `.app` successfully but the optional DMG bundler failed in `bundle_dmg.sh`.

### Pose/Seg prompt repair (2026-09-28)
- Implemented the macOS direct model-service path so Pose/Seg reads stay inside the already-authorized Worker process; non-macOS behavior is unchanged.
- Rebuilt from `worker/.venv` with Python 3.14.5, copied and ad-hoc signed `dist/PixelFlow-v3-beta-no-pose-seg-tcc-prompt.app`.
- `codesign --verify --deep --strict` passed. Frozen Worker ping/probe/shutdown passed; real Pose/Seg models reported ready with `fallback=false` and matching hashes.
- Focused model-cache/classification tests passed 25/25; `git diff --check` passed.
- Final full Worker suite passed 95/95 (`unittest discover`).
- First bundled verification attempt used unavailable `timeout` and was rerun without it; no source issue. The optional DMG step still fails in `bundle_dmg.sh` after `.app` creation.

### Host-app TCC follow-up (2026-09-28)
- Replaced macOS `osascript choose folder/file` with an in-process native `NSOpenPanel` scheduled on the Tauri main thread.
- `cargo check` passed; `cargo test --bin pixelflow-desktop` passed 4/4; `npm run protocol:check` and `npm run build` passed.
- Rebuilt and ad-hoc-signed `dist/PixelFlow.app`; deep codesign verification and bundled Worker ready/shutdown passed.
- Static bundle check confirms the host no longer embeds the folder/file `osascript` commands. The save-file export path still uses `osascript` and is unrelated to material classification.
- Native TCC behavior cannot be proven headlessly; the user must use the new `PixelFlow.app` and select the input folder through its native picker. A new ad-hoc app identity may still prompt once.

### Report and VIP model-image repair (2026-09-28)
- Fixed report-window summary overflow by removing the fixed 82px height from the six-card grid; the detail panel now follows both rows.
- Fixed v3 VIP generation to include `model` and `model_detail` sources; added a regression test covering both model and VIP outputs.
- Vue build passed; focused VIP/override tests 2/2 and full engine tests 35/35 passed; `git diff --check` passed.
- Final full Worker suite after both fixes passed 96/96.

### Final app package verification (2026-09-28)
- Rebuilt `dist/PixelFlow.app` from the current v3 source and ad-hoc signed it; `codesign --verify --deep --strict` passed.
- Embedded Worker hash: `5b9d02d4f744b13dd6f51498cfa089200e58b9e506bde65c333f9d6ed66c53ab`.
- Packaged Worker ready/ping/shutdown exited 0 with empty stderr.
- Packaged Worker generation smoke test with one model source reported `model_images=1`, `vip_images=1`, and produced both `模特图` and `唯品专享1440` outputs.

### Model settings/crop repair (2026-09-28)
- 设置窗口 Worker ready 时自动重新探测已保存模型目录；仅在已启用且非检测中时显示不可用红框。
- 普通模特 Pose `by_size` 已接入 typed `CompositionPlan`，renderer 使用真实 Pose scale/position/protected bbox，修复模型已应用但裁剪仍走通用逻辑的问题。
- 验证：`unittest discover` 99/99；`npm run build`；`npm run protocol:check`；`cargo check`；`git diff --check`。
- 最终 `dist/PixelFlow.app` 已重建并 ad-hoc 签名，`codesign --verify --deep --strict` 通过；内置 Worker hash `91e80652c482cabc90c799a8b496fd1bdd752b3d8bed86da0aa39338dbc3c29f`，冻结 Worker ready/probe(Pose/Seg 已就绪)/shutdown 均退出 0。

### 2026-09-29 用户反馈：隐藏报告统计与 v2.6 模特裁剪校对
- [x] 删除结果报告中的“已接受复核”和“兼容回退”两张统计卡片，保留复核/回退详情。
- [x] `model_detail` 改回 v2.6 的 detail/cover 路径。
- [x] 方形 `model` 输出显式按 `crop_ratio` 执行 v2.6 direct-target-scale，并按 10% 保护边界裁剪。
- [x] VIP/透明请求按各自目标尺寸取 composition plan，避免复用最后一个 portrait plan。
- [x] 新增 renderer 的 crop ratio/model_detail 回归和 engine 的 VIP square-plan 回归。
- **验证：** `worker.tests.test_renderer` + `worker.tests.test_engine` 56/56 通过。

### 2026-09-29 验证与打包
- **Worker 全量测试：** 110/110 通过。
- **长袖888 回归：** 59 张素材；产品主图 222、模特图 132、唯品专享1440 59、透明产品图 32；总计 445；失败 0；尺寸/格式错误 0。
- **耗时：** 墙钟 38.401 秒；阶段累计（并发任务求和）分类 2.919s、解码 15.641s、分析 4.680s、构图 49.674s、叠加 4.184s、编码 30.654s、写盘 0.452s。写盘不是主要瓶颈。
- **前端/Tauri：** `npm run build`、`npm run protocol:check`、`cargo check` 通过；`git diff --check` 通过。
- **`.app`：** `/Users/weiheng/Desktop/PixelFlow/PixelFlow-v3.0.0-beta.1.app`，152 MB，arm64；`codesign --verify --deep --strict` 通过；内置 Worker ready/ping/shutdown 退出 0、stderr 为空；Worker SHA-256 `47db3d81df7a6161c89faf1d8dbebfccb09ff3cfbf404ad8b097f33fcc4e3732`。
- **限制：** 当前为 ad-hoc 未公证 macOS 本地包；DMG/Developer ID/Windows 未验证；v2.6 的 `person_bboxes` 安全镜像扩展仍未迁移到 typed CompositionPlan，未凭空扩大改动范围。

### 2026-09-29 产品白底图主体大小滑块修复
- [x] 修复 v3 忽略 `source_scale_adjustments` 的根因。
- [x] 将调整值接入白底商品图构图缩放；模型图不受该商品滑块影响。
- [x] 增加请求转发和实际前景尺寸变化回归测试。
- **验证：** renderer + engine 60/60 通过；全量 Worker 测试待最终代码冻结后重跑。

### 2026-09-29 产品滑块回归与最终打包
- **全量 Worker：** 112/112 通过。
- **端到端单素材回归：** `长袖888/FRWN009-1 男 苍鹭黑/IMG_5445.jpg`，0% 前景宽度 881px，+50% 前景宽度 1295px；两次均输出成功、尺寸正确、失败 0。
- **前端/Tauri：** Vue build、protocol check、cargo check、git diff --check 通过。
- **最终 `.app`：** `/Users/weiheng/Desktop/PixelFlow/PixelFlow-v3.0.0-beta.1.app`，152 MB，ad-hoc deep codesign 通过；Worker ready/ping/shutdown 退出 0，stderr 为空；Worker SHA-256 `0d956045da651f2098a0d6bb631c12e64065ed98a5b91da2f4c267ec6a532d56`。

### 2026-09-29 最终滑块修复包
- 全量 Worker 最终复跑：112/112 通过。
- 最终 `.app` 已在最后一处 VIP/透明请求透传修复后重建；`/Users/weiheng/Desktop/PixelFlow/PixelFlow-v3.0.0-beta.1.app`，152 MB，ad-hoc deep codesign 通过。
- 内置 Worker ready/ping/shutdown 通过，stderr 为空；最终 Worker SHA-256 `8ccec7e020629fe8798118a967e79e5bee03f65054a25c2d6b2c9018fa8c22cf`。
