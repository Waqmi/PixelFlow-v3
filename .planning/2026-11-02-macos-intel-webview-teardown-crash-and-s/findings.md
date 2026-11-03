# Findings & Decisions

## Requirements
- Stop macOS Big Sur Intel crashes after confirming materials or saving settings; remove blue horizontal lines in the settings window.
- Preserve normal macOS ARM generation and settings flows; keep the fix in the shared main branch/source.
- Verify frontend protocol/build checks; actual Big Sur Intel behavior still needs a fresh test build.

## Research Findings
- Three supplied crash reports from different build batches (two duplicate reports on 2026-11-02, one on 2026-10-30) show the same native teardown stack: `objc_release` -> `WryWebView::remove_custom_task_key` -> `url_scheme_handler::stop_task` -> WebKit `stopAllTasksForPage` -> `WKWebView dealloc`.
- The newest reports are x86-64 native on macOS 11.7.11 and terminate with `EXC_BAD_ACCESS (SIGSEGV)`. Their executable path is `/Applications/PixelFlow-Catalina-Intel.app`; the user confirmed these are from other app build batches, but all report the same crash outcome.
- The provided app folder is labeled `PixelFlow-8f64c00`, matching the current source HEAD. The workspace's packaged Worker is arm64 while the provided Intel app's Worker is x86_64, so this Apple Silicon host cannot safely use the workspace Worker in an Intel test bundle.
- Before the fix, `closeOwnWindow()` hid for 200 ms and then called `destroy()`; settings, confirm, and report windows used it. Settings and confirm reopen paths could also call `close()` on an existing hidden/stale window.
- Current locked dependencies are Tauri 2.11.5 and Wry 0.55.1. The provided Intel binary is x86_64 and declares macOS 11.0 as its minimum.
- The prior uncommitted settings focus change focused the dialog container in the settings WebView. Project CSS documents that older WebKit renders this focus ring as full-width blue bars; the final code restores first-control focus and retains the dialog's Tab trap.
- The confirm WebView loads previews in a background queue. Since reuse prevents component unmount cleanup, pause timers/observers/queued reads on hide, and release preview blob URLs no longer referenced when a new snapshot is applied.
- Each auxiliary window has a native title bar; intercept its close request and hide it so the red close button cannot bypass the retained-window lifecycle. Do not intercept main-window close.
- User confirmed the blue bars disappear when the Intel settings window moves behind another app, supporting the older-WebKit active focus-ring diagnosis.
- The 2026-11-02 15:46 Intel crash report binary UUID is `02DC0C2E-D6B5-340A-9EFA-C37CA30BCE0F`; the downloaded `729f623` x86_64 executable UUID is `AA68EFAD-5BF4-3784-89E9-ACAE98B0C77B`. This report is not from that exact executable.
- Current settings CSS only gives a focus outline to individual controls and disables outline on the dialog container. The screenshot's full-width top/bottom lines do not match a current CSS rule; native WebKit/AppKit focus rendering or an older bundle remains plausible. Do not suppress focus styling globally without reproducing on the matching Big Sur build.
- Reopening settings was fragile: the main view emitted `settings-reopen` only when `isVisible()` returned false and did so before showing the child. A stale visibility result or missed asynchronous listener registration left the retained view blank.
- Windows desktop host had no `windows_subsystem = "windows"` crate attribute. Existing `hide_console()` calls only affect child processes, not the GUI host executable.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Keep auxiliary WebViews alive but hidden when dismissed, then show and refresh them on reuse | Avoids explicit WebView destruction in the user flows implicated by the crash; preserves app-level data and rendering state across reopen. |
| Focus the first settings control instead of the dialog container | Older WebKit renders a focused full-window dialog as full-width bars; focusing a control avoids the full-window ring while keeping keyboard navigation. |
| Pause confirmation previews while the window is hidden | Prevents retained-window preview loads from competing with classification/generation and leaking unused blob URLs. |
| On every reuse, show/focus the settings window and send its snapshot after a bounded readiness wait | Removes visibility-query dependence and closes the cross-WebView listener race. |
| Use the Windows GUI subsystem for release builds | Avoids an attached console without losing debug console output. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| The provided app path does not match crash report paths | User confirmed reports came from earlier build batches and share the same crash outcome; a test of a newly built Intel app remains necessary. |

## Resources
-
