# Findings & Decisions

## Requirements
- Audit Claude Code's four `Image.getdata()` replacements, `App.vue` and animation CSS changes, and both animation documents.
- Run `npm run build`, all Worker tests, and the existing knee-pads regression without overwriting prior reports; use an independent current-run log directory.
- Repackage `dist/PixelFlow-v3-beta.app` only after root review direction. No commit/push.
- Keep `legacy_engine` for at least 14 calendar days or until two real batches, whichever condition is met first; preserve both deleted detection sidecars.

## Research Findings
- Handoff router says the working-tree base is `d388bbdb388d9278d2231d9e1a19faf2d4bf5903`, Pillow in the project venv is 12.3.0, four `getdata()` calls were changed, and the knee-pads baseline previously recorded 5 inputs, 27 output checks passing, 0 failures, and 4 sources requiring review.
- Router's previous regression results predate the latest Pillow and animation edits. Re-run requested checks before any package rebuild.
- Current Git state was read from the actual repository: HEAD `d388bbdb388d9278d2231d9e1a19faf2d4bf5903`; both `models/detection/unused` sidecars remain deleted. The existing worktree includes unrelated model-discovery, packaging, documentation, and host changes; do not revert them.
- `git diff --stat` before this plan's edits: 19 tracked files changed, 504 insertions, 84 deletions; plus new docs, scripts, a knee-pads baseline, and `AGENTS.md`. The initial detailed status showed two preserved deletions.
- Initial source diff confirms four `get_flattened_data()` replacements: two in `worker/pixelflow_core/renderer.py`, one in `v3_classification.py`, one in `legacy_engine/category_engine/source_type_classifier.py`. All remain wrapped in `list(...)` and consume per-pixel tuples after RGB conversion/resizing.
- The animation summary claims all animations can be disabled, GPU-only transforms/opacity, and tested 300-card performance; its checklist remains unchecked. The guide is a proposed implementation, not evidence that its recommended details are present.
- The guide's proposed `data-visible="material.isVisible !== false"` starts visible when `isVisible` is unset. Current CSS also has equally specific later `[data-visible="true"]` animation overriding `nth-child(n+21) { animation: none; }`. This likely defeats the 20-item limit and makes many cards animate immediately; verify actual lifecycle and selector order before reporting.
- Current diff has no Vue `data-complete` binding for the CSS completion pulse. `--confirm-grid-bg`, reduced-motion class coverage, and transition target wiring still require source verification.
- `fast_context_search` was rejected by automatic review for external export risk; all follow-up code search will remain local.
- The locked Pillow dependency is `Pillow==12.3.0`; the project venv is 12.3.0, `Image.Image.get_flattened_data` exists, and its docstring specifies an ordered tuple of pixel values flattened by scanline. The four consumers continue to use `list(...)` and per-pixel tuples.
- Existing CSS supports both the manual setting and system preference: `.reduce-motion *` and `@media(prefers-reduced-motion:reduce)` at `apps/desktop/src/styles.css:324-326`. The system rule disables animations/transitions but does not clear the new active-state transforms, so the narrow addition should set those active transforms to `none` in that media query.
- Existing settings/confirmation/report modals are wrapped by Vue `Transition` names in the main `App.vue` template, but their independent Tauri windows use separate shell state and do not use these transitions. The new CSS applies transform to the transition root/backdrop, while the pre-existing rules animate the inner dialog; remove the new broad transition/backdrop block.
- `--confirm-grid-bg` is already declared for light and dark themes (`styles.css:1,17`). The `::after` pseudo-element on `.confirm-grid` is a generated grid item, has no full-row span, and may add an extra cell/row; remove it and its associated scroll-state machinery per root review.
- Current first build before edits fails at `App.vue:1436` and `:1438` with TS2304 `ComponentPublicInstance` not found, caused by the new observer helper's type references. Removing that helper is within root-approved scope and should clear the compile error.
- Initial requested regressions completed before edits: Worker suite 192 tests, 191 passed/1 skipped; real knee-pads runner passed 5 inputs. Its default report path would overwrite the existing baseline, so rerun used the plan's independent report path.
- The knee-pads rerun report is stored at `.planning/2026-10-03-pixelflow-v3-optimization-closeout-2026-/logs/knee-pads-real-rerun.json`; console log is `.planning/2026-10-03-pixelflow-v3-optimization-closeout-2026-/logs/knee-pads-real-run.log`.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Prefer the existing preview observer/entry animation mechanism if it can satisfy the card-list need | Root reviewer favors reducing duplicated observer state; verify before recommending implementation. |
| Report only evidence-backed animation behavior; do not implement the guide's 1030 progress overshoot | The guide is advisory and overstates current behavior; overshoot is not requested for implementation. |
| Use the pinned Pillow 12.3.0 API directly without a compatibility layer | Both requirements lock and runtime establish 12.3.0; `get_flattened_data()` is present with ordered pixel tuples. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Fast-context MCP denied local-context export to external AI | Use `rg`, `git`, and direct local reads only. |
| Initial skill path typo | Corrected; read `ponytail` and `planning-with-files` instructions. |
| Initial parent `git status` ran from outer directory rather than repo | Rechecked in actual repository root. |

