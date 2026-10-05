"""H1-H4 micro benchmarks: alpha cutout, PNG/JPEG encode, per-path compose.

Run: PYTHONPATH=worker worker/.venv/bin/python .planning/.../bench/bench_micro.py
Writes nothing outside memory.
"""

from __future__ import annotations

import time
from io import BytesIO

from common import (LIMIT_MB, MODEL_SOURCES, PNG_SOURCES, background_to_alpha_numpy,
                    encode_png_tiered, env, median_time, same_pixels)
from PIL import Image

from pixelflow_core import renderer as R
from pixelflow_core.contracts import (CompositionPlan, build_main_request, build_model_request,
                                      build_transparent_request, build_vip_request)

RENDERER = R.Renderer()
OUT = "/tmp/pixelflow-bench-unused"
MB = 1024 * 1024


def load(path):
    with Image.open(path) as opened:
        opened.load()
        return opened.copy()


def h1_alpha():
    print("\n## H1 _background_to_alpha (median of 3)")
    print("| source | size | MP | light_bg | edge_color | orig s | numpy s | speedup | bytes equal |")
    rows = {}
    for path in PNG_SOURCES:
        image = load(path)
        analysis = RENDERER._analyze_source(image)
        working = image.convert("RGBA")
        if analysis.edge_color is None:
            print(f"| {path.name} | {image.size} | - | {analysis.light_background} | None | skip | | | |")
            continue
        t_orig, a = median_time(lambda: R._background_to_alpha(working, analysis.edge_color))
        t_np, b = median_time(lambda: background_to_alpha_numpy(working, analysis.edge_color))
        equal = a.tobytes() == b.tobytes() and a.mode == b.mode
        rows[path.name] = (t_orig, t_np)
        mp = image.width * image.height / 1e6
        print(f"| {path.name} | {image.width}x{image.height} | {mp:.2f} | {analysis.light_background} | {analysis.edge_color} "
              f"| {t_orig:.3f} | {t_np:.4f} | {t_orig / t_np:.0f}x | {equal} |")
    return rows


def transparent_request(path, size):
    return build_transparent_request(str(path), OUT, size, output_path=f"{OUT}/t.png", max_size_mb=LIMIT_MB)


def h1_compose_transparent():
    print("\n## H1b transparent _compose per size (orig vs numpy alpha monkeypatched), median of 3")
    print("| source | size | compose orig s | compose numpy s |")
    composed = {}
    original = R._background_to_alpha
    for path in PNG_SOURCES:
        image = load(path)
        analysis = RENDERER._analyze_source(image)
        for size, target in R.TRANSPARENT_SIZES.items():
            req = transparent_request(path, size)
            t_orig, canvas = median_time(lambda: RENDERER._compose(image, req, target, True, analysis))
            R._background_to_alpha = background_to_alpha_numpy
            try:
                t_np, canvas_np = median_time(lambda: RENDERER._compose(image, req, target, True, analysis))
            finally:
                R._background_to_alpha = original
            assert canvas.tobytes() == canvas_np.tobytes(), "numpy alpha changed composed pixels"
            composed[(path.name, size)] = canvas
            print(f"| {path.name} | {size} | {t_orig:.3f} | {t_np:.3f} |")
    return composed


def _png(img, **kw):
    stream = BytesIO()
    img.save(stream, format="PNG", **kw)
    return stream.getvalue()


