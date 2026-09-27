#!/usr/bin/env python3
"""Compare a generated output tree with a recorded v2.6 app baseline."""

from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from pixelflow_core.compare import compare_output_trees


def digest(path: Path) -> tuple[str, str, list[int], str]:
    raw = path.read_bytes()
    with Image.open(path) as image:
        pixels = hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()
        return hashlib.sha256(raw).hexdigest(), pixels, list(image.size), image.mode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path, help="persist the JSON result")
    parser.add_argument(
        "--compare-root",
        type=Path,
        help="also compare this legacy tree with OUTPUT_DIR and include pixel metrics",
    )
    parser.add_argument("--mode", default="verify", help="label stored in the report")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    root = args.output
    expected = {item["path"]: item for item in manifest["items"]}
    actual = {str(path.relative_to(root)): path for path in root.rglob("*") if path.is_file()}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    mismatches = []
    for name in sorted(set(expected) & set(actual)):
        file_sha, pixel_sha, size, mode = digest(actual[name])
        item = expected[name]
        if (file_sha, pixel_sha, size, mode) != (
            item["sha256"], item["pixel_sha256"], item["size"], item["mode"]
        ):
            mismatches.append(name)
    report = {
        "mode": args.mode,
        "expected": len(expected), "actual": len(actual),
        "missing": len(missing), "extra": len(extra),
        "exact": len(expected) - len(missing) - len(mismatches),
        "mismatches": len(mismatches),
        "missing_paths": missing,
        "extra_paths": extra,
        "mismatch_paths": mismatches,
    }
    if args.compare_root:
        report["comparison"] = compare_output_trees(args.compare_root, root)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if not missing and not extra and not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
