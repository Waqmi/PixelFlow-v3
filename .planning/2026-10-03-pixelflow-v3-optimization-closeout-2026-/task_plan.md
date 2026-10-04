# Task Plan: PixelFlow v3 optimization closeout (2026-10-03)

## Goal
Review Claude Code's four Pillow API substitutions and animation CSS/App.vue/docs changes, run the requested frontend build, full Worker suite, and knee-pads regression, then wait for root review before repackaging the existing app.

## Next Step
Repackage complete; deliver the fresh verified app and repackage logs.

## Current Phase
Phase 6 - Requested full app repackage (complete)

## Phases

### Phase 1: Requirements and state recovery
- [x] Read the handoff router and full transcript
- [x] Read both animation documents and repository instructions
- [x] Record HEAD, working-tree state, and existing diff
- [x] Resolve a dedicated plan for this continuation
- **Status:** complete

### Phase 2: Audit and verification planning
- [x] Verify Pillow API semantics and dependency minimum
- [x] Verify animation selectors, Vue bindings/lifecycle, and document claims
- [x] Identify exact build, Worker, and knee-pads commands/report destinations
- [x] Send root concrete findings and minimal recommendations
- **Status:** complete

### Phase 3: Regression checks
- [x] Frontend build
- [x] Full Worker test suite
- [x] Knee-pads real-material regression
- **Status:** complete

### Phase 4: Package gate
- [x] Wait for root review direction
- [x] Run the authorized package command and capture its log
- [x] Fix and validate the `--app-only` fast path without rebuilding
- [x] Verify app version/architecture, strict signature, Worker smoke, and Pillow payload
- **Status:** complete; initial script invocation reached `hdiutil` because it ignored `--app-only`; no DMG was produced

### Phase 5: Delivery
- [x] Record acceptance evidence, test totals, limitations, and preserved constraints
- **Status:** complete

### Phase 6: Requested full app repackage
- [x] Recheck repository instructions, HEAD/status, deleted detection sidecars, and legacy observation requirement
- [x] Run `tools/package_macos.sh --app-only` with the full Tauri build enabled and capture a new log
- [x] Verify the replaced dist app's version/architecture and strict deep signature
- [x] Run the bundled Worker ready/ping/shutdown smoke and capture a new log
- [x] Record this run's source state, commands, outcomes, and limitations
- **Status:** complete; full rebuild and all requested package checks passed; no DMG generated

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Use a new named plan for this turn | Existing animation and app packaging plans were complete; the active plan belongs to unrelated UI report work. |
| Do not edit code or package before root review | Root owns review/strategy and explicitly said packaging waits for review direction. |
| Keep `legacy_engine` observation active for 14 calendar days or 2 real batches | User's handoff requirement. |
| Preserve deletion of both `models/detection/unused/*.classes.json` files | User's explicit constraint. |
| Do not commit or push | User's explicit constraint. |
| The renewed app request requires a full Tauri rebuild | Do not set `PIXELFLOW_SKIP_TAURI_BUILD=1`; only the app bundle is requested, so `--app-only` must bypass DMG staging/creation. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| `fast_context_search` was rejected by auto-review because it would send local repository context to an untrusted external AI service | Did not retry or bypass; use local `rg`, `git`, and file reads. |
| First Ponytail skill read used an incorrectly split path (`pony tail`) | Corrected to `/Users/weiheng/.codex/skills/ponytail/SKILL.md`. |
| Parent reported initial `git status` was run from the outer PixelFlow directory, which is not the repository | Re-ran all Git state checks from `PixelFlow-App-v3-beta`; record the original error for continuity. |
