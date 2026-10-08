from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Event
from typing import Callable

from .contracts import CompositionPlan, ProtectedRegion


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
GENERATED_OUTPUT_MARKERS = {"唯品专享1440", "卖点图", "1440x1440", "1440x1920", "1125x1500"}
ClassificationProgress = Callable[[int, int, Path, "ClassificationDecision"], None]
ClassificationStageProgress = Callable[[str, int, int, Path], None]
ClassificationModelProgress = Callable[[str, int, int, str, str | None, str | None], None]
ClassificationModelTrace = Callable[[str, str, str, str, str | None], None]
SIZES = {
    "1440x1440": ((1440, 1440), (255, 255, 255)),
    "1440x1920": ((1440, 1920), (255, 255, 255)),
    "1125x1500": ((1125, 1500), (255, 255, 255)),
}


# Detector category used for each anchor category. The old set name remains as
# a compatibility alias for callers that only need the shoe-backed categories.
DETECTION_CATEGORY_MAP = {
    "shoes": "shoes",
    "long_pants": "shoes",
    "pants": "shoes",
    "shorts": "shoes",
    "socks": "shoes",
    "knee_pads": "shoes",
    "hat": "hat",
    "gloves": "gloves",
}
# Public spelling used by integrations that need to inspect the routing table.
DETECTION_CATEGORIES = DETECTION_CATEGORY_MAP
SHOE_DETECTION_CATEGORIES = frozenset(
    category for category, detector_category in DETECTION_CATEGORY_MAP.items()
    if detector_category == "shoes"
)


class ClassificationCancelled(Exception):
    pass


@dataclass(frozen=True)
class ClassificationRequest:
    """Qt-free material classification request."""

    product_root: Path
    source_filter: set[str] | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    workers: int = 1
    category: str | None = None
    local_model_enabled: bool = False
    local_model_path: str | None = None
    output_root: Path | None = None
    cancel_event: Event | None = None
    # WorkerRuntime supplies the already collected source list so preflight and
    # classification do not walk the input tree twice.
    sources: tuple[Path, ...] | None = field(default=None, repr=False, compare=False)
    stage_progress: ClassificationStageProgress | None = field(default=None, repr=False, compare=False)
    model_progress: ClassificationModelProgress | None = field(default=None, repr=False, compare=False)
    model_trace: ClassificationModelTrace | None = field(default=None, repr=False, compare=False)
    trace_request_id: str = field(default="", repr=False, compare=False)

    def validate(self) -> None:
        if not self.product_root.is_dir():
            raise ValueError(f"product_root is not a directory: {self.product_root}")


@dataclass(frozen=True)
class ClassificationDecision:
    source: str
    source_type: str
    confidence: float
    classifier: str
    reason: str
    metadata: dict[str, object] | None = None
    composition_plans: dict[str, CompositionPlan] = field(default_factory=dict)
    fallback: bool = False
    fallback_reason: str | None = None
    skipped_by_scope: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "composition_plans", dict(self.composition_plans))


def _typed_plan_from_mapping(source: Path, size: str, value: object) -> CompositionPlan | None:
    if isinstance(value, CompositionPlan):
        return value
    if not isinstance(value, dict):
        return None
    regions: list[ProtectedRegion] = []
    for item in value.get("protected_regions", ()):
        if isinstance(item, ProtectedRegion):
            regions.append(item)
        elif isinstance(item, dict):
            try:
                regions.append(ProtectedRegion(**item))
            except (TypeError, ValueError):
                continue
    if not regions and isinstance(value.get("protected_crop_bbox"), (list, tuple)):
        try:
            regions.append(
                ProtectedRegion(
                    kind="subject",
                    bbox=value["protected_crop_bbox"],
                    source="pose",
                    required=True,
                    confidence=(float(value["confidence"]) if value.get("confidence") is not None else None),
                )
            )
        except (TypeError, ValueError):
            pass
    scale = value.get("scale")
    if scale is None and value.get("scale_adjustment_percent") is not None:
        try:
            scale = 1.0 + float(value["scale_adjustment_percent"]) / 100.0
        except (TypeError, ValueError):
            scale = 1.0
    crop_ratio = value.get("crop_ratio")
    if crop_ratio is None:
        crop_ratio = value.get("target_crop_height_ratio")
    risk_flags = value.get("risk_flags")
    if risk_flags is None:
        risk_flags = value.get("shoe_risk_reasons", ())
    try:
        return CompositionPlan(
            source=str(value.get("source") or source),
            size=str(value.get("size") or size),
            composition_mode=str(value.get("composition_mode", "fallback")),
            scale=float(scale if scale is not None else 1.0),
            position_x=float(value.get("position_x", 0.5)),
            position_y=float(value.get("position_y", 0.5)),
            crop_ratio=crop_ratio,
            crop_box=value.get("crop_box"),
            protected_regions=tuple(regions),
            risk_flags=tuple(risk_flags or ()),
            requires_review=bool(value.get("requires_review", False) or value.get("review_hint") or value.get("shoe_risk")),
            review_hint=str(value.get("review_hint", "")),
            fallback_reason=str(value.get("fallback_reason", "")),
            skipped_by_scope=bool(value.get("skipped_by_scope", False)),
            evidence=dict(value.get("evidence", {})),
            fallback=bool(value.get("fallback", False)),
        )
    except (TypeError, ValueError):
        return None


