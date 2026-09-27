"""Non-destructive validation for PixelFlow outputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    code: str
    path: Path
    message: str


# Legacy names remain listed so an old/stale output is reported rather than
# silently accepted after the confirmed directory rules changed.
OUTPUT_CATEGORY_NAMES = {"产品主图", "产品细节图", "模特图", "模特细节图"}


def audit_output_tree(
    output_root: Path,
    generated_paths: set[Path],
    source_records: dict[Path, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Run batch-level checks after rendering without requiring manual review."""
    issues: list[dict[str, str]] = []
    seen: set[Path] = set()
    expected_by_type = {
        "main_product": "产品主图",
        "detail": "产品主图",
        "model": "模特图",
        "model_detail": "模特图",
    }
    for source, record in (source_records or {}).items():
        raw_type = str(record.get("classification_type", ""))
        expected = expected_by_type.get(raw_type)
        raw_paths = record.get("paths", [])
        raw_paths = raw_paths if isinstance(raw_paths, list) else []
        expected_count = sum(
            int(record.get(key, 0) or 0)
            for key in ("main_images", "transparent_images", "vip_images", "selling_point_images")
        )
        if expected_count != len(raw_paths) and not record.get("excluded"):
            issues.append(
                {
                    "code": "output_count_mismatch",
                    "path": str(source),
                    "message": f"输出数量记录为 {expected_count}，实际记录 {len(raw_paths)}",
                }
            )
        for raw_path in raw_paths:
            path = raw_path if isinstance(raw_path, Path) else Path(str(raw_path))
            if not path.is_absolute():
                path = output_root / path
            channel_output = any(
                name in path.parts for name in ("透明产品图", "唯品专享1440", "卖点图")
            )
            if path in seen:
                issues.append({"code": "duplicate_output", "path": str(path), "message": "同一输出路径被多个素材重复记录"})
            seen.add(path)
            if expected and not channel_output and expected not in path.parts:
                issues.append({"code": "route_mismatch", "path": str(path), "message": f"最终分类 {raw_type} 未输出到 {expected}"})
            if raw_type in {"model", "model_detail"} and not channel_output and expected in path.parts:
                try:
                    model_index = path.parts.index("模特图")
                    if len(path.parts) > model_index + 1 and path.parts[model_index + 1] not in {"1440x1440", "1440x1920", "1125x1500"}:
                        issues.append({"code": "model_grouped_output", "path": str(path), "message": "模特图不应按颜色或原始子目录分组"})
                except ValueError:
                    pass
            if not path.is_file():
                issues.append({"code": "missing_output", "path": str(path), "message": "manifest 记录的输出文件不存在"})
        if record.get("paths") == [] and not record.get("excluded"):
            issues.append({"code": "missing_source_output", "path": str(source), "message": "输入素材没有生成任何输出"})
    for path in generated_paths:
        categories = OUTPUT_CATEGORY_NAMES.intersection(path.parts)
        if len(categories) > 1:
            issues.append({"code": "ambiguous_output_route", "path": str(path), "message": "输出路径包含多个分类目录"})
    return {
        "passed": not issues,
        "issues": issues,
        "checked_outputs": len(generated_paths),
        "anomaly_count": len(issues),
    }


def validate_output(
    path: Path,
    expected_size: tuple[int, int],
    expected_mode: str,
    max_size_mb: float | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        with Image.open(path) as image:
            image.load()
            if image.size != expected_size:
                issues.append(
                    ValidationIssue("failed", "invalid_dimensions", path, "输出像素尺寸不正确")
                )
            if image.mode != expected_mode:
                issues.append(
                    ValidationIssue("failed", "invalid_mode", path, f"输出模式应为 {expected_mode}")
                )
            if expected_mode == "RGBA":
                corners = (
                    image.getpixel((0, 0))[3],
                    image.getpixel((image.width - 1, 0))[3],
                    image.getpixel((0, image.height - 1))[3],
                    image.getpixel((image.width - 1, image.height - 1))[3],
                )
                if any(alpha != 0 for alpha in corners):
                    issues.append(
                        ValidationIssue("warning", "opaque_transparent_corner", path, "透明图四角不是完全透明")
                    )
    except Exception as error:  # noqa: BLE001 - validation must report corrupt output
        issues.append(ValidationIssue("failed", "invalid_image", path, f"无法读取输出：{error}"))
        return issues

    if max_size_mb is not None and path.stat().st_size > max_size_mb * 1024 * 1024:
        issues.append(ValidationIssue("warning", "file_size_exceeded", path, "文件大小超过设定上限，已保留目标像素尺寸"))
    return issues
