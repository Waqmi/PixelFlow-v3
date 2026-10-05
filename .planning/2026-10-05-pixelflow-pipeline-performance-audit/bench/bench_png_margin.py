"""H2c: choose PNG_PROBE_MARGIN from real transparent canvases (no product code changes).

Corpus: every .png/.jpg/.jpeg/.webp under ASSETS (recursive), excluding paths that
contain App output markers; sampled to 60 sources round-robin over leaf directories.
Each source -> 4 TRANSPARENT_SIZES canvases via Renderer._analyze_source + _compose.

Phase A (process pool): per canvas and candidate (raw, q256, q128, q64, q32) record
L6 bytes, L9+optimize bytes and L6/L9.
Phase B (sequential, single process, for clean timings): HEAD renderer._encode_png
vs current _encode_png with PNG_PROBE_MARGIN monkeypatched to each margin;
byte equality per canvas and total encode seconds.
Raw output is written to results/bench_png_margin.txt.
"""

from __future__ import annotations

import ast
import math
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
from pathlib import Path

from common import ASSETS, LIMIT_MB, PNG_SOURCES, REPO, env

import numpy as np
from PIL import Image

from pixelflow_core import renderer as R
from pixelflow_core.contracts import build_transparent_request

SAMPLE = 60
MARGINS = (0.10, 0.20, 0.35, 0.50)
EXCLUDE = ("透明产品图", "含Logo", "无Logo", "唯品专享")
SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
LIMIT = LIMIT_MB * 1024 * 1024
OUT = Path(__file__).resolve().parent / "results" / "bench_png_margin.txt"
WORKERS = 3
_lines: list[str] = []


def log(text: str = "") -> None:
    print(text, flush=True)
    _lines.append(text)