def _typed_plans_from_metadata(source: Path, metadata: dict[str, object]) -> dict[str, CompositionPlan]:
    values = metadata.get("composition_plans")
    if not isinstance(values, dict) or not values:
        shoe = metadata.get("shoe_composition")
        values = shoe.get("composition_plans") if isinstance(shoe, dict) else None
    if not isinstance(values, dict) or not values:
        values = metadata.get("by_size")
    if not isinstance(values, dict):
        return {}
    return {
        str(size): plan
        for size, value in values.items()
        if (plan := _typed_plan_from_mapping(source, str(size), value)) is not None
    }


def _typed_decision(source: Path, decision: object) -> ClassificationDecision:
    metadata = dict(getattr(decision, "metadata", None) or {})
    plans = _typed_plans_from_metadata(source, metadata)
    fallback_reason = metadata.get("fallback_reason") or next(
        (plan.fallback_reason for plan in plans.values() if plan.fallback_reason),
        None,
    )
    return ClassificationDecision(
        source=str(source),
        source_type=str(getattr(decision, "source_type", "unknown")),
        confidence=float(getattr(decision, "confidence", 0.0)),
        classifier=str(getattr(decision, "source", "rules")),
        reason=str(getattr(decision, "reason", "")),
        metadata=metadata,
        composition_plans=plans,
        fallback=bool(metadata.get("fallback")) or any(plan.fallback for plan in plans.values()),
        fallback_reason=str(fallback_reason) if fallback_reason else None,
        skipped_by_scope=bool(metadata.get("skipped_by_scope"))
        or any(plan.skipped_by_scope for plan in plans.values()),
    )


def classification_summary(decisions: list[ClassificationDecision]) -> dict[str, object]:
    """Return the small auditable summary needed at the Worker boundary."""
    fallback = [item for item in decisions if item.fallback]
    skipped = [item for item in decisions if item.skipped_by_scope]
    return {
        "count": len(decisions),
        "fallback_count": len(fallback),
        "skipped_by_scope_count": len(skipped),
        "skipped_by_scope": [item.source for item in skipped],
    }


