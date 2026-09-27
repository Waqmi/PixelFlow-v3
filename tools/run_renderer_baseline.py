#!/usr/bin/env python3
"""Generate the fixed 16-input baseline in legacy, v3, or compare mode."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from pixelflow_core.compare import aggregate_tree_sha256, compare_output_trees, tree_manifest
from pixelflow_core.engine import (
    GenerationRequest,
    ImageEngine,
    V26EngineAdapter,
    V3EngineAdapter,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--logo-root", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--compare-root", type=Path, help="compare generated output against this tree")
    parser.add_argument("--mode", choices=("legacy", "v3", "compare"), default="v3")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output_root = args.output.resolve()
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    product_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
    fixture = json.loads((root / "fixtures/manifest.json").read_text(encoding="utf-8"))
    logos = {}
    if args.logo_root:
        for key, name in {
            "logo": "新logo 1440.png",
            "white_logo": "新logo 1440 2.png",
            "tall_logo": "新logo 1920.png",
            "tall_white_logo": "新logo 1920 2.png",
        }.items():
            path = args.logo_root / name
            if path.is_file():
                logos[key] = str(path)
    # Stage the fixed fixture set so legacy and v3 see the same complete input
    # tree.  Passing source_filter against the full product folder makes the
    # legacy short-name allocator count unselected files and produces paths
    # that cannot be compared with v3.
    with tempfile.TemporaryDirectory(prefix="pixelflow-baseline-input-") as staging:
        staged_root = Path(staging)
        for item in fixture["fixtures"]:
            source = product_root / item["relative_path"]
            destination = staged_root / item["relative_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        request = GenerationRequest(
            staged_root,
            output_root,
            ("1440x1440", "1440x1920", "1125x1500"),
            include_logo=True,
            include_vip=True,
            include_model_images=False,
            category_override="long_pants",
            enable_category_template=True,
            logo_overrides=logos,
            mode=args.mode,
        )
        if args.mode == "legacy":
            result = V26EngineAdapter().run(request)
        elif args.mode == "compare":
            result = ImageEngine(V26EngineAdapter(), V3EngineAdapter()).run(request)
        else:
            result = V3EngineAdapter().run(request)
    legacy_root = root / "worker" / "pixelflow_core" / "legacy_engine"
    legacy_engine_sha256 = aggregate_tree_sha256(legacy_root)
    report = {
        "mode": args.mode,
        "sources": result.get("sources", 0),
        "main_images": result.get("main_images", 0),
        "transparent_images": result.get("transparent_images", 0),
        "vip_images": result.get("vip_images", 0),
        "failures": result.get("failures", []),
        "typed_renderer": result.get("typed_renderer", {}),
        "output_root": str(output_root),
        "legacy_engine": {
            "path": str(legacy_root),
            "sha256": legacy_engine_sha256,
        },
        "legacy_engine_sha256": legacy_engine_sha256,
        "output_manifest": tree_manifest(output_root),
    }
    if args.compare_root:
        report["comparison"] = compare_output_trees(args.compare_root.resolve(), output_root)
    if "comparison" in result:
        report["comparison"] = result["comparison"]
    report_path = args.report or output_root.with_name(output_root.name + "-report.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
