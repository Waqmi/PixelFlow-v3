# PixelFlow v3 ordered performance worker report

Date: 2026-10-05
Branch: `perf/render-alpha-png`
Base: `0612774 perf(worker): vectorize alpha cutout and defer PNG level-9 encodes`

## Stage 0 — baseline

- Renderer tests: 20 passed.
- Engine tests: 48 passed.
- Full worker suite before this work: 195 passed, 1 skipped.
- Model batch baseline (3 sources, 21 outputs, 0 failures; median of 3): 1 worker 17.30s, 2 workers 11.67s, 3 workers 6.72s.

## Stage A — per-source compose reuse

Implemented in `worker/pixelflow_core/renderer.py` with two renderer tests.

- Cache is a local dictionary created inside one `render_source_batch` call.
- The key covers target/transparent flags, plan identity, request and metadata source/background values, and all layout/scale/position/mirror inputs read by `_compose`.
- Logo/no-logo requests sharing the same plan reuse one compose result; overlay receives a converted image and does not mutate the cached image.
- Different `source_scale_adjustment` values do not reuse.
- Renderer tests: 22 passed.
- Full worker suite after A: 197 passed, 1 skipped.
- Model batch after A (same shape, median of 3): 1 worker 9.25s, 2 workers 6.31s, 3 workers 3.75s; 21 outputs, 0 failures.
- Cache disabled versus enabled output parity: 21/21 files have identical SHA-256 hashes.

## Stage B — wall-clock reporting

Implemented in `worker/pixelflow_core/engine.py` and covered in `test_engine.py`.

- `performance.wall_seconds` measures the complete `V3EngineAdapter.run` call.
- `performance.render_wall_seconds` measures the render thread-pool interval.
- Existing cumulative `performance.total_seconds` remains unchanged.
- Engine tests: 48 passed; full worker suite: 197 passed, 1 skipped.
- Protocol check: `npm run protocol:check` passed; the envelope schema does not constrain result payload fields.

## Stage C — duplicate model probe suppression

Implemented in `apps/desktop/src/App.vue`.

- Automatic `ready` probing is limited to the main window; settings, report, and confirm windows do not auto-probe.
- An in-flight request is ignored while `modelTesting` is active; manual testing and model-path discovery flows remain available.
- Frontend build: `npm run build` passed (`vue-tsc --noEmit` and Vite build).

## Final scope and risks

No commits or pushes were made. `legacy_engine`, the two deleted detection sidecars, rules versions, render concurrency, model locks, JPEG quality, and VIP intermediate JPEG handling were untouched. Existing unrelated `.planning` changes and `:memory:.ses` were preserved.

The compose cache deliberately uses plan object identity, so equivalent but separately allocated plans do not share; this is conservative and avoids broadening the cache lifetime. The PNG probe heuristic from `0612774` remains unchanged.
