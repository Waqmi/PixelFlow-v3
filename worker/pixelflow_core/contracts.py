from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


PROTOCOL_VERSION = 1
RUN_MODES = ("legacy", "v3", "compare")
RunMode = Literal["legacy", "v3", "compare"]
OutputKind = Literal["main", "model", "transparent", "vip"]


class ProtocolError(ValueError):
    """Raised when a Worker message violates the versioned contract."""


def validate_run_mode(mode: str | None) -> RunMode:
    """Return a supported image-engine mode or reject an ambiguous request."""
    value = "legacy" if mode is None else str(mode).strip().lower()
    if value not in RUN_MODES:
        raise ValueError(f"unsupported image engine mode: {mode!r}")
    return value  # type: ignore[return-value]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: _json_safe(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_json_safe(item) for item in value]
    return value


def _box(value: Any, field_name: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValueError(f"{field_name} must contain four numbers")
    values = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in values):
        raise ValueError(f"{field_name} must contain finite numbers")
    if values[2] < values[0] or values[3] < values[1]:
        raise ValueError(f"{field_name} must be ordered left, top, right, bottom")
    return values


@dataclass(frozen=True)
class ClassificationResult:
    """Stable classification output shared by legacy and v3 adapters."""

    source: str
    source_type: str
    confidence: float = 0.0
    classifier: str = "rules"
    reason: str = ""
    metadata: dict[str, Any] | None = None
    risk_flags: tuple[str, ...] = ()
    requires_review: bool = False

    def __post_init__(self) -> None:
        if not str(self.source).strip():
            raise ValueError("source must be a non-empty string")
        if not str(self.source_type).strip():
            raise ValueError("source_type must be a non-empty string")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between zero and one")
        if self.metadata is not None:
            object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "risk_flags", tuple(str(item) for item in self.risk_flags))

    @property
    def material_type(self) -> str:
        return self.source_type

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class ProtectedRegion:
    """A source-image region that a composition must keep visible."""

    kind: str
    bbox: tuple[float, float, float, float]
    source: str = "inference"
    required: bool = True
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not str(self.kind).strip():
            raise ValueError("protected region kind must be non-empty")
        object.__setattr__(self, "bbox", _box(self.bbox, "bbox"))
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("protected region confidence must be between zero and one")

    @property
    def box(self) -> tuple[float, float, float, float]:
        return self.bbox

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class CompositionPlan:
    """Typed crop/position plan produced before rendering one output size."""

    source: str
    size: str
    composition_mode: str = "fallback"
    scale: float = 1.0
    position_x: float = 0.5
    position_y: float = 0.5
    crop_ratio: float | None = None
    protected_regions: tuple[ProtectedRegion, ...] = ()
    risk_flags: tuple[str, ...] = ()
    requires_review: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)
    fallback: bool = False
    review_hint: str = ""
    fallback_reason: str = ""
    skipped_by_scope: bool = False

    def __post_init__(self) -> None:
        if not str(self.source).strip():
            raise ValueError("composition source must be a non-empty string")
        if not str(self.size).strip():
            raise ValueError("composition size must be a non-empty string")
        if float(self.scale) <= 0:
            raise ValueError("composition scale must be greater than zero")
        if not 0.0 <= float(self.position_x) <= 1.0:
            raise ValueError("position_x must be between zero and one")
        if not 0.0 <= float(self.position_y) <= 1.0:
            raise ValueError("position_y must be between zero and one")
        if self.crop_ratio is not None and not 0.0 < float(self.crop_ratio) <= 1.0:
            raise ValueError("crop_ratio must be greater than zero and at most one")
        object.__setattr__(self, "protected_regions", tuple(self.protected_regions))
        object.__setattr__(self, "risk_flags", tuple(str(item) for item in self.risk_flags))
        object.__setattr__(self, "evidence", dict(self.evidence))
        object.__setattr__(self, "review_hint", str(self.review_hint or ""))
        object.__setattr__(self, "fallback_reason", str(self.fallback_reason or ""))

    @property
    def target_crop_height_ratio(self) -> float | None:
        return self.crop_ratio

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class RenderAssets:
    """Serializable image assets selected before one v3 render."""

    logo: str | None = None
    white_logo: str | None = None
    border: str | None = None


