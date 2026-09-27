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
