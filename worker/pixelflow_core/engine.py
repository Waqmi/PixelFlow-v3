from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event
from typing import Any, Callable

from .compare import compare_output_trees
from .contracts import (
    CompositionPlan,
    RenderAssets,
    RenderLayout,
    RenderRequest,
    RenderResult,
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
GENERATED_OUTPUT_ROOTS = frozenset({"产品主图", "模特图", "唯品专享1440", "透明产品图", "卖点图"})


def _v3_manifest_path(output_root: Path) -> Path:
    """Private record of V3-generated files, kept out of the output folder."""
    if sys.platform == "darwin":
        cache_root = Path.home() / "Library" / "Caches"
    else:
        cache_root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    key = hashlib.sha256(str(output_root.expanduser().resolve(strict=False)).encode("utf-8")).hexdigest()
    return cache_root / "PixelFlow" / "manifests" / f"v3-{key}.json"


def _read_v3_manifest(output_root: Path) -> set[str]:
    try:
        payload = json.loads(_v3_manifest_path(output_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    paths = payload.get("generated_paths", []) if isinstance(payload, dict) else []
    return {path for path in paths if isinstance(path, str)}


def _write_v3_manifest(output_root: Path, relative_paths: set[str]) -> None:
    manifest_path = _v3_manifest_path(output_root)
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps({"generated_paths": sorted(relative_paths)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # The manifest only enables stale cleanup; never fail a finished batch.
        pass


def _generated_relative_path(output_root: Path, path: Path) -> str | None:
    """Return the output-relative path only for images under a generated category."""
    try:
        relative = path.resolve(strict=False).relative_to(output_root.resolve(strict=False))
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] not in GENERATED_OUTPUT_ROOTS:
        return None
    if relative.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        return None
    return relative.as_posix()


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
        from .classification import _typed_plan_from_mapping

        return _typed_plan_from_mapping(source, size, value)

    @staticmethod
    def _group_label(source: Path) -> str:
        return V3EngineAdapter._group_label_from_name(source.parent.name)

    @classmethod
    def _short_names(cls, sources: list[Path]) -> dict[Path, str]:
        group_labels: dict[Path, str] = {}
        used_labels: dict[str, int] = {}
        for parent in sorted({source.parent for source in sources}, key=str):
            base = cls._group_label_from_name(parent.name)
            used_labels[base] = used_labels.get(base, 0) + 1
            group_labels[parent] = base if used_labels[base] == 1 else f"{base}_{used_labels[base]}"
        counters: dict[Path, int] = {}
        names: dict[Path, str] = {}
        for source in sources:
            counters[source.parent] = counters.get(source.parent, 0) + 1
            names[source] = f"{group_labels[source.parent]}_{counters[source.parent]:02d}"
        return names

    @staticmethod
    def _group_label_from_name(name: str) -> str:
        match = re.search(r"男女|男|女", name.strip())
        if match:
            color = name[match.end():].strip(" _-—·()（）[]【】")
            if color:
                return f"{match.group(0)}_{color[:12]}"
        return "素材"

    @staticmethod
    def _remove_stale_outputs(output_root: Path, expected_paths: set[Path], full_batch: bool) -> int:
        """Delete only files a previous V3 run recorded and this run no longer needs.

        Files PixelFlow never recorded (user files, outputs from builds without
        a V3 manifest) are left untouched. Retry runs never delete; they merge
        their outputs into the existing record.
        """
        previous = _read_v3_manifest(output_root)
        current = {
            relative
            for path in expected_paths
            if path.is_file() and (relative := _generated_relative_path(output_root, path))
        }
        if not full_batch:
            _write_v3_manifest(output_root, previous | current)
            return 0
        removed = 0
        kept_stale: set[str] = set()
        for relative in previous - current:
            candidate = output_root / relative
            if _generated_relative_path(output_root, candidate) != relative or not candidate.is_file():
                continue
            try:
                candidate.unlink()
            except OSError:
                kept_stale.add(relative)
                continue
            removed += 1
        _write_v3_manifest(output_root, current | kept_stale)
        return removed

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
            return {"sources": 0, "main_images": 0, "model_images": 0, "transparent_images": 0, "vip_images": 0, "render_workers": 0, "failures": [], "outputs": [], "validation": {"passed": 0, "failed": 0}, "audit": {"checked_outputs": 0}}
        timings: dict[str, float | int] = {}
        decisions = []
        # The confirmation window already sends a final type for every source
        # plus its measured metadata. Reclassifying here decodes every image a
        # second time immediately before rendering; keep the native fallback
        # for direct callers that provide only a partial override set.
        confirmed_sources = bool(sources) and all(
            any(key in request.source_type_overrides for key in (str(source), source.as_posix(), source.name))
            for source in sources
        )
        classification_started = time.perf_counter()
        if (
            request.enable_material_understanding
            or request.include_model_images
            or request.source_type_overrides
        ) and not confirmed_sources:
            decisions = V3ClassifierAdapter().classify(ClassificationRequest(
                product_root=request.product_root,
                source_filter=request.source_filter,
                overrides=request.source_type_overrides,
                category=request.category_override,
                local_model_enabled=request.local_model_enabled,
                local_model_path=request.local_model_path,
            ))
        timings["classification_seconds"] = time.perf_counter() - classification_started
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
        skipped_renders: list[dict[str, object]] = []
        reused_outputs = 0
        expected_outputs = 0
        expected_output_paths: set[Path] = set()
        main_count = model_count = transparent_count = vip_count = 0
        source_jobs: list[tuple[Path, list[tuple[RenderRequest, str, CompositionPlan | None, str, bool]]]] = []
        render_workers = 1 if len(sources) <= 1 else min(3, len(sources))
        total = 0
        completed = 0
        for source in sources:
            decision = by_source.get(str(source))
            source_type = next(
                (request.source_type_overrides.get(key) for key in (str(source), source.as_posix(), source.name) if request.source_type_overrides.get(key)),
                None,
            ) or (decision.source_type if decision else "main_product")
            is_model_source = source_type in {"model", "model_detail"}
            excluded_model = is_model_source and any(
                key in request.excluded_model_sources
                for key in (str(source), source.as_posix(), source.name)
            )
            if is_model_source and (excluded_model or not request.include_model_images):
                continue
            metadata = dict((decision.metadata if decision else {}) or {})
            metadata.update(request.source_metadata_overrides.get(str(source), {}))
            source_scale_adjustment = next(
                (
                    request.source_scale_adjustments.get(key)
                    for key in (str(source), source.as_posix(), source.name)
                    if key in request.source_scale_adjustments
                ),
                0.0,
            )
            metadata["source_scale_adjustment"] = (
                float(source_scale_adjustment) if source_type == "main_product" else 0.0
            )
            category = str(request.category_override or metadata.get("category") or "产品主图")
            group = "" if is_model_source else self._group_label(source)
            output_category = "模特图" if is_model_source else "产品主图"
            background_value = metadata.get("background")
            background = None
            if isinstance(background_value, (tuple, list)) and len(background_value) == 3:
                try:
                    background = tuple(int(value) for value in background_value)
                except (TypeError, ValueError):
                    background = None

            source_requests: list[tuple[RenderRequest, str, CompositionPlan | None, str, bool]] = []
            plans_by_size: dict[str, CompositionPlan | None] = {}
            skipped_sizes: list[str] = []
            for size in request.selected_sizes:
                if cancel_event is not None and cancel_event.is_set():
                    return {
                        "cancelled": True,
                        "sources": len(sources),
                        "main_images": main_count,
                        "model_images": model_count,
                        "transparent_images": transparent_count,
                        "vip_images": vip_count,
                        "render_workers": render_workers,
                        "failures": failures,
                        "outputs": outputs,
                        "skipped_by_scope": skipped_outputs,
                        "skipped_outputs": skipped_renders,
                        "validation": {
                            "passed": len(outputs) - skipped_outputs,
                            "failed": len(failures),
                            "skipped": skipped_outputs,
                        },
                        "audit": {"checked_outputs": len(outputs)},
                        "performance": timings,
                    }
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
                    from .classification import _typed_plans_from_metadata

                    plan = _typed_plans_from_metadata(source, metadata).get(size)
                plans_by_size[size] = plan
                filename = (
                    f"{short_names[source]}.jpg"
                    if request.naming_mode == "short"
                    else f"{source.stem}_png.jpg"
                    if source.suffix.lower() == ".png"
                    else f"{source.stem}_jpg.jpg"
                )
                output_dir = request.output_root / output_category
                if group:
                    output_dir /= group
                output_dir /= size
                if plan is not None and plan.composition_mode == "skipped":
                    # Not rendered and not expected, so a stale file from an earlier run is removed.
                    skipped_sizes.append(size)
                    continue
                for include_logo in ((True, False) if request.include_logo else (False,)):
                    logo_key = "tall_logo" if size != "1440x1440" else "logo"
                    white_logo_key = "tall_white_logo" if size != "1440x1440" else "white_logo"
                    logo_path = request.logo_overrides.get(logo_key) or request.logo_overrides.get("logo")
                    white_logo_path = request.logo_overrides.get(white_logo_key) or request.logo_overrides.get("white_logo")
                    if metadata.get("logo_luminance", 255) < 160:
                        logo_path = white_logo_path or logo_path
                    assets = RenderAssets(
                        logo=logo_path,
                        white_logo=white_logo_path,
                        border=str(metadata["border_path"]) if metadata.get("border_path") else None,
                    )
                    layout = RenderLayout(
                        category=category,
                        group=group,
                        output_category=output_category,
                        filename=filename,
                        enable_category_template=request.enable_category_template,
                        allow_mirror_extension=bool(metadata.get("allow_mirror_extension")),
                        scale=float(metadata.get("scale", 1.0) or 1.0),
                    )
                    builder = build_model_request if is_model_source else build_main_request
                    typed_request = builder(
                        str(source),
                        str(request.output_root),
                        size,
                        output_path=str(output_dir / ("含Logo" if include_logo else "无Logo") / filename),
                        mode="v3",
                        plan=plan,
                        include_logo=include_logo,
                        max_size_mb=request.max_size_mb,
                        source_type=source_type,
                        background=background,
                        assets=assets,
                        layout=layout,
                        summary=self._summary(decision, source_type),
                    )
                    typed_request = replace(
                        typed_request,
                        metadata={
                            **typed_request.metadata,
                            "source_scale_adjustment": metadata["source_scale_adjustment"],
                        },
                    )
                    if typed_request.output_path:
                        expected_output_paths.add(Path(typed_request.output_path))
                    expected_outputs += 1
                    if Path(typed_request.output_path).is_file() and not request.force_rerender:
                        outputs.append(typed_request.output_path)
                        reused_outputs += 1
                        main_count += 1
                        model_count += int(is_model_source)
                        continue
                    source_requests.append(
                        (
                            typed_request,
                            "main",
                            plan,
                            source_type,
                            bool(
                                (decision is not None and getattr(decision, "skipped_by_scope", False))
                                or (plan is not None and plan.skipped_by_scope)
                            ),
                        )
                    )

            skipped_square = "1440x1440" in skipped_sizes
            if skipped_square and request.include_vip:
                skipped_sizes.append("唯品专享1440")
            if skipped_sizes:
                skipped_plan = next(plan for plan in plans_by_size.values() if plan is not None and plan.composition_mode == "skipped")
                skipped_renders.append({
                    "source": str(source),
                    "sizes": skipped_sizes,
                    "reason": str(skipped_plan.evidence.get("skip_reason", "")),
                })
            if request.include_vip and "1440x1440" in request.selected_sizes and not skipped_square:
                vip_path = request.output_root / "唯品专享1440" / "无Logo" / filename
                vip_request = build_vip_request(
                    str(source),
                    str(request.output_root),
                    "1440x1440",
                    mode="v3",
                    plan=plans_by_size.get("1440x1440"),
                    output_path=str(vip_path),
                    max_size_mb=request.max_size_mb,
                    source_type=source_type,
                    assets=RenderAssets(border=request.logo_overrides.get("border")),
                    layout=RenderLayout(
                        category=category,
                        filename=filename,
                        enable_category_template=request.enable_category_template,
                    ),
                    summary=self._summary(decision, source_type),
                )
                vip_request = replace(
                    vip_request,
                    metadata={
                        **vip_request.metadata,
                        "source_scale_adjustment": metadata["source_scale_adjustment"],
                    },
                )
                if vip_request.output_path:
                    expected_output_paths.add(Path(vip_request.output_path))
                expected_outputs += 1
                if Path(vip_request.output_path).is_file() and not request.force_rerender:
                    outputs.append(vip_request.output_path)
                    vip_count += 1
                    reused_outputs += 1
                else:
                    source_requests.append(
                        (
                            vip_request,
                            "vip",
                            plans_by_size.get("1440x1440"),
                            source_type,
                            bool(
                                (decision is not None and getattr(decision, "skipped_by_scope", False))
                                or (plans_by_size.get("1440x1440") is not None and plans_by_size["1440x1440"].skipped_by_scope)
                            ),
                        )
                    )

            if source.suffix.lower() == ".png" and request.selected_sizes:
                for size in ("800x800", "1440x1440", "1440x1920", "1440x2160"):
                    transparent_path = request.output_root / "透明产品图" / size / f"{short_names[source]}_透明.png"
                    transparent_request = build_transparent_request(
                        str(source),
                        str(request.output_root),
                        size,
                        mode="v3",
                        plan=plans_by_size.get(size),
                        output_path=str(transparent_path),
                        max_size_mb=request.max_size_mb,
                        source_type=source_type,
                        background=background,
                        layout=RenderLayout(
                            category=category,
                            filename=transparent_path.name,
                            enable_category_template=request.enable_category_template,
                        ),
                        summary=self._summary(decision, source_type),
                    )
                    transparent_request = replace(
                        transparent_request,
                        metadata={
                            **transparent_request.metadata,
                            "source_scale_adjustment": metadata["source_scale_adjustment"],
                        },
                    )
                    if transparent_request.output_path:
                        expected_output_paths.add(Path(transparent_request.output_path))
                    expected_outputs += 1
                    if transparent_path.is_file() and not request.force_rerender:
                        outputs.append(str(transparent_path))
                        transparent_count += 1
                        reused_outputs += 1
                        continue
                    source_requests.append(
                        (
                            transparent_request,
                            "transparent",
                            plans_by_size.get(size),
                            source_type,
                            bool(
                                (decision is not None and getattr(decision, "skipped_by_scope", False))
                                or (plans_by_size.get(size) is not None and plans_by_size[size].skipped_by_scope)
                            ),
                        )
                    )
            if source_requests:
                source_jobs.append((source, source_requests))

        skipped_expected = sum(
            int(skipped)
            for _source, requests in source_jobs
            for _request, _kind, _plan, _source_type, skipped in requests
        )
        total = max(1, expected_outputs - skipped_expected)
        completed = max(0, reused_outputs)
        if progress:
            progress(completed, total, "复用输出" if completed else "准备生成")

        render_timings: dict[str, float | int] = {"source_tasks": 0}

        def render_source_job(job: tuple[Path, list[tuple[RenderRequest, str, CompositionPlan | None, str, bool]]]) -> tuple[tuple[RenderResult, ...], dict[str, float | int]]:
            source, requests = job
            typed_requests = tuple(item[0] for item in requests)
            batch = getattr(self._renderer, "render_source_batch", None)
            if callable(batch):
                batch_result = batch(typed_requests)
                if (
                    isinstance(batch_result, tuple)
                    and len(batch_result) == 2
                    and isinstance(batch_result[1], dict)
                ):
                    results, batch_timings = batch_result
                else:
                    results, batch_timings = batch_result, {}
                return tuple(results), dict(batch_timings or {})
            started = time.perf_counter()
            results = tuple(self._renderer.render(item) for item in typed_requests)
            return results, {"total_seconds": time.perf_counter() - started}

        with ThreadPoolExecutor(max_workers=render_workers, thread_name_prefix="PixelFlowV3Render") as executor:
            future_jobs = {
                executor.submit(render_source_job, job): job
                for job in source_jobs
            }
            for future in as_completed(future_jobs):
                source, source_requests = future_jobs[future]
                try:
                    results, batch_timings = future.result()
                except Exception as error:  # noqa: BLE001 - retain one-source failure records
                    results = tuple(
                        RenderResult(source=str(source), outputs=(), success=False, mode="v3", errors=(str(error),))
                        for _request, _kind, _plan, _source_type, _skipped in source_requests
                    )
                    batch_timings = {"source_tasks": 1}
                for key, value in batch_timings.items():
                    if key == "source_tasks":
                        continue
                    if isinstance(value, (int, float)):
                        render_timings[key] = render_timings.get(key, 0) + value
                render_timings["source_tasks"] = render_timings.get("source_tasks", 0) + 1
                for result, (_typed_request, kind, plan, source_type, skipped) in zip(results, source_requests):
                    size = str(result.metadata.get("size") or _typed_request.size)
                    result_metadata = dict(getattr(result, "metadata", {}) or {})
                    if result_metadata.get("requires_review"):
                        item = review_items.setdefault(str(source), {"source": str(source), "sizes": [], "review_hint": "", "protected_regions": []})
                        item["sizes"].append(size)  # type: ignore[union-attr]
                        item["review_hint"] = result_metadata.get("review_hint") or item["review_hint"]
                        item["protected_regions"] = [region.to_dict() for region in (plan.protected_regions if plan else ())]
                    if result_metadata.get("fallback"):
                        item = fallback_items.setdefault(str(source), {"source": str(source), "sizes": [], "fallback_reason": ""})
                        item["sizes"].append(size)  # type: ignore[union-attr]
                        item["fallback_reason"] = result_metadata.get("fallback_reason") or item["fallback_reason"]
                    if result.success:
                        outputs.extend(result.outputs)
                        if skipped:
                            skipped_outputs += len(result.outputs)
                        if result.outputs and not skipped:
                            completed += len(result.outputs)
                            if progress:
                                phase = {
                                    "vip": "唯品图",
                                    "transparent": "透明图",
                                }.get(kind, "生成")
                                progress(completed, total, phase)
                        if kind == "main":
                            main_count += 1
                            model_count += int(source_type in {"model", "model_detail"})
                        elif kind == "vip":
                            vip_count += 1
                        else:
                            transparent_count += 1
                    else:
                        failures.append({"source": str(source), "message": "; ".join(result.errors)})
        # Clean up only after rendering, so a failed render never loses old files first.
        stale_outputs_removed = self._remove_stale_outputs(
            request.output_root,
            expected_output_paths,
            full_batch=request.source_filter is None,
        )
        timings.update(render_timings)
        timings["total_seconds"] = sum(
            float(timings.get(key, 0.0) or 0.0)
            for key in ("classification_seconds", "decode_seconds", "analysis_seconds", "compose_seconds", "overlay_seconds", "encode_seconds", "write_seconds")
        )
        return {
            "sources": len(sources),
            "main_images": main_count,
            "model_images": model_count,
            "transparent_images": transparent_count,
            "vip_images": vip_count,
            "render_workers": render_workers,
            "failures": failures,
            "outputs": outputs,
            "retried_only": request.source_filter is not None,
            "skipped_by_scope": skipped_outputs,
            "skipped_outputs": skipped_renders,
            "incremental_reused": reused_outputs,
            "incremental_skipped": bool(expected_outputs and reused_outputs == expected_outputs and not failures),
            "stale_outputs_removed": stale_outputs_removed,
            "requires_review": sorted(review_items.values(), key=lambda item: str(item["source"])),
            "requires_review_count": len(review_items),
            "fallback_items": sorted(fallback_items.values(), key=lambda item: str(item["source"])),
            "fallback_count": len(fallback_items),
            "validation": {"passed": len(outputs) - skipped_outputs, "failed": len(failures), "skipped": skipped_outputs},
            "audit": {"checked_outputs": len(outputs)},
            "performance": timings,
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
        raw_outputs = result.get("outputs", ())
        output_paths = (
            sorted(Path(str(value)) for value in raw_outputs if value)
            if isinstance(raw_outputs, (list, tuple, set))
            else []
        )
        if not output_paths:
            # Compatibility seam for injected runners that only report counts
            # and write files directly into the output tree.
            output_paths = sorted(
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
            )
        for path in output_paths:
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
