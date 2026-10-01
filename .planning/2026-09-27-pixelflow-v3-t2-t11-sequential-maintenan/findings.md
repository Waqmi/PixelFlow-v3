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

## Latest .app model probe failure
- 用户截图显示 Pose/Seg `file_version() got an unexpected keyword argument 'include_digest'`。直接调用最新 `dist/PixelFlow-v3-beta.app` 包内 Worker 的 `probe_models`，两个模型均返回相同错误；因此不是前端显示问题。
- `model_service.py` 在 probe 成功加载后调用 `file_version(Path(model_path), include_digest=True)`；`model_cache.py` 内部 `_file_version` 支持该参数，但公开包装 `file_version` 不接受，属于调用接口不一致。
- 两个 ONNX 文件都在当前 `models/` 目录，SHA-256 与 `models/README.md` 完全一致。修复后须用重新打包的 Worker 现场验证状态和校验哈希，不能只看单测。

## User report: confirmation-page previews unavailable (2026-09-27)
- Screenshot shows all visible image cards in the v3 material confirmation window displaying `预览不可用` across mixed `.jpg` and `.png` files; names and classifications render, indicating classification data reached the confirmation UI.
- Screenshot does not expose the Worker error detail, selected input root, app build identity, or whether preview requests reached the Worker. Root cause remains unconfirmed until request-path reproduction.
- Existing T4 evidence covers scoped Worker preview calls (300/300) but not this app confirmation-page flow; do not treat it as disproving the report.
- Root cause reproduced from the confirmation-window data flow: `ConfirmSnapshot` omitted `inputRoot`. The child confirmation window therefore kept both `inputRoot` and `classifiedInputRoot` empty, sent `allowed_roots: []` for every `read_preview`, and the Worker rejected each request with `preview scope is required`.
- Minimal fix: include `inputRoot` in the snapshot and restore both root refs before scheduling previews. Source Worker `read_preview` succeeds for the same real JPG when given the restored root.

## T4/T6 review
- T4 现有实现和 Worker 证据一致：IntersectionObserver 懒加载、并发队列、198×138 预览限制、LRU 128 缓存、canonical scope；300/300 响应耗时 6.03 秒。宿主首屏 1 秒交互仍没有可核实数据。
- T6 人工延期项仍合理；JOB-06 仅 Worker 264/264 已通过，宿主增量 UI 仍等待解锁后复跑。
- 修正文档中的 SET-06 过度表述：当前只有 macOS arm64 `.app` 启停/Worker 双跑和 onedir 资源证据；没有可核实的 DMG、签名、公证或 Windows 安装产物。

## T3 candidate2 findings
- candidate2 在固定 16 张素材、120 个输出上把前一候选剩余的 14 个 `_07` 和 14 个 `_08` 差异全部消除：`different_files=0`、`max_diff=0`、`MAE=0`、PSNR 为无穷。
- 改动仅在 `renderer.py`：不透明浅色背景前景框/背景延展（含 1125x1500 square 条件）和 RGBA 主图品类模板分支；相关 compare/analysis JSON 已持久化。
- 这不是正式基线替换：`v3-current-20260927-compare-report.json` 仍保留 52 个 needs_fix，candidate2 需人工视觉复核后才能决定是否更新正式基线。

## v3 feedback follow-up (2026-09-27)
- Window white flash is a native pre-paint gap: CSS already colors `html/body/#app`, but the native window default is painted before WebView CSS. Added an inline bootstrap background in `apps/desktop/index.html`, static Tauri `backgroundColor`, and matching runtime `backgroundColor` for settings/report/confirm windows.
- Pose/Seg progress already exists in the Worker. The UI handled exact `Pose` and `Seg` phases, but v3 native classification can emit `Pose/Seg`; the main status now renders that phase explicitly. Confirmation summary now states `未启用`, `完成`, `回退`, or `未运行` based on material evidence instead of hiding model state.
- v3 generation performance has a structural difference: `ImageEngine` routes App requests with `mode=v3` to `V3EngineAdapter._render_sources`, whose source loop is serial; legacy/v2.6 uses `ThreadPoolExecutor(max_workers=_auto_worker_count(...))`, capped at 3 on this host. No concurrency change was made without a fixed-material timing baseline because it could change parity and shared-renderer behavior.
- Confirmation preview load remains frontend-concurrency 2 (conservative) or 4 (auto/performance), Worker preview executor 4; a single preview is capped at 198x138 with a 15s timeout and bounded cache.
- Feedback fix app artifact: `dist/PixelFlow-v3-beta-feedback-fix.app`, arm64 local ad-hoc signed bundle; Worker hash remains `e6f28644…` because the Worker source was unchanged. The UI bundle includes the first-paint and Pose/Seg status changes.

## Settings-window flash follow-up (2026-09-28)
- `backgroundColor` alone was insufficient on macOS because Tauri documents the WebView background layer as not implemented there; the white frame is visible while the child WebView is still loading.
- Settings creation now uses `visible: false`. The settings child emits `settings-window-ready` after its first Vue tick and a short browser settling delay; the main window then calls `show()` and `setFocus()`. A stale hidden settings label is closed and recreated instead of being shown before it is ready.
- A 3-second reveal fallback keeps the settings window usable if the cross-window ready event is lost; the normal event path still reveals immediately after the child mount settles.

