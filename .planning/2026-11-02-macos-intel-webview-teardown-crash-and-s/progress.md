# Progress Log

## Session: 2026-11-02

### Current Status
- **Phase:** 4 - Testing & Verification
- **Started:** 2026-11-02

### Actions Taken
- Read three crash reports; confirmed matching Wry custom URL-scheme task cleanup during WKWebView deallocation.
- Traced auxiliary window close/reopen call sites in `apps/desktop/src/App.vue`; explicit `destroy()` remains after the existing 200 ms hide delay.
- Confirmed report app paths differ from the supplied Intel app; user says reports are from earlier batches with the same crash outcome.
- Delegated the scoped `App.vue` lifecycle and settings focus fix to the project worker for implementation and frontend checks.
- User tested the subsequent package and reported settings save/reopen failures across Intel, ARM and Windows, plus a Windows console window.
- Matched the latest Intel crash report UUID against the downloaded `729f623` x64 executable; the UUIDs differ, so the report does not verify a crash in that artifact.
- Added a `settings-ready` handshake; every reuse now shows/focuses the settings window and sends the latest snapshot after readiness, with a bounded timeout and visible status.
- Added a release-only Windows GUI subsystem attribute; debug builds keep their console.
- Re-reviewed the blue-line screenshot and CSS. No CSS rule draws full-width blue lines; the existing dialog focus outline is already disabled. Defer CSS changes pending Big Sur inspection of the exact current bundle.
- Committed the two code changes as `314e59f` and pushed `main`; the CI run is `41415505474`.
- Latest observed CI state: Desktop checks, macOS ARM build, Windows x64 build, and both Worker test jobs passed; macOS x86_64 build was still in progress. A later status query was rejected by the automatic approval reviewer, so no final Intel result or artifact download was confirmed.

### Additional Review Notes
- The screenshot's bars span the WKWebView content bounds. Current CSS does not explain them; native focus rendering or an older bundle remains plausible.
- Auxiliary title-bar close requests must hide the retained window, and confirmation preview work must pause while hidden.
- User confirmed the blue bars disappear when the Intel window moves behind another app.
- Source HEAD `8f64c00` matches the supplied Intel app folder label, but the workspace Worker is arm64, so no x64 bundle was produced on this host.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| Big Sur Intel repro using exact `729f623` x64 binary | No generation/settings crash or blue lines | Not verified; supplied report UUID belongs to a different executable | pending |
| Settings reopen handshake | Latest settings appear after save and reopen | Protocol check and frontend build pass; needs ARM/Windows app test | pending |
| Windows GUI subsystem | Release EXE opens without console | Attribute added; Windows PE verification requires CI | pending |
| Three-platform workflow for `314e59f` | All build jobs succeed | ARM and Windows succeeded; Intel job was in progress at last approved query | pending |
| `npm run protocol:check` | Pass | Passed | complete |
| `npm run build` | Pass | `vue-tsc --noEmit` and Vite production build passed | complete |
| `git diff --check` | No whitespace errors | Passed | complete |

### Errors
| Error | Resolution |
|-------|------------|
| First planning update did not match the template's table separator | Re-read the generated files and applied a corrected patch; no content had been changed by the failed attempt. |
| Several later planning patch attempts used mismatched text context | Re-read the files and applied the findings/progress updates in smaller, separate patches; failed patches made no changes. |
