from __future__ import annotations

import inspect
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event
from typing import Any, Callable

from .compare import compare_output_trees
from .contracts import (
    CompositionPlan,
    RenderAssets,
    RenderLayout,
    RenderRequest,
    RenderSummary,
    RunMode,
    build_main_request,
    build_model_request,
    build_transparent_request,
    build_vip_request,
    validate_run_mode,
)
from .renderer import Renderer


ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class GenerationRequest:
    """Qt-free request accepted by the v3 Worker boundary."""

    product_root: Path
    output_root: Path
    selected_sizes: tuple[str, ...]
    include_logo: bool = True
    include_vip: bool = True
    include_model_images: bool = True
    max_size_mb: float | None = None
    logo_overrides: dict[str, str] = field(default_factory=dict)
    category_override: str | None = None
    enable_category_template: bool = False
    folder_category_overrides: dict[str, str] = field(default_factory=dict)
    enable_material_understanding: bool = False
    source_type_overrides: dict[str, str] = field(default_factory=dict)
    source_metadata_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_scale_adjustments: dict[str, float] = field(default_factory=dict)
    excluded_model_sources: set[str] = field(default_factory=set)
    include_selling_point_images: bool = False
    selling_points: tuple[str, ...] = ()
    source_filter: set[str] | None = None
    force_rerender: bool = False
    naming_mode: str = "short"
    local_model_enabled: bool = False
    local_model_path: str | None = None
    material_confirmation_completed: bool = True
    mode: RunMode = "legacy"
    composition_plans: tuple[CompositionPlan, ...] = ()

    def validate(self) -> None:
        validate_run_mode(self.mode)
        if not self.product_root.is_dir():
            raise ValueError(f"product_root is not a directory: {self.product_root}")
        product_root = self.product_root.expanduser().resolve()
        output_root = self.output_root.expanduser().resolve(strict=False)
        if output_root == product_root or product_root in output_root.parents:
            raise ValueError("output_root cannot be the input directory or one of its subdirectories")
        if self.output_root.exists() and not self.output_root.is_dir():
            raise ValueError(f"output_root is not a directory: {self.output_root}")
        output_parent = self.output_root if self.output_root.exists() else self.output_root.parent
        while not output_parent.exists() and output_parent != output_parent.parent:
            output_parent = output_parent.parent
        if not output_parent.exists() or not os.access(output_parent, os.W_OK):
            raise ValueError(f"output_root is not writable: {self.output_root}")
        if not self.selected_sizes:
            raise ValueError("at least one output size is required")
        if not self.material_confirmation_completed:
            raise ValueError("material confirmation must complete before generation")
        if self.max_size_mb is not None and self.max_size_mb <= 0:
            raise ValueError("max_size_mb must be greater than zero")


