"""H2b: byte-exact gated PNG prototype vs renderer._encode_png (12 transparent canvases)."""

from common import LIMIT_MB, PNG_SOURCES, background_to_alpha_numpy, encode_png_gated_exact, env, median_time
from PIL import Image

from pixelflow_core import renderer as R
from pixelflow_core.contracts import build_transparent_request

RENDERER = R.Renderer()
R._background_to_alpha = background_to_alpha_numpy
print(env())
print("| source | size | orig s | exact-gated s | encodes/skipped L9 | bytes equal |")
t_orig = t_new = 0.0
for path in PNG_SOURCES:
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    analysis = RENDERER._analyze_source(image)
    for size, target in R.TRANSPARENT_SIZES.items():
        req = build_transparent_request(str(path), "/tmp/x", size, output_path="/tmp/x/t.png", max_size_mb=LIMIT_MB)
        rgba = RENDERER._compose(image, req, target, True, analysis).convert("RGBA")
        a, orig = median_time(lambda: R._encode_png(rgba, LIMIT_MB))
        b, (new, stats) = median_time(lambda: encode_png_gated_exact(rgba, LIMIT_MB))
        t_orig += a
        t_new += b
        print(f"| {path.name} | {size} | {a:.3f} | {b:.3f} | {stats['encodes']}/{stats['skipped_l9']} | {orig == new} |")
print(f"sum orig={t_orig:.2f}s exact-gated={t_new:.2f}s")
