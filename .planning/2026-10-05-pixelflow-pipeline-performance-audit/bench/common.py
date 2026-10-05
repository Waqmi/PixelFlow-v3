"""Shared helpers for the 2026-10-05 performance bench (no product code changes)."""

from __future__ import annotations

import os
import platform
import statistics
import sys
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import PIL
from PIL import Image

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "worker"))

ASSETS = Path.home() / "Desktop/PixelFlow/05_测试素材与输出"
PNG_SOURCES = sorted((ASSETS / "护膝").glob("*.png"))
MODEL_SOURCES = sorted((ASSETS / "热能长裤").glob("*.jpg"))
LIMIT_MB = 0.9
REPEATS = 3


def env() -> str:
    return (
        f"{platform.platform()} | cpu_count={os.cpu_count()} | python={sys.version.split()[0]} "
        f"| Pillow={PIL.__version__} | numpy={np.__version__}"
    )


def median_time(fn, repeats: int = REPEATS):
    """Run fn ``repeats`` times; return (median seconds, last result)."""
    times = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - started)
    return statistics.median(times), result


def background_to_alpha_numpy(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """Vectorised prototype of renderer._background_to_alpha (same arithmetic)."""
    rgba = image.convert("RGBA")
    rgb = np.asarray(rgba.convert("RGB"), dtype=np.int16)
    base = np.asarray(background, dtype=np.int16)
    distance = np.abs(rgb - base).max(axis=2).astype(np.float64)
    # Python's round() and np.rint are both round-half-to-even; same float64 formula.
    ramp = np.rint((distance - 8) * 255 / 40)
    alpha = np.where(distance <= 8, 0, np.where(distance >= 48, 255, ramp)).astype(np.uint8)
    rgba.putalpha(Image.fromarray(alpha, "L"))
    return rgba


def _quant(rgba: Image.Image, colors: int) -> Image.Image:
    return rgba.quantize(colors=colors, method=Image.Quantize.FASTOCTREE).convert("RGBA")


def encode_png_tiered(image: Image.Image, max_size_mb: float | None, fast_level: int = 1) -> tuple[bytes, dict]:
    """Prototype: lazy candidates + fast-level probe, level 9 only when needed.

    Picks the same candidate index as renderer._encode_png: the first whose
    level-9 encoding fits.  A candidate whose fast encoding already fits is
    returned as the fast encoding (same pixels, more bytes, still under limit).
    """
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    limit = None if max_size_mb is None else max_size_mb * 1024 * 1024
    stats = {"encodes": 0, "quantized": 0}

    def enc(img: Image.Image, **kw) -> bytes:
        stats["encodes"] += 1
        stream = BytesIO()
        img.save(stream, format="PNG", **kw)
        return stream.getvalue()

    def candidates():
        yield rgba
        if limit is None:
            return
        for colors in (256, 128, 64, 32):
            try:
                stats["quantized"] += 1
                yield _quant(rgba, colors)
            except (ValueError, OSError):
                return

    best = b""
    for candidate in candidates():
        fast = enc(candidate, compress_level=fast_level)
        if limit is None or len(fast) <= limit:
            return fast, stats
        slow = enc(candidate, optimize=True, compress_level=9)
        if not best or len(slow) < len(best):
            best = slow
        if len(slow) <= limit:
            return slow, stats
    return best, stats


def same_pixels(a: bytes, b: bytes) -> bool:
    with Image.open(BytesIO(a)) as x, Image.open(BytesIO(b)) as y:
        return x.mode == y.mode and x.size == y.size and x.tobytes() == y.tobytes()


def encode_png_gated(image: Image.Image, max_size_mb: float | None, margin: float = 0.10) -> tuple[bytes, dict]:
    """Prototype: lazy candidates; level-6 size gates the slow level-9+optimize encode.

    For each candidate (raw, q256, q128, q64, q32 -- same order as renderer):
    encode at level 6; if it already fits, return it (same candidate the
    original would pick provided every earlier candidate's L9 also missed);
    if it misses by more than ``margin`` skip the L9 attempt; otherwise try
    L9+optimize exactly like the original.  NOT exact by construction: an
    earlier candidate whose L9 is > margin smaller than its L6 and fits would
    be skipped.  Pixel parity must be checked on a corpus.
    """
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    limit = None if max_size_mb is None else max_size_mb * 1024 * 1024
    stats = {"encodes": 0, "quantized": 0, "skipped_l9": 0}

    def enc(img: Image.Image, **kw) -> bytes:
        stats["encodes"] += 1
        stream = BytesIO()
        img.save(stream, format="PNG", **kw)
        return stream.getvalue()

    if limit is None:
        return enc(rgba, optimize=True, compress_level=9), stats
    best = b""
    smallest_fast = b""
    candidate = rgba
    for colors in (None, 256, 128, 64, 32):
        if colors is not None:
            try:
                stats["quantized"] += 1
                candidate = _quant(rgba, colors)
            except (ValueError, OSError):
                break
        fast = enc(candidate, compress_level=6)
        if len(fast) <= limit:
            return fast, stats
        if not smallest_fast or len(fast) < len(smallest_fast):
            smallest_fast = fast
        if len(fast) > limit * (1 + margin):
            stats["skipped_l9"] += 1
            continue
        slow = enc(candidate, optimize=True, compress_level=9)
        if not best or len(slow) < len(best):
            best = slow
        if len(slow) <= limit:
            return slow, stats
    # Nothing fits: original returns the smallest L9 over all candidates.
    return best or smallest_fast, stats


def encode_png_gated_exact(image: Image.Image, max_size_mb: float | None, margin: float = 0.10) -> tuple[bytes, dict]:
    """Prototype: same as renderer._encode_png, but a level-6 probe skips L9 on hopeless candidates.

    Output bytes equal the original whenever no skipped candidate's L9 would
    have fitted, i.e. whenever L9 >= L6 / (1 + margin) for skipped candidates.
    """
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    limit = None if max_size_mb is None else max_size_mb * 1024 * 1024
    stats = {"encodes": 0, "quantized": 0, "skipped_l9": 0}

    def enc(img: Image.Image, **kw) -> bytes:
        stats["encodes"] += 1
        stream = BytesIO()
        img.save(stream, format="PNG", **kw)
        return stream.getvalue()

    if limit is None:
        return enc(rgba, optimize=True, compress_level=9), stats
    best = b""
    skipped: list[Image.Image] = []
    candidate = rgba
    for colors in (None, 256, 128, 64, 32):
        if colors is not None:
            try:
                stats["quantized"] += 1
                candidate = _quant(rgba, colors)
            except (ValueError, OSError):
                break
        if len(enc(candidate, compress_level=6)) > limit * (1 + margin):
            stats["skipped_l9"] += 1
            skipped.append(candidate)
            continue
        slow = enc(candidate, optimize=True, compress_level=9)
        if not best or len(slow) < len(best):
            best = slow
        if len(slow) <= limit:
            return slow, stats
    # Nothing fits: original returns the smallest L9 over all candidates, so encode the skipped ones too.
    for candidate in skipped:
        slow = enc(candidate, optimize=True, compress_level=9)
        if not best or len(slow) < len(best):
            best = slow
    return best, stats