@dataclass(frozen=True)
class RenderLayout:
    """Stable layout hints used by the v3 renderer.

    These values used to travel in the open-ended ``RenderRequest.metadata``
    mapping.  Keep the mapping as a compatibility escape hatch, but make the
    normal v3 path explicit and serializable.
    """

    category: str = "产品主图"
    group: str = ""
    output_category: str = "产品主图"
    filename: str | None = None
    enable_category_template: bool = False
    allow_mirror_extension: bool = False
    scale: float = 1.0
    add_border: bool = False

    def __post_init__(self) -> None:
        if not str(self.category).strip():
            raise ValueError("layout category must be a non-empty string")
        if not str(self.output_category).strip():
            raise ValueError("layout output_category must be a non-empty string")
        if float(self.scale) <= 0:
            raise ValueError("layout scale must be greater than zero")

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class RenderSummary:
    """Stable evidence summary attached to one output request/result."""

    source_type: str = "main_product"
    classifier: str = ""
    confidence: float = 0.0
    classification_reason: str = ""
    pose_evidence: str = "not_run"
    segmentation_evidence: str = "not_run"
    risk_flags: tuple[str, ...] = ()
    requires_review: bool = False
    review_hint: str = ""
    fallback: bool = False
    fallback_reason: str = ""
    skipped_by_scope: bool = False

    def __post_init__(self) -> None:
        if not str(self.source_type).strip():
            raise ValueError("summary source_type must be a non-empty string")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("summary confidence must be between zero and one")
        object.__setattr__(self, "risk_flags", tuple(str(item) for item in self.risk_flags))

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class RenderRequest:
    """Explicit renderer input; adapters may add legacy fields in metadata.

    The fields after ``metadata`` are the stable v3 output contract.  Keeping
    them after the legacy fields preserves positional construction used by
    the v2 bridge while callers migrate away from dynamic metadata.
    """

    source: str
    output_root: str
    size: str
    output_kind: str = "main"
    mode: RunMode = "legacy"
    plan: CompositionPlan | None = None
    include_logo: bool = True
    include_vip: bool = False
    transparent: bool = False
    max_size_mb: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    output_path: str | None = None
    source_type: str = "main_product"
    logo: str | None = None
    border: str | None = None
    background: tuple[int, int, int] | None = None
    size_limit: float | None = None
    assets: RenderAssets = field(default_factory=RenderAssets)
    layout: RenderLayout = field(default_factory=RenderLayout)
    validate_only: bool = False
    summary: RenderSummary | None = None

    @property
    def logo_path(self) -> str | None:
        """Compatibility alias used by renderer implementations."""
        return self.logo

    @property
    def border_path(self) -> str | None:
        """Compatibility alias used by renderer implementations."""
        return self.border

    @property
    def size_limit_mb(self) -> float | None:
        """Return the explicit limit, falling back to the legacy field."""
        return self.size_limit if self.size_limit is not None else self.max_size_mb

    def validate(self) -> None:
        validate_run_mode(self.mode)
        if not str(self.source).strip() or not str(self.output_root).strip():
            raise ValueError("source and output_root are required")
        if not str(self.size).strip():
            raise ValueError("size is required")
        if self.max_size_mb is not None and float(self.max_size_mb) <= 0:
            raise ValueError("max_size_mb must be greater than zero")
        if self.size_limit is not None and float(self.size_limit) <= 0:
            raise ValueError("size_limit must be greater than zero")
        if self.output_path is not None and not str(self.output_path).strip():
            raise ValueError("output_path must be a non-empty string")
        if not str(self.source_type).strip():
            raise ValueError("source_type must be a non-empty string")
        if self.background is not None:
            try:
                channels = tuple(int(value) for value in self.background)
            except (TypeError, ValueError):
                raise ValueError("background must contain three channel values between zero and 255") from None
            if len(channels) != 3 or any(not 0 <= value <= 255 for value in channels):
                raise ValueError("background must contain three channel values between zero and 255")

    @property
    def source_path(self) -> str:
        return self.source

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


def _render_request(
    source: str,
    output_root: str,
    size: str,
    *,
    output_kind: OutputKind,
    mode: RunMode = "v3",
    output_path: str | None = None,
    plan: CompositionPlan | None = None,
    include_logo: bool = True,
    include_vip: bool = False,
    transparent: bool = False,
    max_size_mb: float | None = None,
    source_type: str = "main_product",
    background: tuple[int, int, int] | None = None,
    size_limit: float | None = None,
    assets: RenderAssets | None = None,
    layout: RenderLayout | None = None,
    validate_only: bool = False,
    summary: RenderSummary | None = None,
) -> RenderRequest:
    chosen_assets = assets or RenderAssets()
    chosen_layout = layout or RenderLayout()
    return RenderRequest(
        source=str(source),
        output_root=str(output_root),
        size=str(size),
        output_kind=output_kind,
        mode=mode,
        plan=plan,
        include_logo=include_logo,
        include_vip=include_vip,
        transparent=transparent,
        max_size_mb=max_size_mb,
        output_path=str(output_path) if output_path is not None else None,
        source_type=source_type,
        logo=chosen_assets.logo,
        border=chosen_assets.border,
        background=background,
        size_limit=size_limit if size_limit is not None else max_size_mb,
        assets=chosen_assets,
        layout=chosen_layout,
        validate_only=validate_only,
        summary=summary,
    )