def h2_png(composed):
    print(f"\n## H2 PNG encode of transparent canvases (limit {LIMIT_MB} MB), median of 3")
    print("| source | size | orig _encode_png s | orig bytes | L9+opt s | L9 bytes | L6 s | L6 bytes | L1 s | L1 bytes "
          "| raw fits? | q256 s | q128 s | q64 s | q32 s | tiered s | tiered bytes | tiered encodes/quantized | pixels equal |")
    for (name, size), canvas in composed.items():
        rgba = canvas.convert("RGBA")
        t_orig, orig = median_time(lambda: R._encode_png(rgba, LIMIT_MB))
        t9, b9 = median_time(lambda: _png(rgba, optimize=True, compress_level=9))
        t6, b6 = median_time(lambda: _png(rgba, compress_level=6))
        t1, b1 = median_time(lambda: _png(rgba, compress_level=1))
        qt = []
        for colors in (256, 128, 64, 32):
            tq, _ = median_time(lambda: rgba.quantize(colors=colors, method=Image.Quantize.FASTOCTREE).convert("RGBA"))
            qt.append(tq)
        t_tier, (tiered, stats) = median_time(lambda: encode_png_tiered(rgba, LIMIT_MB))
        print(f"| {name} | {size} | {t_orig:.3f} | {len(orig)} | {t9:.3f} | {len(b9)} | {t6:.3f} | {len(b6)} | {t1:.3f} | {len(b1)} "
              f"| {len(b9) <= LIMIT_MB * MB} | " + " | ".join(f"{v:.3f}" for v in qt)
              + f" | {t_tier:.3f} | {len(tiered)} | {stats['encodes']}/{stats['quantized']} | {same_pixels(orig, tiered)} |")


def encode_jpeg_counted(image, max_size_mb):
    """Same loop as renderer._encode_jpeg, plus an attempt counter."""
    if image.mode != "RGB":
        image = image.convert("RGB")
    quality, attempts = 95, 0
    while True:
        attempts += 1
        stream = BytesIO()
        image.save(stream, format="JPEG", quality=quality, subsampling=0, optimize=True)
        payload = stream.getvalue()
        if max_size_mb is None or len(payload) <= max_size_mb * MB or quality <= 45:
            return payload, attempts, quality


def model_plan(path, size, target, image_size, side_fill):
    """Anchor window with the target aspect; side_fill widens it past both edges."""
    w_src, h_src = image_size
    h = 0.95 if side_fill else 0.5
    w = h * h_src * target[0] / target[1] / w_src
    if not side_fill:
        w = min(w, 0.95)
        h = w * w_src * target[1] / target[0] / h_src
    left = 0.5 - w / 2
    top = 0.025 if side_fill else 0.3
    return CompositionPlan(source=str(path), size=size, composition_mode="anchor_crop",
                           crop_box=(left, top, left + w, top + h),
                           evidence={"side_fill": {"strip_left": 0.03, "strip_right": 0.03}} if side_fill else {})


