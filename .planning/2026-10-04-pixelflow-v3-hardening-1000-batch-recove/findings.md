# Findings

- New task started 2026-10-04 after prior v3 closeout commit `1afb566`.
- Test input directory: `/Users/weiheng/Desktop/测试/1000张上衣`.
- Existing active plan from prior closeout is complete; this task has its own plan directory.

## Existing implementation audit
- `App.vue` already has model/build diagnostics and Tauri `export_diagnostics`; it currently exports app/worker/rules/model data, failures, last-result, and a worker log through Rust ZIP creation.
- `App.vue` already handles Escape globally, category picker arrows/Home/End, visible `:focus-visible` CSS, reduced-motion classes, and report retry actions.
- Worker restart supervision emits `worker-exit` with task id and retryable state, but UI only reports interruption; there is no persisted resume payload or dedicated resume action.
- V3 engine already reuses existing output paths when `force_rerender` is false and reports `incremental_reused`; this is the safe basis for resume after cancellation or Worker restart.
- The supplied directory contains 1,271 JPG/JPEG/PNG files (not exactly 1,000), about 6.4 GB; samples are mostly 4000x4000 PNG or 3375x4500 JPEG. Stress verification must report the actual count and avoid writing into the input tree.
- Current app source is clean at commit `1afb566` before this task; no source changes have been made by the primary agent yet.

## Stress evidence
- Cancellation harness on the supplied directory saw 1,271 images, cancelled classification at 128 completed sources, returned terminal `cancelled`, Worker exit code 0, 37.647 seconds, and child peak RSS 2,088.0 MB.
- Preview harness decoded 300 unique images through the same `read_preview` protocol, completed 300/300 with 0 errors, Worker exit code 0, 11.063 seconds, and child peak RSS 679.3 MB. The input directory remained read-only.
