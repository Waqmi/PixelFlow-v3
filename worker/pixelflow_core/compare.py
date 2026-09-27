"""Small, read-only output tree comparison helpers for compare mode.

The comparison code intentionally depends on Pillow only.  It is used by the
Worker at runtime and by the baseline scripts, so reports stay reproducible
without NumPy or a separate image-diff dependency.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


_IGNORED_TREE_PARTS = {"__pycache__"}
_IGNORED_TREE_SUFFIXES = {".pyc", ".pyo"}
ACCEPTABLE_MAX_DIFF = 2
ACCEPTABLE_MAE = 0.25
ACCEPTABLE_PSNR = 50.0


def _file_metadata(root: Path, path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    file_sha256 = hashlib.sha256(payload).hexdigest()
    item: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "sha256": file_sha256,
        "file_sha256": file_sha256,
        "pixel_sha256": None,
        "rgba_pixel_sha256": None,
        "bytes": len(payload),
        "size": None,
        "mode": None,
    }
    try:
        with Image.open(path) as image:
            image.load()
            rgba_digest = hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()
            item["pixel_sha256"] = rgba_digest
            item["rgba_pixel_sha256"] = rgba_digest
            item["size"] = [int(image.width), int(image.height)]
            item["mode"] = image.mode
    except (OSError, ValueError):
        # Output trees may contain manifests or other non-image files.  Their
        # byte hash is still useful; image-only fields remain null.
        pass
    return item


def _tree_manifest(root: str | Path) -> dict[str, dict[str, Any]]:
    base = Path(root)
    if not base.is_dir():
        return {}
    return {
        path.relative_to(base).as_posix(): _file_metadata(base, path)
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


def tree_manifest(root: str | Path) -> list[dict[str, Any]]:
    """Return a stable, JSON-ready manifest for files below ``root``."""
    manifest = _tree_manifest(root)
    return [manifest[key] for key in sorted(manifest)]


def aggregate_tree_sha256(root: str | Path) -> str:
    """Hash a source tree in path order, excluding Python cache files.

    The relative path is included before each file payload so renaming a
    source file changes the digest just like changing its contents does.
    """
    base = Path(root)
    digest = hashlib.sha256()
    if not base.is_dir():
        return digest.hexdigest()
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part in _IGNORED_TREE_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in _IGNORED_TREE_SUFFIXES:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def source_tree_sha256(root: str | Path) -> str:
    """Compatibility alias used by baseline evidence scripts."""
    return aggregate_tree_sha256(root)


def image_diff_metrics(left: str | Path, right: str | Path) -> dict[str, Any] | None:
    """Return channel-level diff metrics for two readable images.

    Images are compared as RGBA.  A size mismatch is reported explicitly and
    does not attempt a resize, since resizing would hide a renderer defect.
    """
    try:
        with Image.open(left) as left_image, Image.open(right) as right_image:
            left_image.load()
            right_image.load()
            if left_image.size != right_image.size:
                return {
                    "compatible": False,
                    "reason": "size_mismatch",
                    "left_size": [int(value) for value in left_image.size],
                    "right_size": [int(value) for value in right_image.size],
                    "max_diff": None,
                    "mae": None,
                    "psnr": None,
                    "diff_bbox": None,
                }
            left_rgba = left_image.convert("RGBA")
            right_rgba = right_image.convert("RGBA")
    except (OSError, ValueError):
        return None

    difference = ImageChops.difference(left_rgba, right_rgba)
    stats = ImageStat.Stat(difference)
    maxima = [float(item[1]) for item in stats.extrema]
    means = [float(value) for value in stats.mean]
    rms_values = [float(value) for value in stats.rms]
    max_diff = int(max(maxima, default=0.0))
    mae = sum(means) / len(means) if means else 0.0
    mse = sum(value * value for value in rms_values) / len(rms_values) if rms_values else 0.0
    psnr_is_infinite = mse == 0
    psnr = None if psnr_is_infinite else 20.0 * math.log10(255.0 / math.sqrt(mse))
    boxes = [difference.getchannel(name).getbbox() for name in difference.getbands()]
    boxes = [box for box in boxes if box]
    bbox = None
    if boxes:
        bbox = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
    return {
        "compatible": True,
        "pixels": int(left_rgba.width * left_rgba.height),
        "channels": 4,
        "max_diff": max_diff,
        "mae": mae,
        "psnr": psnr,
        "psnr_is_infinite": psnr_is_infinite,
        "diff_bbox": list(bbox) if bbox else None,
    }


def _metric_summary(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [item for item in metrics if item.get("compatible")]
    if not comparable:
        return {
            "files": 0,
            "different_files": 0,
            "max_diff": None,
            "mae": None,
            "psnr": None,
            "psnr_is_infinite": False,
            "diff_bbox": None,
        }
    total_pixels = sum(int(item.get("pixels", 0) or 0) for item in comparable)
    weighted_mae = sum(
        float(item.get("mae", 0.0) or 0.0) * int(item.get("pixels", 0) or 0)
        for item in comparable
    )
    weighted_mse = sum(
        (255.0 / (10 ** (float(item["psnr"]) / 20.0))) ** 2 * int(item.get("pixels", 0) or 0)
        for item in comparable
        if item.get("psnr") not in (None, math.inf)
    )
    # Equal images have zero MSE and therefore infinite PSNR.  If every image
    # is equal, preserve that exact value instead of returning a rounded number.
    has_nonzero = any(float(item.get("mae", 0.0) or 0.0) > 0 for item in comparable)
    aggregate_psnr_is_infinite = not has_nonzero
    aggregate_psnr = (
        None
        if aggregate_psnr_is_infinite
        else 20.0 * math.log10(255.0 / math.sqrt(weighted_mse / max(1, total_pixels)))
    )
    boxes = [item.get("diff_bbox") for item in comparable if item.get("diff_bbox")]
    bbox = None
    if boxes:
        bbox = [
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ]
    return {
        "files": len(comparable),
        "different_files": sum(1 for item in comparable if item.get("diff_bbox")),
        "max_diff": max(int(item.get("max_diff", 0) or 0) for item in comparable),
        "mae": weighted_mae / max(1, total_pixels),
        "psnr": aggregate_psnr,
        "psnr_is_infinite": aggregate_psnr_is_infinite,
        "diff_bbox": bbox,
    }


def _difference_classification(status: str, metrics: dict[str, Any] | None) -> str:
    """Apply the documented first-pass classification thresholds."""
    if status != "different":
        return "needs_fix"
    if metrics is None:
        # Non-image payloads cannot be assigned a pixel tolerance.  Keep them
        # visible as version differences for a human to classify further.
        return "version_difference"
    if not metrics.get("compatible"):
        return "needs_fix"
    psnr = metrics.get("psnr")
    if (
        int(metrics.get("max_diff", 0) or 0) <= ACCEPTABLE_MAX_DIFF
        and float(metrics.get("mae", 0.0) or 0.0) <= ACCEPTABLE_MAE
        and (
            bool(metrics.get("psnr_is_infinite"))
            or (psnr is not None and float(psnr) >= ACCEPTABLE_PSNR)
        )
    ):
        return "acceptable_implementation_difference"
    return "needs_fix"


def _output_kind(relative_path: str) -> str:
    parts = set(Path(relative_path).parts)
    if "透明产品图" in parts:
        return "transparent"
    if "唯品专享1440" in parts:
        return "vip"
    if "模特图" in parts:
        return "model"
    return "product_main"


def compare_output_trees(legacy_root: str | Path, v3_root: str | Path) -> dict[str, Any]:
    """Return file-level differences without writing either output tree."""
    legacy = _tree_manifest(legacy_root)
    v3 = _tree_manifest(v3_root)
    differences: list[dict[str, Any]] = []
    pixel_metrics: list[dict[str, Any]] = []
    compared = 0
    for relative_path in sorted(set(legacy) | set(v3)):
        left = legacy.get(relative_path)
        right = v3.get(relative_path)
        if left is None:
            differences.append({
                "path": relative_path,
                "status": "missing_in_legacy",
                "legacy": None,
                "v3": right,
                "differences": ["missing_in_legacy"],
                "classification": "needs_fix",
            })
            continue
        if right is None:
            differences.append({
                "path": relative_path,
                "status": "missing_in_v3",
                "legacy": left,
                "v3": None,
                "differences": ["missing_in_v3"],
                "classification": "needs_fix",
            })
            continue
        compared += 1
        fields = (
            "size",
            "mode",
            "bytes",
            "sha256",
            "pixel_sha256",
        )
        changed = [field for field in fields if left.get(field) != right.get(field)]
        metrics = None
        # Resolve image paths only when both metadata entries describe images.
        legacy_path = Path(legacy_root) / relative_path
        v3_path = Path(v3_root) / relative_path
        if left.get("pixel_sha256") is not None and right.get("pixel_sha256") is not None:
            metrics = image_diff_metrics(legacy_path, v3_path)
            if metrics is not None:
                pixel_metrics.append(metrics)
        if changed:
            item = {
                "path": relative_path,
                "status": "different",
                "legacy": left,
                "v3": right,
                "differences": changed,
            }
            if metrics is not None:
                item["pixel_metrics"] = metrics
                item["max_diff"] = metrics.get("max_diff")
                item["mae"] = metrics.get("mae")
                item["psnr"] = metrics.get("psnr")
                item["diff_bbox"] = metrics.get("diff_bbox")
            item["classification"] = _difference_classification("different", metrics)
            differences.append(item)
    kinds = {
        name: {
            "legacy_files": 0,
            "v3_files": 0,
            "compared_files": 0,
            "different_files": 0,
            "missing_in_legacy": 0,
            "missing_in_v3": 0,
        }
        for name in ("product_main", "model", "transparent", "vip")
    }
    for relative_path in set(legacy) | set(v3):
        summary = kinds[_output_kind(relative_path)]
        if relative_path in legacy:
            summary["legacy_files"] += 1
        if relative_path in v3:
            summary["v3_files"] += 1
        if relative_path in legacy and relative_path in v3:
            summary["compared_files"] += 1
        elif relative_path in legacy:
            summary["missing_in_v3"] += 1
        else:
            summary["missing_in_legacy"] += 1
    for item in differences:
        if item["status"] == "different":
            kinds[_output_kind(item["path"])]["different_files"] += 1
    classification_summary = {
        "version_difference": 0,
        "acceptable_implementation_difference": 0,
        "needs_fix": 0,
    }
    for item in differences:
        category = item.get("classification")
        if category in classification_summary:
            classification_summary[category] += 1
    return {
        "legacy_files": len(legacy),
        "v3_files": len(v3),
        "compared_files": compared,
        "file_differences": differences,
        "output_kind_summary": kinds,
        "pixel_metrics": _metric_summary(pixel_metrics),
        "classification_summary": classification_summary,
    }


__all__ = [
    "aggregate_tree_sha256",
    "ACCEPTABLE_MAE",
    "ACCEPTABLE_MAX_DIFF",
    "ACCEPTABLE_PSNR",
    "compare_output_trees",
    "image_diff_metrics",
    "source_tree_sha256",
    "tree_manifest",
]