## Resources
- `/Users/weiheng/Desktop/PixelFlow/.session-archives/2026-10-03/2026-10-03_路由器_Codex_PixelFlow.md`
- `/Users/weiheng/Desktop/PixelFlow/.session-archives/2026-10-03/2026-10-03_对话全记录_Codex_PixelFlow.md`
- `docs/animation-optimization-manual-guide-2024-10-03.md`
- `docs/animation-optimization-summary-2024-10-03.md`

## Final Closeout
- Root accepted the animation review. Final build/tests passed; see `.planning/2026-10-03-pixelflow-v3-optimization-closeout-2026-/logs/acceptance-report.json` for totals and package evidence.
- `dist/PixelFlow-v3-beta.app` was rebuilt, strictly verified, and smoke-tested. The package wrapper initially ignored `--app-only` and reached a failing `hdiutil`; the narrow flag fix was validated on the existing Tauri bundle. No DMG was produced.
- No commit/push; HEAD is unchanged; both unused detection sidecars remain deleted. The `legacy_engine` observation period remains active. Whether the initial wrapper call removed a prior DMG is unknown because no backup or prior-existence evidence was found.

## Renewed app repackage (2026-10-03)
- User requested a fresh full `.app` rebuild after the earlier no-rebuild fast-path validation. Starting HEAD remains `d388bbdb388d9278d2231d9e1a19faf2d4bf5903`; both `models/detection/unused` sidecars are still deleted.
- The checked `docs/legacy-cycle-beta.2.md` still marks the cycle in progress with 0 real batches and requires 14 natural days or 2 real batches before evaluating `legacy_engine` removal.
- The accepted `tools/package_macos.sh --app-only` path copies and signs the rebuilt Tauri `.app`, then exits before DMG staging, DMG cleanup, or DMG creation. This invocation explicitly leaves the full Tauri build enabled.
- Full Worker/PyInstaller (Python 3.14.5, arm64), frontend (34 modules), and Tauri Rust/app builds passed with the full command `env -u PIXELFLOW_BUILT_APP PIXELFLOW_SKIP_TAURI_BUILD=0 bash tools/package_macos.sh --app-only`; the fresh app is at `dist/PixelFlow-v3-beta.app`.
- Fresh app verification passed: version `3.0.0-beta.1`, app/Worker arm64, `codesign --verify --deep --strict`; bundled Worker startup/ping/shutdown returned ready/ready/done and exited 0. Logs use the `repackage-20261003` suffix in this plan's `logs/` directory.
- Post-build HEAD and tracked source diff are unchanged from the pre-build baseline; both unused detection sidecars remain deleted; no DMG exists in `dist/`; the legacy observation remains in progress. The full Worker unit test suite was not repeated for this packaging-only request; its prior accepted result is recorded in `logs/acceptance-report.json`.