def build_main_request(
    source: str,
    output_root: str,
    size: str,
    *,
    output_path: str | None = None,
    mode: RunMode = "v3",
    plan: CompositionPlan | None = None,
    include_logo: bool = True,
    max_size_mb: float | None = None,
    source_type: str = "main_product",
    background: tuple[int, int, int] | None = None,
    assets: RenderAssets | None = None,
    layout: RenderLayout | None = None,
    validate_only: bool = False,
    summary: RenderSummary | None = None,
) -> RenderRequest:
    """Build one typed product-main or model-image request."""
    return _render_request(
        source,
        output_root,
        size,
        output_kind="model" if source_type in {"model", "model_detail"} else "main",
        mode=mode,
        output_path=output_path,
        plan=plan,
        include_logo=include_logo,
        max_size_mb=max_size_mb,
        source_type=source_type,
        background=background,
        assets=assets,
        layout=layout,
        validate_only=validate_only,
        summary=summary,
    )


def build_model_request(
    source: str,
    output_root: str,
    size: str,
    *,
    output_path: str | None = None,
    mode: RunMode = "v3",
    plan: CompositionPlan | None = None,
    include_logo: bool = True,
    max_size_mb: float | None = None,
    source_type: str = "model",
    background: tuple[int, int, int] | None = None,
    assets: RenderAssets | None = None,
    layout: RenderLayout | None = None,
    validate_only: bool = False,
    summary: RenderSummary | None = None,
) -> RenderRequest:
    """Build one typed model-image request."""
    return _render_request(
        source,
        output_root,
        size,
        output_kind="model",
        mode=mode,
        output_path=output_path,
        plan=plan,
        include_logo=include_logo,
        max_size_mb=max_size_mb,
        source_type=source_type,
        background=background,
        assets=assets,
        layout=layout,
        validate_only=validate_only,
        summary=summary,
    )


def build_transparent_request(
    source: str,
    output_root: str,
    size: str,
    *,
    output_path: str | None = None,
    mode: RunMode = "v3",
    plan: CompositionPlan | None = None,
    max_size_mb: float | None = None,
    source_type: str = "main_product",
    background: tuple[int, int, int] | None = None,
    assets: RenderAssets | None = None,
    layout: RenderLayout | None = None,
    validate_only: bool = False,
    summary: RenderSummary | None = None,
) -> RenderRequest:
    """Build one independent RGBA request from the source image."""
    return _render_request(
        source,
        output_root,
        size,
        output_kind="transparent",
        mode=mode,
        output_path=output_path,
        plan=plan,
        include_logo=False,
        transparent=True,
        max_size_mb=max_size_mb,
        source_type=source_type,
        background=background,
        assets=assets,
        layout=layout,
        validate_only=validate_only,
        summary=summary,
    )


def build_vip_request(
    source: str,
    output_root: str,
    size: str = "1440x1440",
    *,
    output_path: str | None = None,
    mode: RunMode = "v3",
    plan: CompositionPlan | None = None,
    max_size_mb: float | None = None,
    source_type: str = "main_product",
    background: tuple[int, int, int] | None = None,
    assets: RenderAssets | None = None,
    layout: RenderLayout | None = None,
    validate_only: bool = False,
    summary: RenderSummary | None = None,
) -> RenderRequest:
    """Build one independent no-logo VIP request from the source image."""
    return _render_request(
        source,
        output_root,
        size,
        output_kind="vip",
        mode=mode,
        output_path=output_path,
        plan=plan,
        include_logo=False,
        include_vip=True,
        max_size_mb=max_size_mb,
        source_type=source_type,
        background=background,
        assets=assets,
        layout=layout,
        validate_only=validate_only,
        summary=summary,
    )


@dataclass(frozen=True)
class RenderResult:
    """Stable renderer output that can cross the Worker JSON boundary."""

    source: str
    outputs: tuple[str, ...] = ()
    success: bool = True
    mode: RunMode = "legacy"
    errors: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_run_mode(self.mode)
        object.__setattr__(self, "outputs", tuple(str(item) for item in self.outputs))
        object.__setattr__(self, "errors", tuple(str(item) for item in self.errors))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(self)


@dataclass(frozen=True)
class Message:
    protocol: int
    request_id: str
    type: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "protocol": self.protocol,
                "request_id": self.request_id,
                "type": self.type,
                "payload": self.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> "Message":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProtocolError(f"invalid JSON: {error.msg}") from error
        if not isinstance(data, dict):
            raise ProtocolError("message must be an object")
        if data.get("protocol") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported protocol version")
        request_id = data.get("request_id")
        message_type = data.get("type")
        payload = data.get("payload")
        if not isinstance(request_id, str) or not request_id:
            raise ProtocolError("request_id must be a non-empty string")
        if not isinstance(message_type, str) or not message_type:
            raise ProtocolError("type must be a non-empty string")
        if not isinstance(payload, dict):
            raise ProtocolError("payload must be an object")
        return cls(PROTOCOL_VERSION, request_id, message_type, payload)