def corpus() -> tuple[list[Path], dict[Path, tuple[int, int]]]:
    """Round-robin quota per leaf dir (sorted), evenly spaced picks inside each dir."""
    by_dir: dict[Path, list[Path]] = defaultdict(list)
    for path in sorted(ASSETS.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUFFIXES and not any(k in str(path) for k in EXCLUDE):
            by_dir[path.parent].append(path)
    dirs = sorted(by_dir)
    quota = dict.fromkeys(dirs, 0)
    total = sum(len(v) for v in by_dir.values())
    while sum(quota.values()) < min(SAMPLE, total):
        for d in dirs:
            if quota[d] < len(by_dir[d]) and sum(quota.values()) < SAMPLE:
                quota[d] += 1
    picked = []
    for d in dirs:
        files, q = by_dir[d], quota[d]
        picked += [files[math.floor((i + 0.5) * len(files) / q)] for i in range(q)]
    return picked, {d: (quota[d], len(by_dir[d])) for d in dirs}


def candidates(rgba: Image.Image):
    yield "raw", rgba
    for colors in (256, 128, 64, 32):
        try:
            yield f"q{colors}", rgba.quantize(colors=colors, method=Image.Quantize.FASTOCTREE).convert("RGBA")
        except (ValueError, OSError):
            return


def png_len(image: Image.Image, **kw) -> int:
    stream = BytesIO()
    image.save(stream, format="PNG", **kw)
    return len(stream.getvalue())


def phase_a(path_str: str, store: str) -> list[dict]:
    """Compose the 4 transparent canvases of one source and measure every candidate."""
    renderer = R.Renderer()
    with Image.open(path_str) as opened:  # same as Renderer._source_image
        opened.load()
        image = opened.copy()
    analysis = renderer._analyze_source(image)
    kind = "alpha" if analysis.edge_color is None else "light_bg" if analysis.light_background else "opaque_non_light"
    rows = []
    for size, target in R.TRANSPARENT_SIZES.items():
        req = build_transparent_request(path_str, store, size, output_path=f"{store}/t.png", max_size_mb=LIMIT_MB)
        canvas = renderer._compose(image, req, target, True, analysis).convert("RGBA")
        key = f"{abs(hash((path_str, size))):x}"
        np.save(Path(store) / f"{key}.npy", np.asarray(canvas))
        cands = []
        for name, candidate in candidates(canvas):
            l6 = png_len(candidate, compress_level=6)
            l9 = png_len(candidate, optimize=True, compress_level=9)
            cands.append({"cand": name, "l6": l6, "l9": l9, "ratio": l6 / l9})
        rows.append({"path": path_str, "size": size, "kind": kind, "key": key, "cands": cands})
    return rows


def old_encode_png():
    source = subprocess.run(["git", "show", "HEAD:worker/pixelflow_core/renderer.py"], cwd=REPO,
                            check=True, capture_output=True, text=True).stdout
    ns = dict(vars(R))
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_encode_png":
            exec(compile(ast.Module([node], []), "renderer_head", "exec"), ns)
    assert "PNG_PROBE_MARGIN" not in source, "HEAD already has the probe; reference would not be the original"
    return ns["_encode_png"]


def chosen_index(cands: list[dict]) -> int | None:
    """Index the original returns as 'first L9 that fits' (None = nothing fits, fallback)."""
    return next((i for i, c in enumerate(cands) if c["l9"] <= LIMIT), None)


def main() -> None:
    log(env())
    log(f"limit={LIMIT_MB} MB ({LIMIT:.0f} B); margins={MARGINS}; workers(phase A)={WORKERS}")
    sample, quotas = corpus()
    log(f"\n## Corpus: {sum(t for _q, t in quotas.values())} eligible sources in {len(quotas)} leaf dirs; "
        f"sampled {len(sample)} (round-robin quota over sorted leaf dirs, evenly spaced picks within a dir)")
    for d, (q, t) in quotas.items():
        log(f"- {d.relative_to(ASSETS)}: {q}/{t}")
    knee = [str(p) for p in PNG_SOURCES]
    sources = list(dict.fromkeys([str(p) for p in sample] + knee))
    store = tempfile.mkdtemp(prefix="pf-margin-")
    try:
        started = time.perf_counter()
        rows: list[dict] = []
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            for result in pool.map(phase_a, sources, [store] * len(sources)):
                rows += result
                print(f"phase A {len(rows)}/{len(sources) * 4} canvases", file=sys.stderr, flush=True)
        log(f"\nphase A: {len(rows)} canvases in {time.perf_counter() - started:.1f}s")
        sample_set, knee_set = {str(p) for p in sample}, set(knee)

        # ---- distribution ----
        entries = [(r, c) for r in rows for c in r["cands"]]
        ratios = np.array([c["ratio"] for _r, c in entries])
        q = np.percentile(ratios, [50, 95, 99])
        log(f"\n## L6/L9 over {len(entries)} (canvas, candidate) entries from {len(rows)} canvases")
        log(f"min={ratios.min():.3f} p50={q[0]:.3f} p95={q[1]:.3f} p99={q[2]:.3f} max={ratios.max():.3f}")
        log("\n| source kind | sources | canvases | entries | max L6/L9 | p99 |")
        for kind in ("alpha", "light_bg", "opaque_non_light"):
            sel = np.array([c["ratio"] for r, c in entries if r["kind"] == kind])
            if sel.size:
                n_src = len({r["path"] for r in rows if r["kind"] == kind})
                n_can = sum(1 for r in rows if r["kind"] == kind)
                log(f"| {kind} | {n_src} | {n_can} | {sel.size} | {sel.max():.3f} | {np.percentile(sel, 99):.3f} |")
        log("\n| candidate | entries | max L6/L9 |")
        for name in ("raw", "q256", "q128", "q64", "q32"):
            sel = [c["ratio"] for _r, c in entries if c["cand"] == name]
            if sel:
                log(f"| {name} | {len(sel)} | {max(sel):.3f} |")

        log("\n## Entries with L6/L9 > 1.10  (risk = L6 > limit*1.10 and L9 <= limit; "
            "diverges = risk on the candidate the original returns)")
        log("| file | size | kind | cand | L6 | L9 | ratio | risk | diverges@0.10 |")
        for r, c in sorted(entries, key=lambda e: -e[1]["ratio"]):
            if c["ratio"] <= 1.10:
                continue
            k = chosen_index(r["cands"])
            risk = c["l6"] > LIMIT * 1.10 and c["l9"] <= LIMIT
            div = k is not None and r["cands"][k] is c and c["l6"] > LIMIT * 1.10
            log(f"| {Path(r['path']).relative_to(ASSETS)} | {r['size']} | {r['kind']} | {c['cand']} | {c['l6']} | "
                f"{c['l9']} | {c['ratio']:.3f} | {risk} | {div} |")

        # Margin each canvas needs so the original's chosen candidate is not skipped.
        need = [(r["cands"][k]["l6"] / LIMIT - 1, r) for r in rows if (k := chosen_index(r["cands"])) is not None]
        fallback = sum(1 for r in rows if chosen_index(r["cands"]) is None)
        req_margin, req_row = max(need, key=lambda x: x[0]) if need else (float("-inf"), None)
        log(f"\noriginal picks: {len(need)} canvases fit some candidate, {fallback} fall back (nothing fits)")
        if req_row:
            log(f"minimum exact margin from L6/L9 data: {req_margin:.4f} "
                f"({Path(req_row['path']).relative_to(ASSETS)} {req_row['size']})")
        picks = defaultdict(int)
        for r in rows:
            k = chosen_index(r["cands"])
            picks["fallback" if k is None else r["cands"][k]["cand"]] += 1
        log(f"original chosen candidate histogram: {dict(picks)}")

        # ---- phase B: real byte comparison + timing ----
        old = old_encode_png()
        variants = [("HEAD", None)] + [(f"{m:.2f}", m) for m in MARGINS]
        times = {name: {"knee": 0.0, "corpus": 0.0} for name, _ in variants}
        mismatch = {name: {"knee": [], "corpus": []} for name, _ in variants if name != "HEAD"}
        started = time.perf_counter()
        for i, r in enumerate(rows):
            canvas = Image.fromarray(np.load(Path(store) / f"{r['key']}.npy"), "RGBA")
            groups = [g for g, s in (("knee", knee_set), ("corpus", sample_set)) if r["path"] in s]
            reference = None
            for name, margin in variants:
                if margin is None:
                    fn = old
                else:
                    R.PNG_PROBE_MARGIN = margin
                    fn = R._encode_png
                t0 = time.perf_counter()
                payload = fn(canvas, LIMIT_MB)
                dt = time.perf_counter() - t0
                for g in groups:
                    times[name][g] += dt
                if margin is None:
                    reference = payload
                elif payload != reference:
                    for g in groups:
                        mismatch[name][g].append(f"{Path(r['path']).relative_to(ASSETS)} {r['size']}")
            print(f"phase B {i + 1}/{len(rows)}", file=sys.stderr, flush=True)
        R.PNG_PROBE_MARGIN = 0.10
        log(f"\nphase B: {time.perf_counter() - started:.1f}s (sequential)")
        n_knee = sum(1 for r in rows if r["path"] in knee_set)
        n_corpus = sum(1 for r in rows if r["path"] in sample_set)
        log(f"\n## Byte equality vs HEAD _encode_png and total encode seconds "
            f"(knee = {n_knee} 护膝 PNG canvases, corpus = {n_corpus} sampled canvases)")
        log("| variant | knee s | knee mismatches | corpus s | corpus mismatches |")
        for name, _ in variants:
            km = "-" if name == "HEAD" else len(mismatch[name]["knee"])
            cm = "-" if name == "HEAD" else len(mismatch[name]["corpus"])
            log(f"| {name} | {times[name]['knee']:.2f} | {km} | {times[name]['corpus']:.2f} | {cm} |")
        for name, groups in mismatch.items():
            for g, items in groups.items():
                for item in items:
                    log(f"mismatch margin={name} [{g}]: {item}")
    finally:
        shutil.rmtree(store, ignore_errors=True)
        OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