## Settings-open regression repair (2026-09-28)
- The hidden settings Webview depended on a child-to-main `settings-window-ready` event; when that event was lost, the window remained invisible.
- Removed the handshake, hidden initial state, opening guard, and fallback timer. Restored immediate settings Webview creation while retaining stale hidden-window cleanup and native background color.
- `npm run build`, `npm run protocol:check`, `cargo check`, and `git diff --check` passed.
- App-only artifact: `dist/PixelFlow-v3-beta-settings-open-fix.app`; ad-hoc signature verified. Embedded Worker hash: `e6f28644e8c41eead737b16ab93bec5992172c301eafa8c8bec3e9ce101055c5`; ready/ping/shutdown exited 0 with empty stderr.
- Tauri DMG bundling failed in `bundle_dmg.sh` after the `.app` was built; this does not invalidate the `.app` artifact.

## Pose/Seg macOS TCC prompt repair (2026-09-28)
- Root cause: v3 used `multiprocessing.get_context("spawn")` for `PixelFlowModel`; macOS TCC treated the frozen child as a second file-access identity when it read Desktop materials/models. This is why v2.6 did not show the prompt while v3 did.
- Minimal fix in `worker/pixelflow_core/model_service.py`: on Darwin, `ping`, `warmup`, `compose`, `segment`, and `probe` use cached assistants in the existing Worker process. Linux/Windows retain the original isolated spawn process.
- Direct mode restart/close paths clear the assistant cache and do not create a model subprocess.
- Regression test asserts Pose/Seg probe readiness and `_process is None`; focused model-cache/classification tests passed 25/25.
- Final frozen Worker probe against the real ONNX files returned Pose and Seg `state=已就绪`, `fallback=false`, with SHA-256 values; ready/probe/shutdown exited 0 and stderr contained only an ONNX Runtime telemetry warning.
- Final artifact: `dist/PixelFlow-v3-beta-no-pose-seg-tcc-prompt.app`, ad-hoc signature verified; embedded Worker SHA-256 `90c69e079846bdf98489bac49fe6d7aa5ae9be258ac071574b5e8e0125c534c1`.

## Follow-up: screenshot confirms host-app TCC prompt (2026-09-28)
- The new screenshot names the entire `PixelFlow-v3-beta-no-pose-seg-tcc-prompt.app`, not a `PixelFlowModel` child. The prior direct model-service fix therefore addressed only the nested identity, not the host's initial Desktop access.
- v3 currently uses `/usr/bin/osascript` for macOS folder/file selection and passes only a string path to the Worker. `remember_preview_root` is a preview allowlist, not a macOS security-scoped authorization.
- v2.6 uses Qt `QFileDialog` in the same application process. Its native panel is the missing authorization link.
- Additional release caveat: every ad-hoc re-signed/copied `.app` has a new code identity, so macOS can legitimately ask again for that new artifact even with the same bundle identifier.
- Planned minimal fix: use an in-process native macOS `NSOpenPanel` for directory/file selection, invoked on the Tauri main thread; keep Windows/other platforms unchanged.

## Host picker repair verification (2026-09-28)
- `apps/desktop/src-tauri/src/main.rs` now invokes `NSOpenPanel` through `AppHandle::run_on_main_thread` for macOS folder and Logo-file selection. The returned path is still passed through the existing preview-root and Logo validation logic.
- Added cached macOS-only `objc2`, `objc2-app-kit`, and `objc2-foundation` dependencies; Cargo lock changed only by the direct package dependency entries.
- `cargo check` passed; `cargo test --bin pixelflow-desktop` passed 4/4; Vue build and protocol check passed.
- Rebuilt `dist/PixelFlow.app`, ad-hoc signed and deep-verified. The final host binary contains no `choose folder`/`choose file` strings; only the unrelated save-file `osascript` path remains.
- Frozen Worker ready/shutdown passed with exit 0. No native UI permission result was fabricated; final confirmation still requires opening the new app and selecting the folder on the user's Mac.

## User report: result page overlap and VIP model images (2026-09-28)
- Screenshot shows six report summary cards rendered in a four-column grid, but `.report-window-shell .report-summary` is fixed at `height: 82px`; the second row therefore overflows into the following `.report-info` panel.
- Minimal UI fix is to let the summary grid use intrinsic height (with the existing four-column layout and small-screen media rule), so the detail panel starts after both card rows.
- V3 `V3EngineAdapter._render_sources` currently guards VIP generation with `source_type not in {"model", "model_detail"}`. This explicitly skips VIP output for model/model-detail inputs, while legacy VIP generation scans all no-logo source outputs. That is the direct cause of missing model images under `唯品专享1440`.
- The VIP request builder and renderer already accept `source_type="model"`/`"model_detail"`; the narrow fix is to remove only the exclusion guard and add a regression test.

