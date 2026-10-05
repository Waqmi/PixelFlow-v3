"""H2 detail: per-candidate PNG cost and the level-6-gated prototype vs renderer._encode_png."""

from __future__ import annotations

from io import BytesIO

from common import (LIMIT_MB, PNG_SOURCES, background_to_alpha_numpy, encode_png_gated, env,
                    median_time, same_pixels)
from PIL import Image

from pixelflow_core import renderer as R
from pixelflow_core.contracts import build_transparent_request

MB = 1024 * 1024
RENDERER = R.Renderer()


def enc(img, **kw):
    s = BytesIO()
    img.save(s, format="PNG", **kw)
    return s.getvalue()


def main():
    print(env())
    R._background_to_alpha = background_to_alpha_numpy  # faster setup; pixels proven identical in bench_micro
    print("\n## candidate breakdown: L9+optimize seconds / bytes per candidate (single run), then orig vs gated (median of 3)")
    print("| source | size | raw L9 | q256 L9 | q128 L9 | q64 L9 | q32 L9 | orig picks | orig s | gated s | gated bytes | gated encodes/skipped L9 | pixels equal |")
    total_orig = total_gated = 0.0
    for path in PNG_SOURCES:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        analysis = RENDERER._analyze_source(image)
        for size, target in R.TRANSPARENT_SIZES.items():
            req = build_transparent_request(str(path), "/tmp/x", size, output_path="/tmp/x/t.png", max_size_mb=LIMIT_MB)
            rgba = RENDERER._compose(image, req, target, True, analysis).convert("RGBA")
            cells, pick = [], None
            for index, colors in enumerate((None, 256, 128, 64, 32)):
                cand = rgba if colors is None else rgba.quantize(colors=colors, method=Image.Quantize.FASTOCTREE).convert("RGBA")
                t, payload = median_time(lambda: enc(cand, optimize=True, compress_level=9), repeats=1)
                fits = len(payload) <= LIMIT_MB * MB
                if fits and pick is None:
                    pick = ("raw", "q256", "q128", "q64", "q32")[index]
                cells.append(f"{t:.2f}s/{len(payload) // 1024}K{'*' if fits else ''}")
            t_o, orig = median_time(lambda: R._encode_png(rgba, LIMIT_MB))
            t_g, (gated, stats) = median_time(lambda: encode_png_gated(rgba, LIMIT_MB))
            total_orig += t_o
            total_gated += t_g
            print(f"| {path.name} | {size} | " + " | ".join(cells) + f" | {pick} | {t_o:.3f} | {t_g:.3f} | {len(gated)} "
                  f"| {stats['encodes']}/{stats['skipped_l9']} | {same_pixels(orig, gated)} |")
    print(f"\nsum orig _encode_png={total_orig:.2f}s, sum gated={total_gated:.2f}s ('*' = fits {LIMIT_MB} MB)")


if __name__ == "__main__":
    main()