class V3EngineAdapter:
    """Typed seam for the migrating v3 renderer.

    The callable is injected while the v3 renderer is extracted.  Keeping the
    default explicit avoids silently treating a legacy result as a v3 result.
    """

    def __init__(
        self,
        runner: Callable[..., dict[str, object]] | None = None,
        planner: Callable[..., object] | None = None,
        renderer: Renderer | None = None,
    ) -> None:
        self._runner = runner
        self._planner = planner
        self._renderer = renderer or Renderer()

    def plan(self, request: GenerationRequest) -> object:
        if self._planner is None:
            return None
        return self._planner(request)

    def run(
        self,
        request: GenerationRequest,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        request.validate()
        if self._runner is None:
            result = self._render_sources(request, progress, cancel_event)
            result["mode"] = "v3"
            result["renderer"] = "typed-v3"
            result["fallback"] = False
            result["typed_renderer"] = self._validate_outputs(request, result)
            return result
        result = self._runner(request, progress=progress, cancel_event=cancel_event)
        if hasattr(result, "to_dict"):
            result = result.to_dict()
        if not isinstance(result, dict):
            raise TypeError("v3 runner must return a mapping")
        # The adapter owns the routing label even when a typed renderer uses
        # its compatibility default of ``legacy``.
        result["mode"] = "v3"
        result["typed_renderer"] = self._validate_outputs(request, result)
        return result

    @staticmethod
    def _plan(value: object, source: Path, size: str) -> CompositionPlan | None:
        if isinstance(value, CompositionPlan):
            return value
        if not isinstance(value, dict):
            return None
        regions = []
        for item in value.get("protected_regions", ()):
            if not isinstance(item, dict):
                continue
            try:
                from .contracts import ProtectedRegion
                regions.append(ProtectedRegion(**item))
            except (TypeError, ValueError):
                continue
        try:
            return CompositionPlan(
                source=str(value.get("source") or source),
                size=str(value.get("size") or size),
                composition_mode=str(value.get("composition_mode", "fallback")),
                scale=float(value.get("scale", 1.0)),
                position_x=float(value.get("position_x", 0.5)),
                position_y=float(value.get("position_y", 0.5)),
                crop_ratio=value.get("crop_ratio"),
                protected_regions=tuple(regions),
                risk_flags=tuple(value.get("risk_flags", ())),
                requires_review=bool(value.get("requires_review", False)),
                evidence=dict(value.get("evidence", {})),
                fallback=bool(value.get("fallback", False)),
                review_hint=str(value.get("review_hint", "") or ""),
                fallback_reason=str(value.get("fallback_reason", "") or ""),
                skipped_by_scope=bool(value.get("skipped_by_scope", False)),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _group_label(source: Path) -> str:
        name = source.parent.name.strip()
        match = re.search(r"男女|男|女", name)
        if match:
            color = name[match.end():].strip(" _-—·()（）[]【】")
            if color:
                return f"{match.group(0)}_{color[:12]}"
        return "素材"

    @classmethod
    def _short_names(cls, sources: list[Path]) -> dict[Path, str]:
        counters: dict[Path, int] = {}
        names: dict[Path, str] = {}
        for source in sources:
            counters[source.parent] = counters.get(source.parent, 0) + 1
            names[source] = f"{cls._group_label(source)}_{counters[source.parent]:02d}"
        return names

    @staticmethod
    def _summary(decision: object, source_type: str) -> RenderSummary:
        metadata = getattr(decision, "metadata", None) or {}
        return RenderSummary(
            source_type=source_type,
            classifier=str(getattr(decision, "classifier", "") or ""),
            confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            classification_reason=str(getattr(decision, "reason", "") or ""),
            pose_evidence=str((metadata.get("pose_evidence") or {}).get("status", "not_run")) if isinstance(metadata.get("pose_evidence"), dict) else "not_run",
            segmentation_evidence=str((metadata.get("segmentation_evidence") or {}).get("status", "not_run")) if isinstance(metadata.get("segmentation_evidence"), dict) else "not_run",
            risk_flags=tuple(metadata.get("risk_flags", ()) or ()),
            requires_review=bool(metadata.get("requires_review", False)),
            review_hint=str(metadata.get("review_hint", "") or ""),
            fallback=bool(metadata.get("fallback", False)),
            fallback_reason=str(metadata.get("fallback_reason", "") or ""),
            skipped_by_scope=bool(metadata.get("skipped_by_scope", False)),
        )

    def _render_sources(
        self,
        request: GenerationRequest,
        progress: ProgressCallback | None,
        cancel_event: Event | None,
    ) -> dict[str, object]:
        from .classification import ClassificationRequest, _material_sources
        from .v3_classification import V3ClassifierAdapter

        sources = [p for p in _material_sources(request.product_root) if request.source_filter is None or str(p) in request.source_filter]
        if not sources:
            return {"sources": 0, "main_images": 0, "model_images": 0, "transparent_images": 0, "vip_images": 0, "failures": [], "outputs": [], "validation": {"passed": 0, "failed": 0}, "audit": {"checked_outputs": 0}}
        decisions = []
        if request.enable_material_understanding or request.include_model_images or request.source_type_overrides:
            decisions = V3ClassifierAdapter().classify(ClassificationRequest(
                product_root=request.product_root,
                source_filter=request.source_filter,
                overrides=request.source_type_overrides,
                category=request.category_override,
                local_model_enabled=request.local_model_enabled,
                local_model_path=request.local_model_path,
            ))
        by_source = {item.source: item for item in decisions}
        supplied_plans = {
            (plan.source, plan.size): plan for plan in request.composition_plans
        }
        short_names = self._short_names(sources)
        outputs: list[str] = []
        failures: list[dict[str, str]] = []
        review_items: dict[str, dict[str, object]] = {}
        fallback_items: dict[str, dict[str, object]] = {}
        skipped_outputs = 0
        reused_outputs = 0
        expected_outputs = 0
        main_count = model_count = transparent_count = vip_count = 0
        total = max(1, len(sources) * max(1, len(request.selected_sizes)))
        completed = 0
        for source in sources:
            decision = by_source.get(str(source))
            source_type = request.source_type_overrides.get(str(source)) or (
                decision.source_type if decision else "main_product"
            )
            metadata = dict((decision.metadata if decision else {}) or {})
            metadata.update(request.source_metadata_overrides.get(str(source), {}))
            category = str(request.category_override or metadata.get("category") or "产品主图")
            group = "" if source_type in {"model", "model_detail"} else self._group_label(source)
            output_category = "模特图" if source_type in {"model", "model_detail"} else "产品主图"
            background_value = metadata.get("background")
            background = None
            if isinstance(background_value, (tuple, list)) and len(background_value) == 3:
                try:
                    background = tuple(int(value) for value in background_value)
                except (TypeError, ValueError):
                    background = None
            for size in request.selected_sizes:
                if cancel_event is not None and cancel_event.is_set():
                    return {"cancelled": True, "sources": len(sources), "main_images": main_count, "model_images": model_count, "transparent_images": transparent_count, "vip_images": vip_count, "failures": failures, "outputs": outputs, "skipped_by_scope": skipped_outputs, "validation": {"passed": len(outputs) - skipped_outputs, "failed": len(failures), "skipped": skipped_outputs}, "audit": {"checked_outputs": len(outputs)}}
                plan = supplied_plans.get((str(source), size))
                if plan is None:
                    for candidate in request.composition_plans:
                        if candidate.size != size:
                            continue
                        candidate_source = Path(candidate.source)
                        if not candidate_source.is_absolute():
                            candidate_source = request.product_root / candidate_source
                        if candidate_source.resolve() == source.resolve():
                            plan = candidate
                            break
                if plan is None and decision is not None:
                    decision_plans = getattr(decision, "composition_plans", {})
                    candidate = decision_plans.get(size) if isinstance(decision_plans, dict) else None
                    if isinstance(candidate, CompositionPlan):
                        plan = candidate
                if plan is None:
                    plan_value = metadata.get("composition_plans", {}).get(size) if isinstance(metadata.get("composition_plans"), dict) else None
                    plan = self._plan(plan_value, source, size)
                filename = f"{short_names[source]}.jpg" if request.naming_mode == "short" else f"{source.stem}_png.jpg" if source.suffix.lower() == ".png" else f"{source.stem}_jpg.jpg"
                output_dir = request.output_root / output_category
                if group:
                    output_dir /= group
                output_dir /= size
                for include_logo in ((True, False) if request.include_logo else (False,)):
                    logo_key = "tall_logo" if size != "1440x1440" else "logo"
                    white_logo_key = "tall_white_logo" if size != "1440x1440" else "white_logo"
                    logo_path = request.logo_overrides.get(logo_key) or request.logo_overrides.get("logo")
                    white_logo_path = request.logo_overrides.get(white_logo_key) or request.logo_overrides.get("white_logo")
                    if metadata.get("logo_luminance", 255) < 160:
                        logo_path = white_logo_path or logo_path
                    # Keep both logo variants in the typed request metadata so
                    # Renderer can apply the same luminance choice as legacy.
                    assets = RenderAssets(logo=logo_path, white_logo=white_logo_path,
                                          border=str(metadata["border_path"]) if metadata.get("border_path") else None)
                    layout = RenderLayout(category=category, group=group,
                                          output_category=output_category, filename=filename,
                                          enable_category_template=request.enable_category_template,
                                          allow_mirror_extension=bool(metadata.get("allow_mirror_extension")),
                                          scale=float(metadata.get("scale", 1.0) or 1.0))
                    summary = self._summary(decision, source_type)
                    builder = build_model_request if source_type in {"model", "model_detail"} else build_main_request
                    typed_request = builder(
                        str(source), str(request.output_root), size,
                        output_path=str(output_dir / ("含Logo" if include_logo else "无Logo") / filename),
                        mode="v3", plan=plan, include_logo=include_logo,
                        max_size_mb=request.max_size_mb, source_type=source_type,
                        background=background, assets=assets, layout=layout, summary=summary,
                    )
                    expected_outputs += 1
                    # ponytail: reuse an existing deterministic output; add a manifest only when
                    # existence checks prove insufficient for changed-settings invalidation.
                    if Path(typed_request.output_path).is_file() and not request.force_rerender:
                        outputs.append(typed_request.output_path)
                        reused_outputs += 1
                        main_count += 1
                        model_count += int(source_type in {"model", "model_detail"})
                        continue
                    result = self._renderer.render(typed_request)
                    metadata = dict(getattr(result, "metadata", {}) or {})
                    if metadata.get("requires_review"):
                        item = review_items.setdefault(str(source), {"source": str(source), "sizes": [], "review_hint": "", "protected_regions": []})
                        item["sizes"].append(size)  # type: ignore[union-attr]
                        item["review_hint"] = metadata.get("review_hint") or item["review_hint"]
                        item["protected_regions"] = [region.to_dict() for region in (plan.protected_regions if plan else ())]
                    if metadata.get("fallback"):
                        item = fallback_items.setdefault(str(source), {"source": str(source), "sizes": [], "fallback_reason": ""})
                        item["sizes"].append(size)  # type: ignore[union-attr]
                        item["fallback_reason"] = metadata.get("fallback_reason") or item["fallback_reason"]
                    if result.success:
                        outputs.extend(result.outputs)
                        if (decision is not None and getattr(decision, "skipped_by_scope", False)) or (plan is not None and plan.skipped_by_scope):
                            skipped_outputs += len(result.outputs)
                        main_count += 1
                        model_count += int(source_type in {"model", "model_detail"})
                    else:
                        failures.append({"source": str(source), "message": "; ".join(result.errors)})
                completed += 1
                if progress:
                    progress(completed, total, "生成")
            if request.include_vip and source_type not in {"model", "model_detail"}:
                vip_path = request.output_root / "唯品专享1440" / "无Logo" / filename
                vip_request = build_vip_request(
                    str(source), str(request.output_root), "1440x1440", mode="v3", plan=plan,
                    output_path=str(vip_path), max_size_mb=request.max_size_mb, source_type=source_type,
                    assets=RenderAssets(border=request.logo_overrides.get("border")),
                    layout=RenderLayout(category=category, filename=filename,
                                        enable_category_template=request.enable_category_template),
                    summary=self._summary(decision, source_type),
                )
                expected_outputs += 1
                if Path(vip_request.output_path).is_file() and not request.force_rerender:
                    outputs.append(vip_request.output_path)
                    vip_count += 1
                    reused_outputs += 1
                else:
                    vip_result = self._renderer.render(vip_request)
                    if vip_result.success:
                        outputs.extend(vip_result.outputs); vip_count += 1
                        if (decision is not None and getattr(decision, "skipped_by_scope", False)) or (plan is not None and plan.skipped_by_scope):
                            skipped_outputs += len(vip_result.outputs)
                    else:
                        failures.append({"source": str(source), "message": "; ".join(vip_result.errors)})
            if source.suffix.lower() == ".png":
                for size in ("800x800", "1440x1440", "1440x1920", "1440x2160"):
                    transparent_path = request.output_root / "透明产品图" / size / f"{short_names[source]}_透明.png"
                    transparent_request = build_transparent_request(
                        str(source), str(request.output_root), size, mode="v3", plan=plan,
                        output_path=str(transparent_path), max_size_mb=request.max_size_mb,
                        source_type=source_type, background=background,
                        layout=RenderLayout(category=category, filename=transparent_path.name,
                                            enable_category_template=request.enable_category_template),
                        summary=self._summary(decision, source_type),
                    )
                    expected_outputs += 1
                    if transparent_path.is_file() and not request.force_rerender:
                        outputs.append(str(transparent_path))
                        transparent_count += 1
                        reused_outputs += 1
                        continue
                    transparent_result = self._renderer.render(transparent_request)
                    if transparent_result.success:
                        outputs.extend(transparent_result.outputs); transparent_count += 1
                        if (decision is not None and getattr(decision, "skipped_by_scope", False)) or (plan is not None and plan.skipped_by_scope):
                            skipped_outputs += len(transparent_result.outputs)
                    else:
                        failures.append({"source": str(source), "message": "; ".join(transparent_result.errors)})
        return {
            "sources": len(sources),
            "main_images": main_count,
            "model_images": model_count,
            "transparent_images": transparent_count,
            "vip_images": vip_count,
            "failures": failures,
            "outputs": outputs,
            "retried_only": request.source_filter is not None,
            "skipped_by_scope": skipped_outputs,
            "incremental_reused": reused_outputs,
            "incremental_skipped": bool(expected_outputs and reused_outputs == expected_outputs and not failures),
            "requires_review": sorted(review_items.values(), key=lambda item: str(item["source"])),
            "requires_review_count": len(review_items),
            "fallback_items": sorted(fallback_items.values(), key=lambda item: str(item["source"])),
            "fallback_count": len(fallback_items),
            "validation": {"passed": len(outputs) - skipped_outputs, "failed": len(failures), "skipped": skipped_outputs},
            "audit": {"checked_outputs": len(outputs)},
        }

    @staticmethod
    def _output_size(path: Path) -> str | None:
        if "唯品专享1440" in path.parts:
            return "1440x1440"
        for part in reversed(path.parts):
            if part.count("x") != 1:
                continue
            width, height = part.split("x")
            if width.isdigit() and height.isdigit() and int(width) > 0 and int(height) > 0:
                return f"{int(width)}x{int(height)}"
        return None

    def _validate_outputs(self, request: GenerationRequest, result: dict[str, object]) -> dict[str, object]:
        """Validate generated files through the typed renderer contract."""
        root = request.output_root
        if not root.is_dir():
            return {"validated": 0, "failed": 0, "outputs": []}
        typed_requests: list[RenderRequest] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            size = self._output_size(path)
            if size is None:
                continue
            transparent = "透明产品图" in path.parts or path.suffix.lower() == ".png"
            vip = "唯品专享1440" in path.parts
            source_type = "model" if "模特图" in path.parts else "main_product"
            layout = RenderLayout(filename=path.name, category="模特图" if source_type == "model" else "产品主图")
            if transparent:
                typed = build_transparent_request(
                    str(path), str(root), size, output_path=str(path), mode="v3",
                    max_size_mb=request.max_size_mb, source_type=source_type,
                    layout=layout, validate_only=True,
                )
            elif vip:
                typed = build_vip_request(
                    str(path), str(root), size, output_path=str(path), mode="v3",
                    max_size_mb=request.max_size_mb, source_type=source_type,
                    layout=layout, validate_only=True,
                )
            else:
                typed = (build_model_request if source_type == "model" else build_main_request)(
                    str(path), str(root), size, output_path=str(path), mode="v3",
                    include_logo="含Logo" in path.parts, max_size_mb=request.max_size_mb,
                    source_type=source_type, layout=layout, validate_only=True,
                )
            typed_requests.append(typed)
        if not typed_requests:
            return {"validated": 0, "failed": 0, "outputs": []}
        validate_existing = getattr(self._renderer, "validate_existing", None)
        if callable(validate_existing):
            typed_results = tuple(validate_existing(item) for item in typed_requests)
        else:
            # Compatibility seam for a test/dynamic renderer supplied by a
            # caller that only exposes the original batch method.
            render_batch = getattr(self._renderer, "render_batch", None)
            typed_results = tuple(render_batch(typed_requests)) if callable(render_batch) else ()
        failures = []
        for item in typed_results:
            if getattr(item, "success", False):
                continue
            failures.append(
                {
                    "source": str(getattr(item, "source", "")),
                    "errors": list(getattr(item, "errors", ())),
                }
            )
        output_paths: list[str] = []
        for item in typed_results:
            output_paths.extend(str(path) for path in getattr(item, "outputs", ()) if path)
        return {
            "validated": len(typed_results),
            "failed": len(failures),
            "outputs": output_paths,
            "failures": failures,
        }


def _plan_payload(result: dict[str, object]) -> object:
    for key in ("composition_plans", "plans", "plan"):
        if key in result:
            return result[key]
    return None


def _plan_differences(left: object, right: object) -> list[str]:
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        return sorted(
            key
            for key in set(left) | set(right)
            if left.get(key) != right.get(key)
        )
    return ["composition_plan"]


class ImageEngine:
    """Dispatch legacy, v3, and compare runs without changing v2 output."""

    def __init__(
        self,
        legacy_adapter: Any,
        v3_adapter: Any | None = None,
    ) -> None:
        self.legacy_adapter = legacy_adapter
        self.v3_adapter = v3_adapter or V3EngineAdapter()

    @staticmethod
    def _plan_adapter(adapter: Any, request: GenerationRequest) -> object:
        planner = getattr(adapter, "plan", None)
        return planner(request) if callable(planner) else None

    @staticmethod
    def _run_adapter(
        adapter: Any,
        request: GenerationRequest,
        progress: ProgressCallback | None,
        cancel_event: Event | None,
    ) -> dict[str, object]:
        result = adapter.run(request, progress=progress, cancel_event=cancel_event)
        if hasattr(result, "to_dict"):
            result = result.to_dict()
        if not isinstance(result, dict):
            raise TypeError("image engine adapters must return an object")
        return dict(result)

    def run(
        self,
        request: GenerationRequest,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        mode = validate_run_mode(request.mode)
        if mode == "legacy":
            result = self._run_adapter(self.legacy_adapter, request, progress, cancel_event)
            result.setdefault("mode", "legacy")
            return result
        if mode == "v3":
            try:
                result = self._run_adapter(self.v3_adapter, request, progress, cancel_event)
                # Renderer.render reports per-file errors in the typed result
                # instead of raising.  Promote those errors here so the
                # ImageEngine owns the documented legacy fallback path.
                if result.get("failures"):
                    raise RuntimeError(f"v3 renderer reported failures: {result['failures']}")
                return result
            except Exception as error:
                # v3 failures must preserve the proven output path.
                result = self._run_adapter(
                    self.legacy_adapter,
                    replace(request, mode="legacy"),
                    progress,
                    cancel_event,
                )
                result["mode"] = "v3"
                result["fallback"] = True
                result["fallback_reason"] = str(error)
                return result

        # compare: exploratory v3 work never replaces the compatibility output.
        legacy_request = replace(request, mode="legacy")
        v3_request = replace(request, mode="v3")
        legacy_plan = self._plan_adapter(self.legacy_adapter, legacy_request)
        legacy = self._run_adapter(
            self.legacy_adapter,
            legacy_request,
            progress,
            cancel_event,
        )
        # Never let diagnostic v3 rendering touch the user's formal output
        # tree.  Adapters are allowed to write while computing metadata, so
        # give the compare leg a private temporary destination and discard it.
        compare_details: dict[str, object]
        try:
            with tempfile.TemporaryDirectory(prefix="pixelflow-compare-") as compare_root:
                v3_request = replace(v3_request, output_root=Path(compare_root))
                v3_plan = self._plan_adapter(self.v3_adapter, v3_request)
                v3 = self._run_adapter(
                    self.v3_adapter,
                    v3_request,
                    progress,
                    cancel_event,
                )
                # Capture only metadata while the private tree exists.  The
                # TemporaryDirectory is removed before the formal result is
                # returned, so compare never leaks v3 files to the user.
                compare_details = compare_output_trees(request.output_root, compare_root)
        except Exception as error:  # comparison must not break formal output
            v3_plan = None
            v3 = {"mode": "v3", "error": str(error), "fallback": True}
            legacy_tree = compare_output_trees(request.output_root, request.output_root)
            compare_details = {
                "legacy_files": legacy_tree["legacy_files"],
                "v3_files": 0,
                "compared_files": 0,
                "file_differences": [{
                    "path": "",
                    "status": "v3_error",
                    "legacy": None,
                    "v3": None,
                    "differences": ["v3_error"],
                    "error": str(error),
                }],
            }
        legacy_plan = legacy_plan if legacy_plan is not None else _plan_payload(legacy)
        v3_plan = v3_plan if v3_plan is not None else _plan_payload(v3)
        comparison = {
            "formal_output_mode": "legacy",
            "legacy_plan": legacy_plan,
            "v3_plan": v3_plan,
            "differences": _plan_differences(legacy_plan, v3_plan),
        }
        comparison.update(compare_details)
        legacy["mode"] = "compare"
        legacy["comparison"] = comparison
        return legacy


class V26EngineAdapter:
    """Transitional bridge from v3 Worker contracts to the v2.6 engine.

    The bridge keeps the proven v2.6 image behavior while the implementation is
    moved into the Qt-free v3 core in smaller, testable steps.
    """

    def __init__(self, v2_root: Path | None = None) -> None:
        self.v2_root = (v2_root or self._default_v2_root()).resolve()
        self._generate_images = None
        self._resource_root = None

    @staticmethod
    def _default_v2_root() -> Path:
        configured = os.environ.get("PIXELFLOW_V2_ROOT")
        if configured:
            return Path(configured).expanduser()
        return Path(__file__).resolve().parent / "legacy_engine"

    def _load_engine(self) -> tuple[Callable[..., dict[str, object]], Path]:
        if self._generate_images is not None and self._resource_root is not None:
            return self._generate_images, self._resource_root
        if not self.v2_root.is_dir():
            raise FileNotFoundError(f"v2.6 engine root not found: {self.v2_root}")
        root_text = str(self.v2_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        import generate_main_images as module

        configured_resource = os.environ.get("PIXELFLOW_RESOURCE_ROOT")
        if configured_resource:
            module.RESOURCE_ROOT = Path(configured_resource).expanduser()
        elif self.v2_root == Path(__file__).resolve().parent / "legacy_engine":
            module.RESOURCE_ROOT = self.v2_root / "resources"
        self._generate_images = module.generate_images
        self._resource_root = module.RESOURCE_ROOT
        return module.generate_images, module.RESOURCE_ROOT

    def plan(self, request: GenerationRequest) -> object:
        """Expose already-confirmed typed plans without re-running v2 logic."""
        return [plan.to_dict() for plan in request.composition_plans] if request.composition_plans else None

    def run(
        self,
        request: GenerationRequest,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        request.validate()
        generate_images, resource_root = self._load_engine()
        options = {
            "resource_root": resource_root,
            "selected_sizes": request.selected_sizes,
            "include_logo": request.include_logo,
            "include_vip": request.include_vip,
            "include_model_images": request.include_model_images,
            "max_size_mb": request.max_size_mb,
            "logo_overrides": {
                key: Path(value) for key, value in request.logo_overrides.items()
            },
            "category_override": request.category_override,
            "enable_category_template": request.enable_category_template,
            "folder_category_overrides": request.folder_category_overrides,
            "enable_material_understanding": request.enable_material_understanding,
            "source_type_overrides": request.source_type_overrides,
            "source_metadata_overrides": request.source_metadata_overrides,
            "source_scale_adjustments": request.source_scale_adjustments,
            "excluded_model_sources": request.excluded_model_sources,
            "include_selling_point_images": request.include_selling_point_images,
            "selling_points": request.selling_points,
            "material_confirmation_completed": request.material_confirmation_completed,
            "naming_mode": request.naming_mode,
            "local_model_enabled": request.local_model_enabled,
            "local_model_path": request.local_model_path,
            "cancel_event": cancel_event,
            "source_filter": request.source_filter,
            "progress": progress,
        }
        parameters = inspect.signature(generate_images).parameters
        if not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            # ponytail: keep the bridge compatible with pinned v2 references;
            # remove filtering after the renderer belongs to the v3 Core.
            options = {key: value for key, value in options.items() if key in parameters}
        result = generate_images(request.product_root, request.output_root, **options)
        result["output_root"] = str(request.output_root)
        result["skipped_model_sources"] = len(request.excluded_model_sources)
        return result
