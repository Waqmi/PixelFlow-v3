"""Synthesize background columns beside a model photo that is too narrow.

Only used when the side strips are plain studio backdrop (``strip_structure``
decides); the low-frequency light falloff is extrapolated and the fine grain is
re-created from the strip itself, so the extension keeps the backdrop texture.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# Strip analysis height; structure is measured on a band scaled to this.
STRUCTURE_BAND_HEIGHT = 720
# Local gradient (luminance levels per pixel) that counts as structure.
STRUCTURE_GRADIENT = 1.0
# Share of strip pixels above that gradient above which the backdrop is not plain.
STRUCTURE_MAX_SHARE = 0.01
# A strip is not plain when many rows differ between its inner and outer half
# by more than PARTIAL_LEVEL (a tape end or mark that stops inside the strip
# cannot be extended); the residual grain is clipped at GRAIN_CLIP robust sigmas
# so outliers are never copied into the fill as blocks. The first SEAM_BLEND
# fill columns fade in from a mirror of the source columns next to the seam.
PARTIAL_LEVEL = 8.0
PARTIAL_MAX_SHARE = 0.015
GRAIN_CLIP = 2.5
SEAM_BLEND = 24


def _box(a: np.ndarray, r: float, axis: int) -> np.ndarray:
    r = int(max(1, r))
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    p = np.pad(a, pad, mode="reflect" if a.shape[axis] > r + 1 else "edge")
    c = np.cumsum(p, axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def blur_f(a: np.ndarray, r: float) -> np.ndarray:
    """Gaussian-like blur (three box passes) over the first two axes."""
    b = r * 0.58
    for _ in range(3):
        a = _box(_box(a, b, 0), b, 1)
    return a


def _robust_sigma(a: np.ndarray, axis=None) -> np.ndarray:
    return 1.4826 * np.median(np.abs(a - np.median(a, axis=axis, keepdims=True)), axis=axis)


def _grain_sigma(a: np.ndarray) -> np.ndarray:
    """Per-channel robust sigma of the fine grain (what a light blur leaves)."""
    return _robust_sigma(a - blur_f(a, 4), axis=(0, 1))[None, None, :]


def strip_partial(strip: Image.Image) -> float:
    """Share of rows whose inner-half and outer-half residual means differ by more than PARTIAL_LEVEL."""
    scale = STRUCTURE_BAND_HEIGHT / strip.height
    strip = strip.convert("L").resize((max(4, round(strip.width * scale)), STRUCTURE_BAND_HEIGHT), Image.Resampling.LANCZOS)
    luma = np.asarray(strip, dtype=np.float32)[:, max(2, strip.width // 12):]
    res = luma - blur_f(luma, max(6, luma.shape[1] / 2))
    half = res.shape[1] // 2
    return float((np.abs(res[:, :half].mean(1) - res[:, half:].mean(1)) > PARTIAL_LEVEL).mean())


def strip_structure(strip: Image.Image) -> float:
    """Share of a backdrop strip's pixels that carry local structure.

    A heavy blur removes the smooth light falloff of a studio backdrop; what
    remains and still has a gradient (objects, edges, hair, floor lines) is
    structure. Grain is suppressed by blurring lightly before the gradient.
    Only the horizontal gradient counts: rows are extended sideways as they
    are, so horizontal lines (floor tape, wall/floor boundaries) are harmless
    while vertical and diagonal edges are not.
    """
    scale = STRUCTURE_BAND_HEIGHT / strip.height
    strip = strip.convert("L").resize((max(4, round(strip.width * scale)), STRUCTURE_BAND_HEIGHT), Image.Resampling.LANCZOS)
    luma = np.asarray(strip, dtype=np.float32)
    local = blur_f(luma - blur_f(luma, max(2, luma.shape[1] / 2)), 1.5)
    gx = np.gradient(local, axis=1)
    return float((np.abs(gx) > STRUCTURE_GRADIENT).mean())


def _patch_field(res: np.ndarray, height: int, ext: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Random feathered patches of the residual, tiled over height x ext."""
    sw = res.shape[1]
    block = max(8, min(block, sw))
    step = max(4, block * 2 // 3)
    acc = np.zeros((height, ext, 3), np.float32)
    wsum = np.zeros((height, ext, 1), np.float32)
    win = np.outer(np.hanning(block + 2)[1:-1], np.hanning(block + 2)[1:-1]).astype(np.float32)[..., None]
    for y in range(-block // 2, height, step):
        for x in range(-block // 2, ext, step):
            sy = rng.integers(0, max(1, height - block))
            sx = rng.integers(0, max(1, sw - block + 1))
            patch = res[sy:sy + block, sx:sx + block]
            y0, x0 = max(0, y), max(0, x)
            y1, x1 = min(height, y + block), min(ext, x + block)
            if y1 <= y0 or x1 <= x0:
                continue
            py, px = y0 - y, x0 - x
            acc[y0:y1, x0:x1] += patch[py:py + y1 - y0, px:px + x1 - x0] * win[py:py + y1 - y0, px:px + x1 - x0]
            wsum[y0:y1, x0:x1] += win[py:py + y1 - y0, px:px + x1 - x0]
    field = acc / np.maximum(wsum, 1e-6)
    # Overlap averaging lowers contrast: restore the residual's spread.
    return field * (res.std() / max(field.std(), 1e-6))


def extend_side(region: np.ndarray, strip_w: int, ext: int, side: str) -> np.ndarray:
    """Return ``ext`` synthesized columns beyond the ``side`` edge of ``region``.

    ``region`` is an (H, W, 3) float array whose outermost ``strip_w`` columns
    on that side are plain background. The rng is created per call so reruns
    produce identical pixels.
    """
    rng = np.random.default_rng(7)
    if side == "left":
        region = region[:, ::-1]
    height = region.shape[0]
    # The strip's inner columns may graze the subject's soft edge: leave them out.
    strip = region[:, -strip_w:][:, max(2, strip_w // 12):]
    strip_w = strip.shape[1]
    low = blur_f(strip, max(6, strip_w // 2))
    res = strip - low
    raw_res = res
    # Horizontal structure (wall/floor steps, tape lines) lives in the per-row
    # mean and stays on its rows; the rest of the residual is grain. Row means
    # of plain backdrop are just noise, so only what exceeds it is kept.
    rows = res.mean(1, keepdims=True)
    res = res - rows
    noise = 1.4826 * np.median(np.abs(rows - np.median(rows, axis=0)), axis=0, keepdims=True)
    # A real horizontal feature spans the strip: keep only what the inner and
    # outer halves agree on, so partial features cannot become streaks.
    half = strip_w // 2
    inner, outer = (res_half.mean(1, keepdims=True) for res_half in (raw_res[:, :half], raw_res[:, half:]))
    rows = np.where(inner * outer > 0, np.sign(inner) * np.minimum(np.abs(inner), np.abs(outer)), 0.0)
    rows = np.sign(rows) * np.maximum(np.abs(rows) - 3 * np.maximum(noise, 1e-3), 0)
    # Outliers of the grain must never be copied into the fill as blocks.
    limit = GRAIN_CLIP * np.maximum(_robust_sigma(res, axis=(0, 1))[None, None, :], 1e-3)
    res = np.clip(res, -limit, limit)
    target = _grain_sigma(strip)
    k = max(3, strip_w // 4)
    edge_val = low[:, -k:].mean(1)
    inner_val = low[:, -2 * k:-k].mean(1)
    slope = (edge_val - inner_val) / k
    x = np.arange(1, ext + 1, dtype=np.float32)
    tau = strip_w * 0.8
    # Saturating slope: the falloff continues but flattens out.
    disp = tau * (1 - np.exp(-x / tau))
    low_ext = blur_f(edge_val[:, None, :] + slope[:, None, :] * disp[None, :, None], 4)
    m = np.arange(ext) % (2 * strip_w)
    mirror = res[:, np.where(m < strip_w, strip_w - 1 - m, m - strip_w)]
    rand = _patch_field(res, height, ext, strip_w // 2, rng)
    # Grain carries no row mean (that would be a streak); rows were handled above.
    rand = rand - rand.mean(1, keepdims=True)
    # Mirror the grain near the seam, random patches further out.
    w = np.exp(-x / max(8.0, strip_w * 0.25))[None, :, None]
    grain = mirror * np.sqrt(w) + rand * np.sqrt(1 - w)
    # Clipping and patch averaging lower the grain energy: match the strip's.
    grain = grain * (target / np.maximum(_grain_sigma(grain), 1e-3))
    synth = np.clip(low_ext + rows + grain, 0, 255)
    # Start from the source columns themselves so tone and texture change gradually.
    n = min(SEAM_BLEND, ext, region.shape[1])
    ramp = (0.5 * (1 + np.cos(np.pi * (np.arange(n) + 0.5) / n)))[None, :, None].astype(np.float32)
    synth[:, :n] = ramp * region[:, -n:][:, ::-1] + (1 - ramp) * synth[:, :n]
    return synth[:, ::-1] if side == "left" else synth
