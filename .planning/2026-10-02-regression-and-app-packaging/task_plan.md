# Task Plan: Regression and app packaging

## Goal
Run the project regression gates, then build and smoke-test the macOS `.app` if all gates pass.

## Next Step
Deliver the verified `.app` path and test results.

## Current Phase
Phase 1

## Phases

### Phase 1: Requirements & Discovery
- [x] Confirm build entry points and output target
- [x] Confirm user requested regression before packaging
- [x] Document constraints in findings.md
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Use existing `npm run build`, Worker unittest suite, protocol check, and Tauri app bundle path
- **Status:** complete

### Phase 3: Implementation
- [x] Run regression gates; no source changes planned
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Verify app package contents and Worker handshake
- [x] Regression results documented
- **Status:** complete

### Phase 5: Delivery
- [x] Review output path and smoke-test result
- [x] Deliver to user
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Build app-only bundle | Existing project docs record DMG bundling as environment-blocked; user requested `.app`. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| Initial smoke scripts assumed ping returns `done` | Worker contract returns `ready` for ping; validate that event and use `done` only for shutdown. |
