"""Typed v3 façade for the existing v2 shoe composition rules.

The v2 assistant still owns the pose heuristics during the migration.  This
module keeps its result stable at the v3 boundary and makes per-size shoe
risk visible to callers that only inspect top-level metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from collections.abc import Mapping
from typing import Union

from .contracts import CompositionPlan, ProtectedRegion
from .model_cache import COMPOSITION_RULES_VERSION


BBox = tuple[float, float, float, float]
TargetSize = tuple[int, int]
SizeInput = Union[TargetSize, tuple[TargetSize, object]]

SHOE_REVIEW_HINT = "鞋部姿态复杂，已使用保守构图，请人工确认"
MULTIPLE_PEOPLE_HINT = "双人/多人鞋类构图按当前范围跳过，不计入通过数量"


@dataclass(frozen=True)
class PoseDetection:
    """Minimal pose evidence accepted by the v3 shoe façade."""

    bbox: BBox
    confidence: float
    keypoints: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True)
class ShoeCompositionPlan:
    """One output-size shoe composition plan."""

    composition_mode: str = "zoom_focus"
    scale_adjustment_percent: float = 0.0
    position_x: float = 0.5
    position_y: float = 0.5
    confidence: float = 0.0
    target_crop_height_ratio: float | None = None
    protected_crop_bbox: BBox | None = None
    shoe_risk: bool = False
    shoe_risk_reasons: tuple[str, ...] = ()
    review_hint: str | None = None
    fallback_reason: str | None = None
    skipped_by_scope: bool = False


@dataclass(frozen=True)
class ShoeCompositionResult:
    """Stable shoe composition data with risk flattened across sizes."""

    by_size: dict[str, ShoeCompositionPlan]
    shoe_risk: bool = False
    shoe_risk_reasons: tuple[str, ...] = ()
    review_hint: str | None = None
    model_view: str | None = None
    confidence: float = 0.0
    person_count: int = 0
    person_bboxes: tuple[BBox, ...] = ()
    composition_plans: dict[str, CompositionPlan] = field(default_factory=dict)
    fallback: bool = False
    fallback_reason: str | None = None
    skipped_by_scope: bool = False

    @property
    def plans(self) -> dict[str, CompositionPlan]:
        """Alias used by callers that only need the generic v3 plans."""
        return self.composition_plans


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bbox(value: object) -> BBox | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        return tuple(float(item) for item in value[:4])  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _as_reasons(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _target_size(value: SizeInput) -> TargetSize:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        first, second = value
        if isinstance(first, int) and isinstance(second, int):
            return first, second
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            return int(first[0]), int(first[1])
    raise ValueError(f"invalid target size: {value!r}")


def _plan_from_mapping(value: object) -> ShoeCompositionPlan:
    if not isinstance(value, Mapping):
        return ShoeCompositionPlan()
    reasons = _as_reasons(value.get("shoe_risk_reasons"))
    risk = bool(value.get("shoe_risk")) or bool(reasons)
    hint = value.get("review_hint")
    review_hint = str(hint) if hint else (SHOE_REVIEW_HINT if risk else None)
    return ShoeCompositionPlan(
        composition_mode=str(value.get("composition_mode", "zoom_focus")),
        scale_adjustment_percent=_as_float(value.get("scale_adjustment_percent")),
        position_x=_as_float(value.get("position_x"), 0.5),
        position_y=_as_float(value.get("position_y"), 0.5),
        confidence=_as_float(value.get("confidence")),
        target_crop_height_ratio=(
            _as_float(value.get("target_crop_height_ratio"))
            if value.get("target_crop_height_ratio") is not None
            else None
        ),
        protected_crop_bbox=_as_bbox(value.get("protected_crop_bbox")),
        shoe_risk=risk,
        shoe_risk_reasons=reasons,
        review_hint=review_hint,
        fallback_reason=(str(value.get("fallback_reason")) if value.get("fallback_reason") else None),
        skipped_by_scope=bool(value.get("skipped_by_scope")),
    )


def _reliable_shoe_bbox(value: object) -> BBox | None:
    """Accept only explicit shoe contour evidence from Seg.

    Generic ``mask_bbox_ratio`` data describes a foreground/person mask and
    must never become a shoe protected region by itself.
    """
    if not isinstance(value, Mapping):
        return None
    direct = _as_bbox(value.get("reliable_shoe_bbox"))
    if direct is not None:
        return direct
    contour = value.get("reliable_shoe_contour")
    if isinstance(contour, Mapping):
        if contour.get("reliable") is True:
            return _as_bbox(contour.get("bbox"))
    if value.get("shoe_contour_reliable") is True:
        return _as_bbox(value.get("shoe_bbox") or value.get("shoe_region"))
    return None


def _typed_plan(
    size_name: str,
    plan: ShoeCompositionPlan,
    *,
    source: str = "shoe_composition",
    model_view: str | None,
    protected_source: str = "pose",
    segmentation_evidence: Mapping[str, object] | None = None,
    fallback_reason: str | None = None,
    skipped_by_scope: bool = False,
) -> CompositionPlan:
    protected_regions: tuple[ProtectedRegion, ...] = ()
    if plan.protected_crop_bbox is not None:
        try:
            protected_regions = (
                ProtectedRegion(
                    kind="shoe",
                    bbox=plan.protected_crop_bbox,
                    source=protected_source,
                    required=True,
                    confidence=plan.confidence,
                ),
            )
        except ValueError:
            protected_regions = ()
    evidence: dict[str, object] = {
        "composition_rules_version": COMPOSITION_RULES_VERSION,
        "model_view": model_view,
        "shoe_risk": plan.shoe_risk,
        "shoe_risk_reasons": list(plan.shoe_risk_reasons),
        "review_hint": plan.review_hint,
        "protected_region_source": protected_source,
        "fallback_reason": plan.fallback_reason or fallback_reason,
        "skipped_by_scope": bool(plan.skipped_by_scope or skipped_by_scope),
    }
    if segmentation_evidence is not None:
        evidence["segmentation"] = dict(segmentation_evidence)
    return CompositionPlan(
        source=str(source or "shoe_composition"),
        size=size_name,
        composition_mode=plan.composition_mode,
        scale=max(0.01, 1.0 + plan.scale_adjustment_percent / 100.0),
        position_x=plan.position_x,
        position_y=plan.position_y,
        crop_ratio=plan.target_crop_height_ratio,
        protected_regions=protected_regions,
        risk_flags=plan.shoe_risk_reasons,
        requires_review=bool(plan.shoe_risk or plan.review_hint),
        review_hint=plan.review_hint or "",
        fallback_reason=plan.fallback_reason or fallback_reason or "",
        skipped_by_scope=bool(plan.skipped_by_scope or skipped_by_scope),
        evidence=evidence,
        fallback=bool(plan.composition_mode == "fallback" or plan.fallback_reason or fallback_reason),
    )


def _pose_shoe_risks(detection: PoseDetection) -> tuple[str, ...]:
    """Add only cheap geometry risks missing from the legacy composition."""
    points = detection.keypoints
    visible = lambda index: len(points) > index and points[index][2] >= 0.28
    reasons: list[str] = []
    feet = [index for index in (15, 16) if visible(index)]
    if len(feet) < 2:
        reasons.append("单脚或脚部遮挡")
    if all(visible(index) for index in (11, 12, 15, 16)):
        hip_delta = points[11][0] - points[12][0]
        ankle_delta = points[15][0] - points[16][0]
        if hip_delta * ankle_delta < 0:
            reasons.append("交叉腿")
    for knee, ankle in ((13, 15), (14, 16)):
        if visible(knee) and visible(ankle):
            # A foot above its knee is a reliable low-cost signal for a
            # foreshortened sole-facing pose; ordinary standing keeps ankles
            # below knees in image coordinates.
            if points[ankle][1] + abs(points[ankle][1] - points[knee][1]) * 0.05 < points[knee][1]:
                reasons.append("鞋底朝镜头")
                break
    return tuple(dict.fromkeys(reasons))


def _conservative_raw_plans(
    raw_by_size: Mapping[str, object],
    sizes: Mapping[str, SizeInput],
    reasons: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    if not reasons:
        return {
            str(name): dict(value) if isinstance(value, Mapping) else {}
            for name, value in raw_by_size.items()
        }
    updated: dict[str, dict[str, object]] = {}
    for name, value in raw_by_size.items():
        plan = dict(value) if isinstance(value, Mapping) else {}
        merged = list(_as_reasons(plan.get("shoe_risk_reasons")))
        for reason in reasons:
            if reason not in merged:
                merged.append(reason)
        plan["shoe_risk"] = True
        plan["shoe_risk_reasons"] = merged
        plan["review_hint"] = plan.get("review_hint") or SHOE_REVIEW_HINT
        try:
            target = _target_size(sizes[str(name)])
            minimum_ratio = 0.82 if target[0] == target[1] else 0.88
            current_ratio = _as_float(plan.get("target_crop_height_ratio"), 0.0)
            if current_ratio <= 0.0 or current_ratio < minimum_ratio:
                plan["target_crop_height_ratio"] = minimum_ratio
            current_scale = _as_float(plan.get("scale_adjustment_percent"), 0.0)
            # Cap aggressive zoom while keeping the established rule path.
            plan["scale_adjustment_percent"] = min(current_scale, 20.0)
        except (TypeError, ValueError):
            pass
        updated[str(name)] = plan
    return updated


def normalize_shoe_composition(
    raw: Mapping[str, object] | None,
    *,
    source: str | None = None,
    segmentation_evidence: Mapping[str, object] | None = None,
) -> ShoeCompositionResult:
    """Normalize a v2 composition dictionary into auditable v3 plans."""
    if not isinstance(raw, Mapping):
        return ShoeCompositionResult(by_size={})
    raw_by_size = raw.get("by_size")
    if isinstance(raw_by_size, Mapping):
        by_size = {
            str(size_name): _plan_from_mapping(plan)
            for size_name, plan in raw_by_size.items()
            if isinstance(size_name, str)
        }
    else:
        by_size = {"default": _plan_from_mapping(raw)}

    reliable_bbox = _reliable_shoe_bbox(
        segmentation_evidence
        if segmentation_evidence is not None
        else raw.get("segmentation")
    )
    protected_source = "segmentation" if reliable_bbox is not None else "pose"
    if reliable_bbox is not None:
        by_size = {
            name: replace(plan, protected_crop_bbox=reliable_bbox)
            for name, plan in by_size.items()
        }

    reasons: list[str] = []
    for reason in _as_reasons(raw.get("shoe_risk_reasons")):
        if reason not in reasons:
            reasons.append(reason)
    review_hint = str(raw["review_hint"]) if raw.get("review_hint") else None
    for plan in by_size.values():
        for reason in plan.shoe_risk_reasons:
            if reason not in reasons:
                reasons.append(reason)
        if review_hint is None and plan.review_hint:
            review_hint = plan.review_hint
    person_count = int(_as_float(raw.get("person_count")))
    skipped_by_scope = bool(raw.get("skipped_by_scope")) or person_count > 1
    if person_count > 1 and "双人/多人" not in reasons:
        reasons.append("双人/多人")
    if skipped_by_scope and review_hint is None:
        review_hint = MULTIPLE_PEOPLE_HINT
    risk = bool(raw.get("shoe_risk")) or bool(reasons) or any(
        plan.shoe_risk for plan in by_size.values()
    )
    if risk and review_hint is None:
        review_hint = SHOE_REVIEW_HINT
    if reasons or skipped_by_scope:
        reason_tuple = tuple(reasons)
        by_size = {
            name: replace(
                plan,
                shoe_risk=bool(plan.shoe_risk or reasons),
                shoe_risk_reasons=tuple(dict.fromkeys((*plan.shoe_risk_reasons, *reason_tuple))),
                review_hint=plan.review_hint or review_hint,
                skipped_by_scope=bool(plan.skipped_by_scope or skipped_by_scope),
            )
            for name, plan in by_size.items()
        }
    raw_bboxes = raw.get("person_bboxes")
    person_bboxes = tuple(
        bbox
        for item in raw_bboxes
        if (bbox := _as_bbox(item)) is not None
    ) if isinstance(raw_bboxes, (list, tuple)) else ()
    composition_plans = {
        size_name: _typed_plan(
            size_name,
            plan,
            source=str(source or "shoe_composition"),
            model_view=str(raw["model_view"]) if raw.get("model_view") else None,
            protected_source=protected_source,
            segmentation_evidence=segmentation_evidence,
            fallback_reason=(str(raw["fallback_reason"]) if raw.get("fallback_reason") else None),
            skipped_by_scope=skipped_by_scope,
        )
        for size_name, plan in by_size.items()
    }
    fallback_reason = str(raw["fallback_reason"]) if raw.get("fallback_reason") else next(
        (plan.fallback_reason for plan in by_size.values() if plan.fallback_reason),
        None,
    )
    fallback = bool(raw.get("fallback")) or bool(fallback_reason) or any(
        plan.fallback_reason for plan in by_size.values()
    )
    return ShoeCompositionResult(
        by_size=by_size,
        shoe_risk=risk,
        shoe_risk_reasons=tuple(reasons),
        review_hint=review_hint,
        model_view=str(raw["model_view"]) if raw.get("model_view") else None,
        confidence=_as_float(raw.get("confidence")),
        person_count=person_count,
        person_bboxes=person_bboxes,
        composition_plans=composition_plans,
        fallback=fallback,
        fallback_reason=fallback_reason,
        skipped_by_scope=skipped_by_scope,
    )


def _load_model_types(_model_root: Path | None):
    from .model_assistant import LocalModelAssistant, PersonDetection

    return LocalModelAssistant, PersonDetection


def compose_shoe_composition(
    detection: PoseDetection,
    image_size: tuple[int, int],
    sizes: Mapping[str, SizeInput],
    *,
    model_view: str = "lower_body",
    protected_region: BBox | None = None,
    v2_root: Path | None = None,
) -> ShoeCompositionResult:
    """Reuse v2 ``_composition`` and return a typed, flattened v3 result."""
    assistant_type, detection_type = _load_model_types(v2_root)
    assistant = assistant_type.__new__(assistant_type)
    v2_detection = detection_type(
        tuple(float(value) for value in detection.bbox),
        float(detection.confidence),
        tuple(tuple(float(value) for value in point) for point in detection.keypoints),
    )
    raw_by_size: dict[str, dict[str, object]] = {}
    for size_name, size in sizes.items():
        raw_by_size[str(size_name)] = assistant_type._composition(
            assistant,
            v2_detection,
            image_size,
            _target_size(size),
            model_view,
            "shoes",
            protected_region,
        )
    raw_by_size = _conservative_raw_plans(
        raw_by_size,
        sizes,
        _pose_shoe_risks(detection),
    )
    return normalize_shoe_composition(
        {
            "by_size": raw_by_size,
            "model_view": model_view,
            "confidence": detection.confidence,
            "person_count": 1,
            "person_bboxes": [list(detection.bbox)],
        }
    )


__all__ = [
    "BBox",
    "PoseDetection",
    "ShoeCompositionPlan",
    "ShoeCompositionResult",
    "compose_shoe_composition",
    "normalize_shoe_composition",
]
