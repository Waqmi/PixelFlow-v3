# Task Plan: Diagnostic Bundle Enrichment

## Goal
Make exported v3 diagnostics useful for reproducing task failures, progress regressions, and duplicate outputs while keeping the package bounded and privacy-aware.

## Next Step
No remaining implementation work; deliver the tested arm64 app artifact.

## Current Phase
Phase 3

## Phases

### Phase 1: Audit Existing Diagnostics
- [x] Trace worker stderr persistence and rotation
- [x] Trace current ZIP entries and result snapshot
- [x] Identify existing per-source diagnostics artifact
- **Status:** complete

### Phase 2: Implement Diagnostic Enhancements
- [x] Bundle per-source diagnostics JSON when available
- [x] Persist and export bounded Worker event records
- [x] Add bounded output inventory and duplicate hash groups
- [x] Redact absolute machine paths in exported data
- **Status:** complete

### Phase 3: Review & Verification
- [x] Review privacy, size bounds, and archive behavior
- [x] Run focused and full tests/build
- [x] Record test outcomes and deliver
- **Status:** complete

## Decisions
| Decision | Rationale |
|---|---|
| Preserve structured event types and selected progress fields | Enough to replay task phases without persisting arbitrary source paths or large payloads. |
| Inventory outputs by relative path, byte count, dimensions, and SHA-256; do not include image bytes | Detect duplicate files while keeping the archive small. |
| Treat missing diagnostics artifacts as an explanatory ZIP entry | Export must remain available if a per-source file was rotated, cleaned, or inaccessible. |

## Errors
| Error | Resolution |
|---|---|
| `cargo fmt -- --check` reports pre-existing formatting differences throughout `main.rs` | `cargo test` passes; no broad formatting rewrite was applied to avoid unrelated churn. |