def _merge_composition_metadata(
    metadata: dict[str, object],
    composition: object,
    category: str | None,
    *,
    source: str | None = None,
    purpose: str,
    segmentation_evidence: object | None = None,
    analyzed: bool = True,
) -> dict[str, object]:
    """Flatten model output once, keeping the renderer-facing plan typed."""
    result = dict(metadata)
    raw = composition if isinstance(composition, dict) else {}
    if category in {"shoes", "socks"} and purpose == "pose" and not raw and not result.get("shoe_composition"):
        from .model_service import _fallback_composition

        raw = _fallback_composition(SIZES, "Pose 未返回构图计划")
    if purpose == "seg" and not raw:
        from .model_service import _fallback_segmentation

        raw = _fallback_segmentation("Seg 未返回分割证据")
    if category in {"shoes", "socks"}:
        from .shoe_composition import normalize_shoe_composition

        shoe_raw: object = result.get("shoe_composition") if purpose == "seg" else raw
        shoe = normalize_shoe_composition(
            shoe_raw if isinstance(shoe_raw, dict) else raw,
            source=source,
            segmentation_evidence=(
                segmentation_evidence if isinstance(segmentation_evidence, dict) else None
            ),
        )
        result.update(
            {
                "shoe_risk": shoe.shoe_risk,
                "shoe_risk_reasons": list(shoe.shoe_risk_reasons),
                "shoe_composition": asdict(shoe),
                "composition_plans": {
                    size: plan.to_dict() for size, plan in shoe.composition_plans.items()
                },
                "fallback": bool(result.get("fallback")) or shoe.fallback,
                "fallback_reason": result.get("fallback_reason") or shoe.fallback_reason,
                "skipped_by_scope": bool(result.get("skipped_by_scope")) or shoe.skipped_by_scope,
            }
        )
        if shoe.review_hint:
            result["review_hint"] = shoe.review_hint
    # Pose returns one crop plan per output size. Keep that typed plan for all
    # model-photo categories; without this bridge the renderer only receives
    # the source type and silently falls back to a generic crop.
    if purpose == "pose" and category not in {"shoes", "socks"} and isinstance(raw.get("by_size"), dict):
        plans: dict[str, dict[str, object]] = {}
        for size, value in raw["by_size"].items():
            if not isinstance(value, dict):
                continue
            plan = dict(value)
            percent = plan.pop("scale_adjustment_percent", None)
            if "scale" not in plan and percent is not None:
                try:
                    plan["scale"] = max(0.05, 1.0 + float(percent) / 100.0)
                except (TypeError, ValueError):
                    pass
            if "crop_ratio" not in plan and "target_crop_height_ratio" in plan:
                plan["crop_ratio"] = plan.get("target_crop_height_ratio")
            protected = plan.pop("protected_crop_bbox", None)
            if protected is not None and "protected_regions" not in plan:
                plan["protected_regions"] = [{"kind": "model_subject", "bbox": protected}]
            plan["source"] = str(source or "")
            plan["size"] = str(size)
            plan["evidence"] = {
                "detector": raw.get("detector"),
                "model_view": raw.get("model_view"),
                **plan.get("evidence", {}),
            }
            plans[str(size)] = plan
        if plans:
            result["composition_plans"] = plans
            hint = next((str(plan["review_hint"]) for plan in plans.values() if plan.get("review_hint")), "")
            if hint and not result.get("review_hint"):
                result["review_hint"] = hint
    if isinstance(raw, dict):
        result.setdefault(
            f"{purpose}_evidence",
            {"status": "fallback" if raw.get("fallback") else "ready"},
        )
        if raw.get("fallback"):
            result["fallback"] = True
            result["fallback_reason"] = result.get("fallback_reason") or raw.get("fallback_reason")
    if purpose == "pose":
        result["_pose_analyzed"] = analyzed
    else:
        result["_seg_analyzed"] = analyzed
    result["composition_summary"] = {
        "fallback": bool(result.get("fallback")),
        "fallback_reason": result.get("fallback_reason"),
        "skipped_by_scope": bool(result.get("skipped_by_scope")),
    }
    return result


def _material_sources(product_root: Path) -> list[Path]:
    sources: list[Path] = []

    def looks_like_generated_output(directory: Path) -> bool:
        if directory.name not in {"成品主图", "产品主图", "模特图"}:
            return False
        try:
            child_names = {
                entry.name for entry in directory.rglob("*") if entry.is_dir()
            }
        except OSError:
            return False
        return bool(child_names & GENERATED_OUTPUT_MARKERS)

    def collect(directory: Path) -> None:
        if directory != product_root and looks_like_generated_output(directory):
            return
        try:
            entries = tuple(sorted(directory.iterdir(), key=lambda path: path.name))
        except OSError:
            return
        sources.extend(
            entry
            for entry in entries
            if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS
        )
        for entry in entries:
            if entry.is_dir() and not entry.is_symlink() and not entry.name.startswith("."):
                collect(entry)

    collect(product_root)
    return sorted(sources, key=lambda path: str(path))


def _matches_filter(source: Path, source_filter: set[str] | None) -> bool:
    return source_filter is None or any(
        key in source_filter for key in (str(source), source.as_posix())
    )


