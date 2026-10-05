# Pipeline performance measurements

## Smoke result and provenance

- Read-only source: `pixelflow.report-data` in WebKit LocalStorage at `/Users/weiheng/Library/WebKit/com.pixelflow.desktop/WebsiteData/Default/n_V_rTqkIQXeK5jwH6WNpJqBO_AW4yPlypOAZsnMt8s/n_V_rTqkIQXeK5jwH6WNpJqBO_AW4yPlypOAZsnMt8s/LocalStorage/localstorage.sqlite3`. The SQLite database was opened with `mode=ro`; its `ItemTable.value` was decoded as UTF-16LE JSON. Retrieved on 2026-10-05 03:24 UTC.
- The payload's `outputRoot` is `/tmp/pixelflow-smoke-20261005`, which identifies the sample by date. The stored report has no run timestamp, elapsed wall time, run ID, or build identifier, so the exact execution time cannot be recovered from this record.
- The run reported `mode=v3`, `renderer=typed-v3`, 5 sources, 27 validated outputs, 0 failures, 3 items requiring review, `render_workers=3`, and `incremental_reused=0`.
- The five inputs were PNG ×3 and WebP ×2 per the prior AX view; the UI max-size setting was 0.90 MB. Whether templates were enabled is unknown from the evidence recorded here.
- The inspected beta app bundle is `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/dist/PixelFlow-v3-beta.app`, version `3.0.0-beta.1`; its embedded Worker SHA-256 was `05f4ccc69488d065b75e9ea9a561c97fcb56b4aa316f2579c1572925914559c0`. The report does not store either value or a Git SHA, so these observations cannot independently bind the saved result to a precise source revision or binary. This provenance gap alone does not establish a mismatch.
- At retrieval, Python reported 10 logical CPUs and 16 GiB physical memory (`os.cpu_count()` and `SC_PHYS_PAGES * SC_PAGE_SIZE`). The smoke record does not include host metadata, so treat these as audit-host observations, not run-attached provenance.

## Recorded stage timings

| Stage | Recorded seconds |
| --- | ---: |
| Classification | 0.000000542 |
| Decode | 1.430314 |
| Analysis | 2.631023 |
| Compose | 42.405328 |
| Overlay | 0.783275 |
| Encode | 27.363353 |
| Write | 0.677457 |
| `performance.total_seconds` | 75.290751 |

Compose is about 56.3% and encode about 36.3% of the summed stage time; together they account for about 92.7%. These shares describe only this sample's recorded stage-time sum. They are not shares of user wait time, CPU time, or throughput.

## Timer semantics and interpretation

In `worker/pixelflow_core/engine.py`, the renderer uses up to three concurrent source workers (around lines 322 and 627), adds each completed source batch's numeric timing fields into `render_timings`, then defines the report's `total_seconds` as the sum of classification, decode, analysis, compose, overlay, encode, and write (around lines 650–692). In `worker/pixelflow_core/renderer.py` (around lines 1093–1156), decode and analysis are timed per source, while compose/overlay/encode/write are accumulated over outputs. Source jobs can overlap, so these accumulated `perf_counter` durations are not end-to-end wall time. The engine result contains no reliable batch wall-clock duration; do not use 75.291 seconds as elapsed user wait or derive output throughput from it.

The report's classification timer is effectively zero. The smoke used confirmed source classifications, so this does not benchmark Pose/Seg inference. The report has no independent model-stage timer, and its classification field cannot establish model latency. The ONNX session setup in `worker/pixelflow_core/model_assistant.py` selects `CPUExecutionProvider` but does not explicitly set ONNX Runtime intra-op or inter-op thread counts; actual model-stage duration and thread behavior remain unmeasured here.

## Limits

- This is one small 5-source / 27-output smoke result. It has no exact run timestamp, reliable batch wall time, repeated-run variance, model-stage breakdown, or run-linked build hash.
- `incremental_reused=0` is an output-reuse report field; it does not establish whether lower-level decode/analysis/model caches were warm or cold.
- No additional batch was run for this audit. Historical preview, cancellation, and smoke workloads are not normalized to this workload and are not used for speed comparisons.