## Report/VIP repair verification (2026-09-28)
- Report CSS now uses `height: auto` with the existing minimum height for the summary grid; the second row of cards pushes the info panel down instead of overlapping it.
- V3 VIP generation now includes all source types. A focused regression creates both `模特图/1440x1440/无Logo` and `唯品专享1440/无Logo` outputs for a model source.
- Focused VIP/override tests passed 2/2; full `worker.tests.test_engine` passed 35/35; Vue build passed.

## Packaged output smoke test (2026-09-28)
- The final embedded Worker was run with one forced `model` source, v3 mode, one selected size, and VIP enabled. It returned `model_images=1`, `vip_images=1`, exit 0, empty stderr, and created one output under each of `模特图` and `唯品专享1440`.

## User report: model settings persistence and crop (2026-09-28)
- Screenshot shows the model test page after reopening settings: the external model path is present, but both Pose/Seg rows say `未检测`, status says rules-only/Seg unavailable, and the warning says conservative composition. This is a state restoration/re-probe defect, not proof that the ONNX files are missing.
- The same test session reportedly passed before Save, but the generated model crop is wrong. The investigation must distinguish a UI-only diagnostic reset from the Worker request actually carrying `local_model_enabled`, `local_model_path`, and model-derived composition plans.

## Model settings/crop repair (2026-09-28)
- 设置页保存的是模型开关/目录，Pose/Seg 诊断只在窗口运行时内存中；重开设置页先显示空诊断并触发红框。Worker ready 后自动重新 probe，未启用或检测中不再误报红框。
- 普通模特图的 Pose 返回 `by_size` 构图计划，但原 typed-plan 转换只处理鞋类组合，导致 renderer 的 `plan=None`，普通模特走通用裁剪。现在 `by_size` 会转换为 `CompositionPlan`，保留缩放、焦点、保护框和裁剪比例。
- renderer 原先先 resize 再 cover 会抵消 plan scale；现在 model 分支把 scale 作为 `_resize_cover` 的 `extra_scale`，保护框保持源图坐标；保护框过大时不再把 cover 降级为空边填充。
- 验证：Worker 全量 99/99；Vue build、protocol check、Cargo check、git diff --check 均通过。

## v2.6 model-logic audit (2026-09-29)
- `model_assistant.py` 的品类 Pose 裁剪公式与 v2.6 对应实现一致；差异集中在 renderer/engine 的计划使用。
- v3 原先将 `model_detail` 与完整 `model` 一起走 Pose/模特裁剪；v2.6 将 `model_detail` 归入 detail，直接 cover。
- v3 方图 renderer 没有显式应用 v2.6 的 `target_crop_height_ratio` 直接缩放规则；虽然通常能被 `scale_adjustment_percent` 间接逼近，但外部/回退计划只有 crop ratio 时会偏离，且 v2.6 方图会重置额外 scale。
- v3 VIP 和透明请求原先复用 selected sizes 循环的 `last_plan`；v2.6 VIP 基于 1440x1440 无 Logo 构图，现改为按实际目标尺寸取 plan。
- v2.6 的安全镜像扩展依赖 `person_bboxes`，CompositionPlan 当前没有该字段；本轮保留 v3 的显式扩展门槛，避免无证据扩展造成误裁，并记录为剩余差异。

## Regression and package evidence (2026-09-29)
- `长袖888` output tree matched the expected 222/132/59/32 distribution with 445 readable files; all JPG outputs are RGB at directory dimensions and all transparent outputs are RGBA PNG at directory dimensions.
- The observed 38.401s wall-clock run used three source tasks in parallel; summed stage timings exceed wall time by design. Write time was 0.452s, so changing JPEG/PNG quality is not justified by this run.
- The packaged Worker build hash is `47db3d81df7a6161c89faf1d8dbebfccb09ff3cfbf404ad8b097f33fcc4e3732`; protocol lifecycle returned clean `ready`, `ready`, `done` events.
- Follow-up audit found two edge cases and both were fixed: direct square model scaling now caps protected regions at 80% of the canvas like v2.6; VIP generation now requires `1440x1440` to be selected, matching v2.6.

## Product scale slider regression (2026-09-29)
- UI `generationPayload()` already sends `source_scale_adjustments`; Worker parsing already stores it in `GenerationRequest`.
- v3 `_render_sources` previously never read that mapping, so the confirmation slider changed only UI state and had no rendering effect. The legacy/v2.6 adapter did consume the same mapping.
- Fix: resolve the adjustment by absolute path/POSIX path/name, attach it to each v3 main-product `RenderRequest`, and apply it in the opaque foreground scale, category template scale, transparent template scale, and generic cover fallback. Model sources remain unaffected by the product-only slider.
- Regression tests now prove request forwarding and a +50% adjustment produces a wider foreground in the rendered image.
- End-to-end evidence confirms the fix is not only a request-field change: the same product rendered at 0% had an 881px detected foreground width and at +50% had 1295px, with both outputs passing typed validation.
- Final package also forwards the product adjustment metadata to VIP and transparent requests, keeping all outputs for the same source consistent with the confirmation slider.
