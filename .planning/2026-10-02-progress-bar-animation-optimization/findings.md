# Findings & Decisions

## Requirements
- Preserve existing uncommitted changes.
- Inspect every `setProgress` caller, event cadence, interpolation parameters, and CSS before editing.
- Preserve true progress, completion semantics, and reduced-motion behavior.

## Initial Context
- Existing implementation uses RAF plus exponential interpolation in `apps/desktop/src/composables/useTaskProgress.ts`.
- User still observes sudden forward motion and insufficient smoothness.
- Implementation is delegated to Luna worker; final review and verification remain with root agent.

## Worker Findings
- `App.vue` has four `setProgress` calls: immediate reset on start/retry, non-immediate generation progress from `completed/total`, and immediate 1000 on done.
- Worker generation emits an initial reuse/preparation update and then one update per concurrent output completion; `as_completed` can batch target jumps in one frame.
- CSS has no width transition; the visible motion comes from the RAF helper.
- Luna worker changed only `apps/desktop/src/composables/useTaskProgress.ts`: cap RAF elapsed at 32ms and cap movement at 1.2 progress units/ms while retaining the 180ms exponential time constant, immediate completion, and reduced-motion behavior.
