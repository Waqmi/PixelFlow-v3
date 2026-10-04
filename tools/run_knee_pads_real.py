#!/usr/bin/env python3
"""Run the v3 renderer against the checked-out real knee-pad fixture.

Generated images stay in a temporary directory.  The committed JSON contains
only paths, counts and renderer evidence so large source/output files are not
added to Git.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

from pixelflow_core.engine import GenerationRequest, V3EngineAdapter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT.parent / "05_测试素材与输出" / "护膝")
    parser.add_argument("--model-root", type=Path, default=ROOT / "models")
    parser.add_argument("--report", type=Path, default=ROOT / "baselines" / "v3" / "knee-pads-real-20261003.json")
    args = parser.parse_args()
    source_root = args.input.expanduser().resolve()
    model_root = args.model_root.expanduser().resolve()
    report: dict[str, object] = {
        "schema_version": "v3-knee-pads-real-1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture": str(source_root),
        "category": "knee_pads",
        "model_root": str(model_root),
        "status": "blocked",
        "inputs": [],
        "limitations": [],
    }
    if not source_root.is_dir():
        report["limitations"] = [f"fixture directory is unavailable: {source_root}"]
    else:
        source_paths = sorted(
            path for path in source_root.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
        report["inputs"] = [str(path) for path in source_paths]
        report["fixture_input_count"] = len(source_paths)
        supported_paths = [path for path in source_paths if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
        report["renderer_supported_inputs"] = [str(path) for path in supported_paths]
        report["excluded_inputs"] = [
            {"path": str(path), "reason": "unsupported image extension"}
            for path in source_paths if path not in supported_paths
        ]
        if not source_paths:
            report["limitations"] = ["fixture directory has no supported image files"]
        else:
            local_model_enabled = all(
                (model_root / relative).is_file()
                for relative in ("pose/yolo11n-pose.onnx", "segmentation/yolo11n-seg.onnx")
            )
            report["model_probe_prerequisites"] = {
                "pose": (model_root / "pose/yolo11n-pose.onnx").is_file(),
                "segmentation": (model_root / "segmentation/yolo11n-seg.onnx").is_file(),
                "detection": (model_root / "detection/yolov8m-worldv2-pixelflow.onnx").is_file(),
            }
            try:
                with tempfile.TemporaryDirectory(prefix="pixelflow-knee-pads-") as output_dir:
                    request = GenerationRequest(
                        product_root=source_root,
                        output_root=Path(output_dir),
                        selected_sizes=("1440x1440", "1440x1920", "1125x1500"),
                        include_logo=False,
                        include_vip=False,
                        include_model_images=True,
                        category_override="knee_pads",
                        enable_category_template=True,
                        enable_material_understanding=True,
                        local_model_enabled=local_model_enabled,
                        local_model_path=str(model_root) if local_model_enabled else None,
                        force_rerender=True,
                        mode="v3",
                    )
                    result = V3EngineAdapter().run(request)
                    report["result"] = {
                        key: result.get(key)
                        for key in (
                            "mode", "renderer", "fallback", "sources", "main_images", "model_images",
                            "transparent_images", "vip_images", "failures", "requires_review",
                            "fallback_items", "validation", "audit", "typed_renderer",
                        )
                        if key in result
                    }
                    report["renderer_source_count"] = result.get("sources", 0)
                    report["status"] = "passed" if not result.get("failures") else "failed"
                    limitations = []
                    if report["excluded_inputs"]:
                        limitations.append("some fixture files were recorded but excluded by the current v3 source collector")
                    if not local_model_enabled:
                        limitations.append("Pose/Seg ONNX prerequisites unavailable; run used the documented rules fallback")
                    report["limitations"] = limitations
            except Exception as error:  # noqa: BLE001 - preserve exact environment evidence
                report["status"] = "blocked"
                report["limitations"] = [f"renderer execution failed: {type(error).__name__}: {error}"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.report), "status": report["status"], "inputs": len(report["inputs"])}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
