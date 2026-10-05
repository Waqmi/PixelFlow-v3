"""H5: end-to-end V3EngineAdapter.run wall clock with render_workers=1/2/3.

Batch A reproduces the smoke shape: 护膝 folder (3 PNG + 2 WebP), size 1440x1440,
logo + no-logo + VIP + 4 transparent sizes per PNG, 0.9 MB limit -> 27 outputs.
Batch B: 3 model JPEGs (热能长裤), 3 standard sizes, side-fill anchor plans -> 21 outputs.
Variants are monkeypatched in-process only; product code is untouched.
Outputs go to a temp dir that is removed afterwards.
"""

from __future__ import annotations

import shutil
import statistics
import tempfile
import time
from pathlib import Path

from common import (ASSETS, LIMIT_MB, MODEL_SOURCES, REPEATS, background_to_alpha_numpy,
                    encode_png_gated, env)
from PIL import Image

from bench_micro import model_plan
from pixelflow_core import engine as E
from pixelflow_core import renderer as R
from pixelflow_core.engine import GenerationRequest, V3EngineAdapter

ORIG_ALPHA, ORIG_PNG = R._background_to_alpha, R._encode_png
ORIG_POOL = E.ThreadPoolExecutor
POOL = {"workers": None, "seconds": 0.0}


class Pool(ORIG_POOL):
    """Force max_workers and time the render pool block."""

    def __init__(self, max_workers=None, **kw):
        super().__init__(max_workers=POOL["workers"] or max_workers, **kw)

    def __enter__(self):
        self._t = time.perf_counter()
        return super().__enter__()

    def __exit__(self, *exc):
        result = super().__exit__(*exc)
        POOL["seconds"] = time.perf_counter() - self._t
        return result


def set_variant(name: str) -> None:
    R._background_to_alpha = background_to_alpha_numpy if name != "orig" else ORIG_ALPHA
    R._encode_png = (lambda img, mb: encode_png_gated(img, mb)[0]) if name == "numpy+gated" else ORIG_PNG


def run_once(request_factory, workers: int) -> dict:
    R.Renderer._source_image.cache_clear()  # cold decode every run (class-level lru_cache)
    POOL["workers"] = workers
    out = Path(tempfile.mkdtemp(prefix="pixelflow-bench-"))
    try:
        request = request_factory(out)
        wall0, cpu0 = time.perf_counter(), time.process_time()
        result = V3EngineAdapter().run(request)
        wall, cpu = time.perf_counter() - wall0, time.process_time() - cpu0
        perf = result["performance"]
        return {
            "wall": wall, "cpu": cpu, "pool": POOL["seconds"],
            "outputs": len(result["outputs"]), "failures": len(result["failures"]),
            "compose": perf.get("compose_seconds", 0.0), "encode": perf.get("encode_seconds", 0.0),
        }
    finally:
        shutil.rmtree(out, ignore_errors=True)


def knee_request(out: Path) -> GenerationRequest:
    root = ASSETS / "护膝"
    sources = sorted(p for p in root.iterdir() if p.suffix.lower() in {".png", ".webp"})
    return GenerationRequest(
        product_root=root, output_root=out, selected_sizes=("1440x1440",),
        include_logo=True, include_vip=True, max_size_mb=LIMIT_MB, mode="v3", force_rerender=True,
        source_type_overrides={str(p): "main_product" for p in sources},
    )


def model_request(out: Path) -> GenerationRequest:
    sources = MODEL_SOURCES[:3]
    plans = []
    for path in sources:
        with Image.open(path) as im:
            size_px = im.size
        for size, target in R.STANDARD_SIZES.items():
            plans.append(model_plan(path, size, target, size_px, True))
    return GenerationRequest(
        product_root=sources[0].parent, output_root=out, selected_sizes=tuple(R.STANDARD_SIZES),
        include_logo=True, include_vip=True, max_size_mb=LIMIT_MB, mode="v3", force_rerender=True,
        source_filter={str(p) for p in sources}, source_type_overrides={str(p): "model" for p in sources},
        composition_plans=tuple(plans),
    )


def bench(label, factory, variants):
    print(f"\n## {label} (median of {REPEATS})")
    print("| variant | workers | wall s | render pool s | process CPU s | CPU/wall | sum compose s | sum encode s | outputs | failures |")
    for variant in variants:
        set_variant(variant)
        for workers in (1, 2, 3):
            runs = [run_once(factory, workers) for _ in range(REPEATS)]
            med = {k: statistics.median(r[k] for r in runs) for k in runs[0]}
            print(f"| {variant} | {workers} | {med['wall']:.2f} | {med['pool']:.2f} | {med['cpu']:.2f} | {med['cpu'] / med['wall']:.2f} "
                  f"| {med['compose']:.2f} | {med['encode']:.2f} | {int(med['outputs'])} | {int(med['failures'])} |", flush=True)
    set_variant("orig")


if __name__ == "__main__":
    print(env())
    E.ThreadPoolExecutor = Pool
    try:
        bench("Batch A: 护膝 5 sources -> 27 outputs", knee_request, ("orig", "numpy", "numpy+gated"))
        bench("Batch B: 热能长裤 3 model JPEG 4943x7415, side fill, 3 sizes -> 21 outputs", model_request, ("orig",))
    finally:
        E.ThreadPoolExecutor = ORIG_POOL