class V26ClassifierAdapter:
    """Use v2.6's rule classifier without importing Qt or its UI."""

    def __init__(self, v2_root: Path | None = None, *, bundled_only: bool = False) -> None:
        self.v2_root = (v2_root or self._default_v2_root()).resolve()
        bundled_root = (Path(__file__).resolve().parent / "legacy_engine").resolve()
        self.bundled_only = bundled_only or self.v2_root == bundled_root
        self._classifier_type = None

    @staticmethod
    def _default_v2_root() -> Path:
        configured = os.environ.get("PIXELFLOW_V2_ROOT")
        if configured:
            return Path(configured).expanduser()
        return Path(__file__).resolve().parent / "legacy_engine"

    def _load_classifier(self):
        if self._classifier_type is None:
            if not self.v2_root.is_dir():
                raise FileNotFoundError(f"v2.6 engine root not found: {self.v2_root}")
            bundled_root = (Path(__file__).resolve().parent / "legacy_engine").resolve()
            if self.bundled_only:
                if self.v2_root != bundled_root:
                    raise FileNotFoundError(f"external v2.6 engine root is not supported: {self.v2_root}")
                from .legacy_engine.category_engine.source_type_classifier import RuleSourceTypeClassifier
            else:
                root_text = str(self.v2_root)
                if root_text not in sys.path:
                    sys.path.insert(0, root_text)
                from category_engine.source_type_classifier import RuleSourceTypeClassifier

            self._classifier_type = RuleSourceTypeClassifier
        return self._classifier_type

    def classify(
        self,
        request: ClassificationRequest,
        progress: ClassificationProgress | None = None,
    ) -> list[ClassificationDecision]:
        request.validate()
        sources = [
            source
            for source in (
                request.sources
                if request.sources is not None
                else _material_sources(request.product_root)
            )
            if _matches_filter(source, request.source_filter)
        ]
        classifier = self._load_classifier()()
        decisions = classifier.classify_many(
            sources,
            overrides=request.overrides,
            workers=max(1, int(request.workers or 1)),
            progress=(
                lambda completed, total, source, decision: progress(
                    completed,
                    total,
                    source,
                    ClassificationDecision(
                        source=str(source),
                        source_type=decision.source_type,
                        confidence=float(decision.confidence),
                        classifier=decision.source,
                        reason=decision.reason,
                        metadata=decision.metadata,
                    ),
                )
                if progress is not None
                else None
            ),
        )
        if request.local_model_enabled and request.local_model_path:
            category = request.category
            if not category or category == "auto":
                # Resolve the category before Pose/Seg so model composition gets
                # the same category context as the v2.6 confirmation flow.
                from .legacy_engine.category_engine.category_classifier import RuleCategoryClassifier

                category = RuleCategoryClassifier().classify(request.product_root).category
            decisions = self._apply_optional_models(
                decisions,
                request.local_model_path,
                category,
                request.cancel_event,
                request.stage_progress,
                request.model_progress,
                request.model_trace,
                request.trace_request_id,
            )
        return [_typed_decision(source, decision) for source, decision in decisions.items()]

    def _apply_optional_models(
        self,
        decisions,
        model_root: str,
        category: str | None,
        cancel_event: Event | None,
        stage_progress: ClassificationStageProgress | None = None,
        model_progress: ClassificationModelProgress | None = None,
        model_trace: ClassificationModelTrace | None = None,
        trace_request_id: str = "",
    ):
        """Match v2.6 confirmation behavior without importing Qt."""
        root_text = str(model_root).strip()
        if not root_text:
            return decisions
        from .model_service import ModelProcessError, ModelProcessService, configured_model_candidates, get_model_process_service
        from .model_cache import file_version
        from .legacy_engine.local_model_assistant import (
            resolve_model_source_type,
            resolve_segmentation_source_type,
        )

        # A single classification request can consult the same source in
        # Pose, anchor Seg, and the final Seg pass.  Computing the SHA-256
        # source identity once per source keeps cache identity exact while
        # avoiding repeated full-image reads.
        source_versions: dict[Path, dict[str, object] | None] = {}

        def cache_source_version(source: Path) -> dict[str, object] | None:
            if source not in source_versions:
                source_versions[source] = file_version(source, include_digest=True)
            return source_versions[source]

        # Keep obvious product folders cheap. Homogeneous detail folders get a
        # small Pose sample so model photos without naming hints are covered.
        folder_types: dict[Path, set[str]] = {}
        folder_sources: dict[Path, list[Path]] = {}
        for source, decision in decisions.items():
            folder_types.setdefault(source.parent, set()).add(decision.source_type)
            folder_sources.setdefault(source.parent, []).append(source)
        pose_sources = [
            source
            for source, decision in decisions.items()
            if (
                decision.source_type != "main_product"
                and (
                    decision.confidence < 0.70
                    or decision.source in {"error", "fallback"}
                    or decision.source_type in {"model", "model_detail"}
                    or (
                        "model" in folder_types.get(source.parent, set())
                        and bool(
                            folder_types.get(source.parent, set())
                            & {"main_product", "detail", "model_detail"}
                        )
                    )
                )
            )
        ]
        sampled_groups: dict[Path, tuple[list[Path], list[Path]]] = {}
        for folder, sources_in_folder in folder_sources.items():
            if folder_types.get(folder) != {"detail"}:
                continue
            ordered = sorted(sources_in_folder, key=str)
            if any(
                decisions[source].confidence < 0.70
                or decisions[source].source in {"rules_product_folder", "error", "fallback"}
                for source in ordered
            ):
                continue
            # Up to five evenly spaced samples, always including both ends.
            sample_count = min(len(ordered), 5)
            samples = list(dict.fromkeys(
                ordered[round(index * (len(ordered) - 1) / max(1, sample_count - 1))]
                for index in range(sample_count)
            ))
            sampled_groups[folder] = (ordered, samples)
            pose_sources.extend(samples)
        pose_sources = list(dict.fromkeys(pose_sources))
        pose_available = bool(configured_model_candidates(root_text, "pose"))
        failed_models: set[str] = set()
        failure_codes: dict[str, str] = {}
        failure_stages: dict[str, str] = {}
        failed_images: dict[str, set[Path]] = {
            "pose": set(),
            "segmentation": set(),
            "detection": set(),
        }
        safe_model_errors = {
            "model_not_found",
            "model_configuration_invalid",
            "model_session_create_failed",
            "model_process_start_failed",
            "model_process_exited",
            "model_request_timeout",
            "model_request_cancelled",
            "model_inference_failed",
        }

        def failure_code(error: Exception, purpose: str) -> str:
            code = getattr(error, "code", None)
            return code if isinstance(code, str) and code in safe_model_errors else "model_inference_failed"

        def disables_model(code: str) -> bool:
            return code in {
                "model_not_found",
                "model_configuration_invalid",
                "model_session_create_failed",
                "model_process_start_failed",
                "model_process_exited",
                "model_request_timeout",
            }

        def failure_reason(purpose: str, failed_stage: str) -> str:
            if purpose == "pose" and failed_stage == "session_create":
                return "Pose 模型初始化失败，已使用规则回退"
            if purpose == "pose":
                return "Pose 模型处理失败，已使用规则回退"
            if purpose == "segmentation" or purpose == "seg":
                return "Seg 模型处理失败，已使用保守回退"
            return "检测模型处理失败，已跳过检测"

        def report_model_progress(
            phase: str,
            total: int,
            purpose: str,
            error_code: str | None = None,
            failed_stage: str | None = None,
        ) -> None:
            if model_progress is None:
                return
            bounded_total = max(0, min(int(total), 1_000_000))
            model_progress(
                phase,
                0,
                bounded_total,
                purpose,
                error_code,
                failed_stage,
            )

        def model_call(service, method_name: str, *args, **kwargs):
            method = getattr(service, method_name)
            if isinstance(service, ModelProcessService):
                kwargs.update(
                    cancel_event=cancel_event,
                    request_id=trace_request_id,
                    trace_callback=model_trace,
                )
            return method(*args, **kwargs)

        def mark_model_fallback(
            sources_to_mark: list[Path],
            purpose: str,
            reason: str,
            error_code: str | None = None,
            failed_stage: str | None = None,
        ) -> None:
            from .model_service import _fallback_composition, _fallback_segmentation

            fallback_purpose = {
                "pose": "pose",
                "seg": "segmentation",
                "segmentation": "segmentation",
                "detection": "detection",
            }.get(purpose, purpose)
            fallback = (
                _fallback_composition(SIZES, reason)
                if purpose == "pose"
                else _fallback_segmentation(reason)
            )
            for source in sources_to_mark:
                decision = decisions[source]
                metadata = _merge_composition_metadata(
                    dict(decision.metadata or {}),
                    fallback,
                    category,
                    source=str(source),
                    purpose=purpose,
                    segmentation_evidence=(fallback if purpose == "seg" else None),
                    analyzed=False,
                )
                metadata["fallback"] = True
                metadata["fallback_reason"] = reason
                metadata["model_status"] = "fallback"
                metadata["fallback_purpose"] = fallback_purpose
                evidence = {
                    "status": "fallback",
                    "reason": reason,
                }
                metadata.pop("fallback_code", None)
                if isinstance(error_code, str) and error_code in safe_model_errors:
                    metadata["fallback_code"] = error_code
                    evidence["error_code"] = error_code
                metadata.pop("failed_stage", None)
                if failed_stage in {"session_create", "inference"}:
                    metadata["failed_stage"] = failed_stage
                    evidence["failed_stage"] = failed_stage
                metadata[f"{fallback_purpose}_evidence"] = evidence
                decisions[source] = type(decision)(
                    decision.source_type,
                    decision.confidence,
                    decision.source,
                    decision.reason,
                    metadata=metadata,
                )

        if pose_available:
            service = get_model_process_service()
            from .model_cache import load_model_result, store_model_result

            pose_models = configured_model_candidates(root_text, "pose")
            pose_model_path = Path(pose_models[0]) if pose_models else None
            # Shoe detector shapes the shoe and pants compositions, so it keys that cache.
            detection_models = (
                configured_model_candidates(root_text, "detection")
                if category in DETECTION_CATEGORY_MAP else []
            )
            detection_model_path = Path(detection_models[0]) if detection_models else None
            pose_ready = True
            if pose_sources:
                warmup = getattr(service, "warmup", None)
                if warmup is not None:
                    report_model_progress("model_loading", len(pose_sources), "pose")
                    try:
                        warmup_result = model_call(service, "warmup", root_text, "pose")
                        pose_ready = (
                            isinstance(warmup_result, dict)
                            and warmup_result.get("status") == "ready"
                            and not warmup_result.get("fallback")
                        )
                        if pose_ready:
                            report_model_progress("warmup_ready", len(pose_sources), "pose")
                        else:
                            error_code = (
                                str(warmup_result.get("error_code"))
                                if isinstance(warmup_result, dict)
                                and warmup_result.get("error_code") in safe_model_errors
                                else "model_session_create_failed"
                            )
                            failed_models.add("pose")
                            failure_codes["pose"] = error_code
                            failure_stages["pose"] = "session_create"
                            report_model_progress(
                                "model_fallback", len(pose_sources), "pose", error_code, "session_create"
                            )
                    except Exception as error:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ClassificationCancelled() from error
                        pose_ready = False
                        error_code = failure_code(error, "pose")
                        failed_models.add("pose")
                        failure_codes["pose"] = error_code
                        failure_stages["pose"] = "session_create"
                        report_model_progress(
                            "model_fallback", len(pose_sources), "pose", error_code, "session_create"
                        )
                    if not pose_ready:
                        mark_model_fallback(
                            pose_sources,
                            "pose",
                            failure_reason("pose", "session_create"),
                            failure_codes.get("pose"),
                            "session_create",
                        )
            seg_models = configured_model_candidates(root_text, "segmentation")
            anchor_seg_path = Path(seg_models[0]) if seg_models else None

            def anchor_segmentation(source: Path, composition: dict[str, object]) -> dict[str, object] | None:
                """Seg outline for shoe/shirt model photos (head top and sole)."""
                from .model_assistant import ANCHOR_CATEGORIES

                if (
                    "segmentation" in failed_models
                    or
                    anchor_seg_path is None
                    or category not in ANCHOR_CATEGORIES
                    or composition.get("model_view") in {None, "detail"}
                ):
                    return None
                try:
                    evidence = load_model_result(
                        source, anchor_seg_path, "segmentation", category,
                        output_size="segmentation", source_version=cache_source_version(source),
                    )
                    if isinstance(evidence, dict) and (
                        evidence.get("error_code") or evidence.get("failed_stage")
                    ):
                        evidence = None
                    if evidence is None:
                        evidence = model_call(service, "segment", root_text, source)
                        if not evidence.get("error_code") and not evidence.get("failed_stage"):
                            store_model_result(
                                source, anchor_seg_path, "segmentation", category,
                                evidence, output_size="segmentation", source_version=cache_source_version(source),
                            )
                    if evidence.get("fallback"):
                        error_code = evidence.get("error_code")
                        if error_code in safe_model_errors:
                            failed_images["segmentation"].add(source)
                            if disables_model(error_code):
                                failed_models.add("segmentation")
                                failure_codes["segmentation"] = error_code
                                failure_stages["segmentation"] = "inference"
                                report_model_progress(
                                    "model_fallback", len(pose_sources), "segmentation", error_code, "inference"
                                )
                        return None
                except Exception as error:  # noqa: BLE001 - anchors fall back to Pose only
                    if cancel_event is not None and cancel_event.is_set():
                        raise ClassificationCancelled() from error
                    error_code = failure_code(error, "segmentation")
                    failed_images["segmentation"].add(source)
                    if disables_model(error_code):
                        failed_models.add("segmentation")
                        failure_codes["segmentation"] = error_code
                        failure_stages["segmentation"] = "inference"
                        report_model_progress(
                            "model_fallback", len(pose_sources), "segmentation", error_code, "inference"
                        )
                    return None
                return None if evidence.get("fallback") else evidence

            detect_boxes = getattr(service, "detect_boxes", None)
            detect_shoes = getattr(service, "detect_shoes", None)

            def detector_boxes(source: Path) -> list[list[float]]:
                """Optional category-mapped detector boxes (source px)."""
                if "detection" in failed_models or source in failed_images["detection"]:
                    return []
                detector_category = DETECTION_CATEGORY_MAP.get(category)
                if detector_category is None:
                    return []
                try:
                    if detect_boxes is not None:
                        boxes = model_call(service, "detect_boxes", root_text, source, detector_category)
                    elif detector_category == "shoes" and detect_shoes is not None:
                        boxes = model_call(service, "detect_shoes", root_text, source)
                    else:
                        return []
                    if isinstance(service, ModelProcessService):
                        status = service.model_status(root_text, "detection")
                        if status.get("status") == "fallback":
                            error_code = status.get("fallback_reason", "model_inference_failed")
                            error_code = error_code if error_code in safe_model_errors else "model_inference_failed"
                            failed_images["detection"].add(source)
                            if disables_model(error_code):
                                failed_models.add("detection")
                                failure_codes["detection"] = error_code
                                failure_stages["detection"] = "inference"
                    return list(boxes or [])
                except Exception as error:  # noqa: BLE001 - anchors fall back to Pose only
                    if cancel_event is not None and cancel_event.is_set():
                        raise ClassificationCancelled() from error
                    error_code = failure_code(error, "detection")
                    failed_images["detection"].add(source)
                    if disables_model(error_code):
                        failed_models.add("detection")
                        failure_codes["detection"] = error_code
                        failure_stages["detection"] = "inference"
                return []

            def run_pose(sources_to_check: list[Path]) -> None:
                for index, source in enumerate(sources_to_check, start=1):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ClassificationCancelled()
                    if "pose" in failed_models:
                        mark_model_fallback(
                            sources_to_check[index - 1 :],
                            "pose",
                            failure_reason("pose", failure_stages.get("pose", "inference")),
                            failure_codes.get("pose", "model_inference_failed"),
                            failure_stages.get("pose", "inference"),
                        )
                        return
                    if source in failed_images["pose"]:
                        mark_model_fallback(
                            [source],
                            "pose",
                            failure_reason("pose", "inference"),
                            failure_codes.get("pose", "model_inference_failed"),
                            "inference",
                        )
                        if stage_progress is not None:
                            stage_progress("Pose", index, len(sources_to_check), source)
                        continue
                    decision = decisions[source]
                    try:
                        composition = (
                            load_model_result(
                                source,
                                pose_model_path,
                                "pose",
                                category,
                                output_size="all",
                                detection_model_path=detection_model_path,
                                source_version=cache_source_version(source),
                            )
                            if pose_model_path is not None
                            else None
                        )
                        if isinstance(composition, dict) and (
                            composition.get("error_code") or composition.get("failed_stage")
                        ):
                            composition = None
                        if composition is None:
                            composition = model_call(service, "compose_for_sizes",
                                root_text, source, SIZES, product_category=category
                            )
                            error_code = composition.get("error_code")
                            if error_code in safe_model_errors:
                                error_code = str(error_code)
                                failed_images["pose"].add(source)
                                if disables_model(error_code):
                                    failure_stages["pose"] = "inference"
                                raise ModelProcessError("Pose 模型失败", error_code)
                            segmentation = anchor_segmentation(source, composition)
                            boxes = detector_boxes(source)
                            if segmentation is not None or boxes:
                                # Pose detections are cached in-process, so the
                                # second pass only recomputes the crop window.
                                extra = {"detector_boxes": boxes} if boxes else {}
                                composition = model_call(service, "compose_for_sizes",
                                    root_text, source, SIZES, product_category=category,
                                    segmentation=segmentation, **extra,
                                )
                                error_code = composition.get("error_code")
                                if error_code in safe_model_errors:
                                    error_code = str(error_code)
                                    failed_images["pose"].add(source)
                                    if disables_model(error_code):
                                        failure_stages["pose"] = "inference"
                                    raise ModelProcessError("Pose 模型失败", error_code)
                            if pose_model_path is not None and not composition.get("error_code") and not composition.get("failed_stage"):
                                store_model_result(
                                    source,
                                    pose_model_path,
                                    "pose",
                                    category,
                                    composition,
                                    output_size="all",
                                    detection_model_path=detection_model_path,
                                    source_version=cache_source_version(source),
                                )
                        updated = resolve_model_source_type(decision, composition, category)
                        metadata = _merge_composition_metadata(
                            dict(updated.metadata or {}),
                            composition,
                            category,
                            source=str(source),
                            purpose="pose",
                        )
                        decisions[source] = type(updated)(
                            updated.source_type, updated.confidence, updated.source,
                            updated.reason, metadata=metadata,
                        )
                    except Exception as error:
                        if cancel_event is not None and cancel_event.is_set():
                            raise ClassificationCancelled() from error
                        error_code = failure_code(error, "pose")
                        failed_images["pose"].add(source)
                        if disables_model(error_code):
                            failed_models.add("pose")
                            failure_codes["pose"] = error_code
                            failure_stages["pose"] = "inference"
                            mark_model_fallback(
                                sources_to_check[index - 1 :],
                                "pose",
                                failure_reason("pose", "inference"),
                                error_code,
                                "inference",
                            )
                            report_model_progress(
                                "model_fallback", len(sources_to_check), "pose", error_code, "inference"
                            )
                        else:
                            mark_model_fallback(
                                [source],
                                "pose",
                                failure_reason("pose", "inference"),
                                error_code,
                                "inference",
                            )
                        if stage_progress is not None:
                            stage_progress("Pose", index, len(sources_to_check), source)
                        if disables_model(error_code):
                            return
                        continue
                    if stage_progress is not None:
                        stage_progress("Pose", index, len(sources_to_check), source)

            if pose_ready:
                run_pose(pose_sources)
            expanded_sources: list[Path] = []
            for ordered, samples in sampled_groups.values():
                sample_types = [decisions[source].source_type for source in samples]
                model_types = {item for item in sample_types if item in {"model", "model_detail"}}
                # Any model sample: run Pose on every sibling so each image
                # gets its own crop plan (plans are never shared between files).
                if model_types:
                    expanded_sources.extend(source for source in ordered if source not in samples)
            expanded_sources = list(dict.fromkeys(expanded_sources))
            if expanded_sources:
                run_pose(expanded_sources)
            all_pose_sources = list(dict.fromkeys(pose_sources + expanded_sources))
        else:
            all_pose_sources = pose_sources
            if all_pose_sources:
                mark_model_fallback(all_pose_sources, "pose", "pose/ 中没有 ONNX 模型")

        seg_available = bool(configured_model_candidates(root_text, "segmentation"))
        if seg_available:
            service = get_model_process_service()
            from .model_cache import load_model_result, store_model_result

            seg_models = configured_model_candidates(root_text, "segmentation")
            seg_model_path = Path(seg_models[0]) if seg_models else None
            candidates = [
                source for source in all_pose_sources
                if decisions[source].source_type in {"detail", "model_detail"}
                or decisions[source].confidence < 0.70
            ]
            for index, source in enumerate(candidates, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise ClassificationCancelled()
                if "segmentation" in failed_models:
                    mark_model_fallback(
                        candidates[index - 1 :],
                        "seg",
                        failure_reason("segmentation", failure_stages.get("segmentation", "inference")),
                        failure_codes.get("segmentation", "model_inference_failed"),
                        failure_stages.get("segmentation", "inference"),
                    )
                    break
                if source in failed_images["segmentation"]:
                    mark_model_fallback(
                        [source],
                        "seg",
                        failure_reason("segmentation", "inference"),
                        "model_inference_failed",
                        "inference",
                    )
                    if stage_progress is not None:
                        stage_progress("Seg", index, len(candidates), source)
                    continue
                decision = decisions[source]
                try:
                    evidence = (
                        load_model_result(
                            source,
                            seg_model_path,
                            "segmentation",
                            category,
                            output_size="segmentation",
                            source_version=cache_source_version(source),
                        )
                        if seg_model_path is not None
                        else None
                    )
                    if isinstance(evidence, dict) and (
                        evidence.get("error_code") or evidence.get("failed_stage")
                    ):
                        evidence = None
                    if evidence is None:
                        evidence = model_call(service, "segment", root_text, source)
                        error_code = evidence.get("error_code")
                        if error_code in safe_model_errors:
                            error_code = str(error_code)
                            failed_images["segmentation"].add(source)
                            if disables_model(error_code):
                                failure_stages["segmentation"] = "inference"
                                raise ModelProcessError("Seg 模型失败", error_code)
                            mark_model_fallback(
                                [source], "seg", failure_reason("segmentation", "inference"), error_code, "inference"
                            )
                            if stage_progress is not None:
                                stage_progress("Seg", index, len(candidates), source)
                            continue
                        if seg_model_path is not None and not evidence.get("error_code") and not evidence.get("failed_stage"):
                            store_model_result(
                                source,
                                seg_model_path,
                                "segmentation",
                                category,
                                evidence,
                                output_size="segmentation",
                                source_version=cache_source_version(source),
                            )
                    updated = resolve_segmentation_source_type(decision, evidence)
                    metadata = _merge_composition_metadata(
                        dict(updated.metadata or {}),
                        evidence,
                        category,
                        source=str(source),
                        purpose="seg",
                        segmentation_evidence=evidence,
                    )
                    decisions[source] = type(updated)(
                        updated.source_type, updated.confidence, updated.source,
                        updated.reason, metadata=metadata,
                    )
                except Exception as error:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ClassificationCancelled() from error
                    error_code = failure_code(error, "segmentation")
                    failed_images["segmentation"].add(source)
                    if disables_model(error_code):
                        failed_models.add("segmentation")
                        failure_codes["segmentation"] = error_code
                        failure_stages["segmentation"] = "inference"
                        mark_model_fallback(
                            candidates[index - 1 :],
                            "seg",
                            failure_reason("segmentation", "inference"),
                            error_code,
                            "inference",
                        )
                        report_model_progress(
                            "model_fallback", len(candidates), "segmentation", error_code, "inference"
                        )
                        if stage_progress is not None:
                            stage_progress("Seg", index, len(candidates), source)
                        break
                    mark_model_fallback(
                        [source], "seg", failure_reason("segmentation", "inference"), error_code, "inference"
                    )
                    if stage_progress is not None:
                        stage_progress("Seg", index, len(candidates), source)
                    continue
                if stage_progress is not None:
                    stage_progress("Seg", index, len(candidates), source)
        else:
            candidates = [
                source for source in all_pose_sources
                if decisions[source].source_type in {"detail", "model_detail"}
                or decisions[source].confidence < 0.70
            ]
            if candidates:
                mark_model_fallback(candidates, "seg", "segmentation/ 中没有 ONNX 模型")
        return decisions