def h3_h4():
    print("\n## H3/H4 per-output compose / overlay / JPEG encode (median of 3; limit 0.9 MB)")
    print("| source | kind | size | compose s | overlay(logo) s | overlay(nologo) s | jpeg s | attempts | final q | bytes | logo/nologo compose identical |")
    cases = []
    for path in PNG_SOURCES[:1] + sorted(PNG_SOURCES[0].parent.glob("*.webp"))[:1]:
        image = load(path)
        for size, target in R.STANDARD_SIZES.items():
            cases.append((path, image, "product", size, target, None))
    model_path = MODEL_SOURCES[0]
    model_image = load(model_path)
    for size, target in R.STANDARD_SIZES.items():
        cases.append((model_path, model_image, "model-fallback", size, target, None))
        cases.append((model_path, model_image, "model-anchor", size, target, model_plan(model_path, size, target, model_image.size, False)))
        cases.append((model_path, model_image, "model-sidefill", size, target, model_plan(model_path, size, target, model_image.size, True)))
    for path, image, kind, size, target, plan in cases:
        analysis = RENDERER._analyze_source(image)
        builder = build_model_request if kind.startswith("model") else build_main_request
        source_type = "model" if kind.startswith("model") else "main_product"
        req_logo = builder(str(path), OUT, size, output_path=f"{OUT}/a.jpg", plan=plan, include_logo=True,
                           max_size_mb=LIMIT_MB, source_type=source_type)
        req_nologo = builder(str(path), OUT, size, output_path=f"{OUT}/b.jpg", plan=plan, include_logo=False,
                             max_size_mb=LIMIT_MB, source_type=source_type)
        t_c, composed = median_time(lambda: RENDERER._compose(image, req_logo, target, False, analysis))
        composed_b = RENDERER._compose(image, req_nologo, target, False, analysis)
        identical = composed.tobytes() == composed_b.tobytes()
        t_ol, over = median_time(lambda: RENDERER._overlay(composed, req_logo, False))
        t_on, _ = median_time(lambda: RENDERER._overlay(composed, req_nologo, False))
        rgb = over.convert("RGB")
        t_j, (payload, attempts, quality) = median_time(lambda: encode_jpeg_counted(rgb, LIMIT_MB))
        assert payload == R._encode_jpeg(rgb, LIMIT_MB)
        print(f"| {path.name} {image.width}x{image.height} | {kind} | {size} | {t_c:.3f} | {t_ol:.3f} | {t_on:.3f} | {t_j:.3f} "
              f"| {attempts} | {quality} | {len(payload)} | {identical} |")
    # VIP: compose + overlay (temp JPEG + border) + encode for the product PNG.
    path = PNG_SOURCES[0]
    image = load(path)
    analysis = RENDERER._analyze_source(image)
    vip = build_vip_request(str(path), OUT, "1440x1440", output_path=f"{OUT}/v.jpg", max_size_mb=LIMIT_MB)
    main = build_main_request(str(path), OUT, "1440x1440", output_path=f"{OUT}/m.jpg", include_logo=False, max_size_mb=LIMIT_MB)
    t_c, composed = median_time(lambda: RENDERER._compose(image, vip, (1440, 1440), False, analysis))
    same = composed.tobytes() == RENDERER._compose(image, main, (1440, 1440), False, analysis).tobytes()
    t_o, over = median_time(lambda: RENDERER._overlay(composed, vip, True))
    t_j, (payload, attempts, quality) = median_time(lambda: encode_jpeg_counted(over.convert("RGB"), LIMIT_MB))
    print(f"| {path.name} | vip | 1440x1440 | {t_c:.3f} | {t_o:.3f} (vip) | - | {t_j:.3f} | {attempts} | {quality} | {len(payload)} | vip==nologo compose: {same} |")


def h4_side_fill_breakdown():
    print("\n## H4b side fill breakdown (model 1440x1440, median of 3)")
    from pixelflow_core import side_fill as S
    path = MODEL_SOURCES[0]
    image = load(path).convert("RGB")
    target = (1440, 1440)
    plan = model_plan(path, "1440x1440", target, image.size, True)
    spent = {"extend_side": 0.0, "_patch_field": 0.0}
    orig_ext, orig_patch = S.extend_side, S._patch_field

    def wrap(name, fn):
        def inner(*a, **k):
            started = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                spent[name] += time.perf_counter() - started
        return inner

    R.extend_side = wrap("extend_side", orig_ext)
    S._patch_field = wrap("_patch_field", orig_patch)
    try:
        t, _ = median_time(lambda: R._side_filled_window(image, plan.crop_box, target, plan.evidence["side_fill"]))
    finally:
        R.extend_side, S._patch_field = orig_ext, orig_patch
    print(f"crop_box={tuple(round(v, 3) for v in plan.crop_box)}; _side_filled_window={t:.3f}s; "
          f"per call extend_side(2 sides)={spent['extend_side'] / 3:.3f}s, _patch_field={spent['_patch_field'] / 3:.3f}s")


if __name__ == "__main__":
    print(env())
    for path in PNG_SOURCES + MODEL_SOURCES[:1]:
        with Image.open(path) as im:
            print(f"source {path.parent.name}/{path.name}: {im.size} {im.mode}")
    h1_alpha()
    composed = h1_compose_transparent()
    h2_png(composed)
    h3_h4()
    h4_side_fill_breakdown()
