# Task Plan: PixelFlow v3 hardening (1000 batch, recovery, diagnostics, accessibility)

## Goal
Implement and verify four approved hardening areas: stress test with `/Users/weiheng/Desktop/测试/1000张上衣`, resumable interrupted jobs, one-click diagnostics export, and keyboard/accessibility behavior.

## Acceptance criteria
- 1000-material run is measured without modifying source files; record duration, peak memory, preview/worker behavior, cancellation outcome, and failures.
- Interrupted generation persists enough state to resume safely from unfinished work.
- Diagnostics export contains app/worker/rules/model/build information and recent task state without exposing image bytes.
- Main dialogs and actionable controls support keyboard navigation, Enter/Esc behavior, visible focus, and reduced-motion behavior.
- Existing legacy_engine observation and deleted detection sidecars remain intact.

## Phases
- [x] 1. Inspect existing flows and record findings
- [x] 2. Implement resumable state and diagnostics export
- [x] 3. Implement keyboard/accessibility fixes
- [x] 4. Run 1000-material stress verification and targeted regression
- [x] 5. Review diff, package if requested, and record results

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|

## Decisions
| Decision | Rationale |
|---|---|
| Do not alter the 1000 image directory | It is user test input; verification must be read-only. |
| Keep legacy_engine and deleted sidecars unchanged | Existing project constraint. |

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|
| `npm run protocol:check` from repository root found no package.json | 1 | Re-ran from `apps/desktop`, passed. |
| Luna implementation worker quota exceeded | 1 | Primary agent continued with the same scoped design. |
