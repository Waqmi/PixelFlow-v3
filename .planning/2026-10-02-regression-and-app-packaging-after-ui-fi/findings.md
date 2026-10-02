# Findings & Decisions

## Requirements
- Regression must pass before packaging.
- Build the app-only macOS bundle with the existing onedir Worker.

## Research Findings
- Tauri `beforeBuildCommand` builds the Worker and frontend before the app bundle.
- Existing project packaging target is `CARGO_TARGET_DIR=/private/tmp/pixelflow-cargo-target` with `--bundles app`.

## Verification Findings
- Regression gates passed: frontend build, protocol check, Worker 191 tests with 1 existing skip, Rust 4 tests, and diff check.
- Delivery app is ad-hoc signed and its Worker handshake passed.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
-
