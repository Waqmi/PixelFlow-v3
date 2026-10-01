# Task Plan: Update anchor composition and investigate doubled output counts

## Goal
Update the requested shoe/shirt anchor parameters and version to v3-composition-4, then identify and fix why the latest shirt-material output directory contains roughly twice as many results as expected, with focused and full verification.

## Next Step
Run focused and full Worker tests, then verify the corrected output-count behavior against the current input/output paths.

## Current Phase
Phase 1

## Phases

### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify constraints
- [x] Document in findings.md
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Define approach
- [x] Identify minimal files to change
- **Status:** complete

### Phase 3: Implementation
- [x] Update anchor constants and rule versions
- [x] Fix the output-count root cause at the shared generation path
- [x] Update focused tests/docs only where required
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Run focused anchor/output-count tests
- [x] Run the full Worker suite
- [x] Recount and inspect the specified output directory
- **Status:** complete

### Phase 5: Delivery
- [x] Review diff and residual risks
- [x] Deliver paths, test results, and root cause
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|

## Errors Encountered
| Error | Resolution |
|-------|------------|
| Full Worker suite has one missing fixture directory | Recorded as pre-existing; 132 other tests and all new focused tests pass. |
