# Task Plan: Cross-platform Settings Window Reopen and Intel Crash Follow-up

## Goal
Fix settings-window reopen failures on macOS ARM and Windows, suppress the Windows console host, and verify whether the Intel crash/blue lines reproduce on the exact current build without regressing ARM behavior.

## Next Step
Build and test a fresh x64 package on macOS 11 Intel; compare its executable UUID with any new crash report and inspect focus state if blue lines remain.

## Current Phase
Phase 4

## Phases

### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify constraints
- [x] Document in findings.md
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Define approach
- [x] Create project structure
- **Status:** complete

### Phase 3: Implementation
- [x] Execute the plan
- [x] Write to files before executing
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Verify source/build requirements
- [x] Document local test results
- [ ] Verify on Big Sur Intel using the exact current x64 bundle
- [ ] Verify settings reopen on ARM and Windows
- [ ] Verify Windows release EXE uses GUI subsystem
- **Status:** in_progress

### Phase 5: Delivery
- [ ] Review outputs
- [ ] Deliver to user
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Reuse hidden auxiliary windows instead of destroying them after every close | Earlier source explicitly destroyed WebViews; current crash report UUID does not match the tested 729f623 x64 artifact, so the crash remains unverified against current source. |
| Do not alter generation or Worker code | The crash is in native webview lifecycle, and ARM behavior must remain stable. |
| Add a settings-ready handshake and refresh on every window reuse | Prevents a missed cross-window event from leaving the retained settings view blank after save. |
| Mark release builds as Windows GUI applications | Prevents the main executable from attaching a console window. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| First planning update did not match the template's table separator | Re-read the generated files and applied a corrected patch; no content had been changed by the failed attempt. |
| Crash report binary UUID differs from the 729f623 Intel bundle | The supplied report cannot verify a crash in that exact artifact; test the matching x64 package on Big Sur. |
