from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable


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
    category_override: str | None = None
    enable_category_template: bool = False
    enable_material_understanding: bool = False
    source_type_overrides: dict[str, str] = field(default_factory=dict)
    source_metadata_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_scale_adjustments: dict[str, float] = field(default_factory=dict)
    excluded_model_sources: set[str] = field(default_factory=set)
    source_filter: set[str] | None = None
    naming_mode: str = "short"
    local_model_enabled: bool = False
    local_model_path: str | None = None
    material_confirmation_completed: bool = True

    def validate(self) -> None:
        if not self.product_root.is_dir():
            raise ValueError(f"product_root is not a directory: {self.product_root}")
        if not self.selected_sizes:
            raise ValueError("at least one output size is required")
        if not self.material_confirmation_completed:
            raise ValueError("material confirmation must complete before generation")


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
        # core.py -> pixelflow_core -> worker -> v3 -> workspace root
        return Path(__file__).resolve().parents[3] / "PixelFlow-App-v2.0"

    def _load_engine(self) -> tuple[Callable[..., dict[str, object]], Path]:
        if self._generate_images is not None and self._resource_root is not None:
            return self._generate_images, self._resource_root
        if not self.v2_root.is_dir():
            raise FileNotFoundError(f"v2.6 engine root not found: {self.v2_root}")
        root_text = str(self.v2_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        from generate_main_images import RESOURCE_ROOT, generate_images

        self._generate_images = generate_images
        self._resource_root = RESOURCE_ROOT
        return generate_images, RESOURCE_ROOT

    def run(
        self,
        request: GenerationRequest,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        request.validate()
        generate_images, resource_root = self._load_engine()
        return generate_images(
            request.product_root,
            request.output_root,
            resource_root=resource_root,
            selected_sizes=request.selected_sizes,
            include_logo=request.include_logo,
            include_vip=request.include_vip,
            include_model_images=request.include_model_images,
            category_override=request.category_override,
            enable_category_template=request.enable_category_template,
            enable_material_understanding=request.enable_material_understanding,
            source_type_overrides=request.source_type_overrides,
            source_metadata_overrides=request.source_metadata_overrides,
            source_scale_adjustments=request.source_scale_adjustments,
            excluded_model_sources=request.excluded_model_sources,
            material_confirmation_completed=request.material_confirmation_completed,
            naming_mode=request.naming_mode,
            local_model_enabled=request.local_model_enabled,
            local_model_path=request.local_model_path,
            cancel_event=cancel_event,
            source_filter=request.source_filter,
            progress=progress,
        )

