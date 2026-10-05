from __future__ import annotations

import json
import os
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import shutil
import sys
import threading
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .contracts import Message, ProtocolError, PROTOCOL_VERSION, validate_run_mode
from .classification import (
    ClassificationCancelled,
    ClassificationDecision,
    ClassificationRequest,
    V26ClassifierAdapter,
    _material_sources,
    classification_summary,
)
from .engine import GenerationRequest, ImageEngine, V26EngineAdapter, V3EngineAdapter


WORKER_VERSION = os.environ.get("PIXELFLOW_WORKER_VERSION", "3.0.0-beta.1")
RULES_VERSION = "v3-composition-11"


def _worker_build_hash() -> str:
    configured = os.environ.get("PIXELFLOW_BUILD_HASH")
    if configured:
        return configured
    try:
        import hashlib

        target = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return "unknown"


BUILD_HASH = _worker_build_hash()


_PREVIEW_CACHE_LIMIT = 128
_preview_cache: OrderedDict[tuple[str, int, int, int, int], dict[str, str]] = OrderedDict()
_preview_cache_lock = threading.Lock()


SUPPORTED_REQUESTS = {
    "ping",
    "probe_models",
    "classify_materials",
    "start_generation",
    "cancel_generation",
    "retry_failed",
    "clear_cache",
    "read_preview",
    "shutdown",
}


def _event(request_id: str, event_type: str, payload: dict) -> str:
    return Message(PROTOCOL_VERSION, request_id, event_type, _json_safe(payload)).to_json()


def _ready_payload() -> dict[str, Any]:
    return {
        "worker": "pixelflow-v3",
        "protocol": PROTOCOL_VERSION,
        "worker_version": WORKER_VERSION,
        "rules_version": RULES_VERSION,
        "build_hash": BUILD_HASH,
    }


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _generation_request(payload: dict[str, Any]) -> GenerationRequest:
    def paths_map(name: str) -> dict[str, Any]:
        value = payload.get(name, {})
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be an object")
        return value

    selected_sizes = payload.get("selected_sizes", [])
    if not isinstance(selected_sizes, (list, tuple)):
        raise ValueError("selected_sizes must be an array")
    selling_points = payload.get("selling_points", [])
    if not isinstance(selling_points, (list, tuple)):
        raise ValueError("selling_points must be an array")
    source_filter = payload.get("source_filter")
    if source_filter is not None and not isinstance(source_filter, (list, tuple, set)):
        raise ValueError("source_filter must be an array")
    max_size_mb = payload.get("max_size_mb")
    if max_size_mb is not None:
        max_size_mb = float(max_size_mb)
    raw_plans = payload.get("composition_plans", [])
    if not isinstance(raw_plans, (list, tuple)):
        raise ValueError("composition_plans must be an array")
    composition_plans = []
    for value in raw_plans:
        if not isinstance(value, dict):
            raise ValueError("each composition plan must be an object")
        source = Path(str(value.get("source", "")))
        size = str(value.get("size", ""))
        plan = V3EngineAdapter._plan(value, source, size)
        if plan is None:
            raise ValueError("invalid composition plan")
        composition_plans.append(plan)
    return GenerationRequest(
        product_root=Path(str(payload.get("product_root", ""))).expanduser(),
        output_root=Path(str(payload.get("output_root", ""))).expanduser(),
        selected_sizes=tuple(str(item) for item in selected_sizes),
        include_logo=bool(payload.get("include_logo", True)),
        include_vip=bool(payload.get("include_vip", True)),
        include_model_images=bool(payload.get("include_model_images", True)),
        max_size_mb=max_size_mb,
        logo_overrides={str(key): str(value) for key, value in paths_map("logo_overrides").items()},
        category_override=payload.get("category_override"),
        enable_category_template=bool(payload.get("enable_category_template", False)),
        folder_category_overrides={
            str(key): str(value) for key, value in paths_map("folder_category_overrides").items()
        },
        enable_material_understanding=bool(payload.get("enable_material_understanding", False)),
        source_type_overrides={str(key): str(value) for key, value in paths_map("source_type_overrides").items()},
        source_metadata_overrides=paths_map("source_metadata_overrides"),
        source_scale_adjustments={str(key): float(value) for key, value in paths_map("source_scale_adjustments").items()},
        excluded_model_sources={str(item) for item in payload.get("excluded_model_sources", [])},
        include_selling_point_images=bool(payload.get("include_selling_point_images", False)),
        selling_points=tuple(str(item) for item in selling_points),
        source_filter={str(item) for item in source_filter} if source_filter is not None else None,
        force_rerender=bool(payload.get("force_rerender", False)),
        naming_mode=str(payload.get("naming_mode", "short")),
        local_model_enabled=bool(payload.get("local_model_enabled", False)),
        local_model_path=str(payload["local_model_path"]) if payload.get("local_model_path") else None,
        material_confirmation_completed=bool(payload.get("material_confirmation_completed", True)),
        composition_plans=tuple(composition_plans),
        request_id=str(payload.get("_request_id", "")),
        mode=validate_run_mode(
            payload.get(
                "mode",
                payload.get("run_mode", os.environ.get("PIXELFLOW_ENGINE_MODE", "v3")),
            )
        ),
    )


def _classification_request(payload: dict[str, Any]) -> ClassificationRequest:
    source_filter = payload.get("source_filter")
    if source_filter is not None and not isinstance(source_filter, (list, tuple, set)):
        raise ValueError("source_filter must be an array")
    overrides = payload.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be an object")
    return ClassificationRequest(
        product_root=Path(str(payload.get("product_root", ""))).expanduser(),
        source_filter={str(item) for item in source_filter} if source_filter is not None else None,
        overrides={str(key): str(value) for key, value in overrides.items()},
        workers=int(payload.get("workers", 1) or 1),
        category=str(payload["category"]) if payload.get("category") else None,
        local_model_enabled=bool(payload.get("local_model_enabled", False)),
        local_model_path=str(payload["local_model_path"]) if payload.get("local_model_path") else None,
        output_root=Path(str(payload["output_root"])).expanduser() if payload.get("output_root") else None,
    )


def _preflight_materials(
    request: ClassificationRequest,
    sources: tuple[Path, ...] | list[Path] | None = None,
) -> list[str]:
    from PIL import Image

    sources = tuple(sources) if sources is not None else tuple(_material_sources(request.product_root))
    if not sources:
        raise ValueError("文件夹内没有 JPG、JPEG 或 PNG 图片")
    unreadable: list[str] = []
    oversized: list[str] = []
    for source in sources:
        try:
            with Image.open(source) as image:
                if image.width * image.height > 50_000_000:
                    oversized.append(source.name)
        except Exception:
            unreadable.append(source.name)
    warnings = []
    if unreadable:
        warnings.append(f"无法读取 {len(unreadable)} 张图片（例如：{', '.join(unreadable[:3])}）。")
    if oversized:
        warnings.append(f"有 {len(oversized)} 张图片超过 5000 万像素，处理时可能占用较多内存。")
    if request.output_root is not None:
        output = request.output_root
        product_root = request.product_root.expanduser().resolve()
        output_resolved = output.expanduser().resolve(strict=False)
        if output_resolved == product_root or product_root in output_resolved.parents:
            raise ValueError("输出目录不能与输入目录相同，也不能位于输入目录内")
        if output.exists() and not output.is_dir():
            raise ValueError(f"输出位置不是文件夹：{output}")
        parent = output
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not os.access(parent, os.W_OK):
            raise ValueError(f"输出位置不可写：{output}")
        if shutil.disk_usage(parent).free < 200 * 1024 * 1024:
            warnings.append("输出磁盘可用空间低于 200 MB，批量生成可能中途失败。")
    return warnings


def _preview_roots(payload: dict[str, Any]) -> tuple[Path, ...]:
    raw_roots = payload.get("allowed_roots")
    if raw_roots is None and payload.get("allowed_root") is not None:
        raw_roots = [payload["allowed_root"]]
    if isinstance(raw_roots, str):
        raw_roots = [raw_roots]
    if not isinstance(raw_roots, (list, tuple)):
        raise ValueError("preview scope is required")
    roots: list[Path] = []
    for value in raw_roots:
        if not isinstance(value, str) or not value.strip():
            continue
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("preview scope must be a directory")
        roots.append(root)
    if not roots:
        raise ValueError("preview scope is required")
    return tuple(roots)


def _validate_preview_path(payload: dict[str, Any]) -> Path:
    path = Path(str(payload.get("path", ""))).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("preview file is missing")
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("preview file type is not supported")
    roots = _preview_roots(payload)
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("preview path is outside the allowed scope")
    return path


def _read_preview(payload: dict[str, Any], require_scope: bool = False) -> dict[str, str]:
    from PIL import Image, ImageOps

    path = _validate_preview_path(payload) if require_scope else Path(str(payload.get("path", ""))).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("preview file is missing")
    max_width = max(1, min(198, int(payload.get("max_width", 198) or 198)))
    max_height = max(1, min(138, int(payload.get("max_height", 138) or 138)))
    stat = path.stat()
    cache_key = (str(path), stat.st_mtime_ns, stat.st_size, max_width, max_height)
    with _preview_cache_lock:
        cached = _preview_cache.get(cache_key)
        if cached is not None:
            _preview_cache.move_to_end(cache_key)
            return dict(cached)
    with Image.open(path) as source:
        # Ask JPEG decoders for a reduced decode before EXIF correction. This
        # avoids materializing multi-megapixel originals just to make a card
        # thumbnail. PNG remains on the normal decoder path.
        if source.format in {"JPEG", "MPO"}:
            try:
                source.draft("RGB", (max_width, max_height))
            except Exception:  # noqa: BLE001 - decoder-specific optimization
                pass
        try:
            image = ImageOps.exif_transpose(source)
        except Exception:  # noqa: BLE001 - keep preview usable for malformed EXIF
            image = source.copy()
        try:
            image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        except Exception:  # noqa: BLE001 - match v2.6 fallback to the decoded source image
            image = source.copy()
        output = BytesIO()
        if path.suffix.lower() == ".png":
            image.save(output, format="PNG", optimize=True)
            mime_type = "image/png"
        else:
            image.convert("RGB").save(output, format="JPEG", quality=85, optimize=True)
            mime_type = "image/jpeg"
    result = {"mime_type": mime_type, "data": base64.b64encode(output.getvalue()).decode("ascii")}
    with _preview_cache_lock:
        _preview_cache[cache_key] = result
        _preview_cache.move_to_end(cache_key)
        while len(_preview_cache) > _PREVIEW_CACHE_LIMIT:
            _preview_cache.popitem(last=False)
    return dict(result)


class WorkerRuntime:
    """Own one asynchronous job and its cancellation event."""

    def __init__(
        self,
        adapter: V26EngineAdapter | None = None,
        classifier: V26ClassifierAdapter | None = None,
        emit: Callable[[str, str, dict[str, Any]], None] | None = None,
        v3_adapter: V3EngineAdapter | Any | None = None,
    ) -> None:
        self.adapter = adapter or V26EngineAdapter()
        # Injected adapters in unit/process tests are intentional seams. Keep
        # them on the v3 leg unless the production default is in use.
        self.v3_adapter = v3_adapter or (
            self.adapter if adapter is not None and not isinstance(self.adapter, V26EngineAdapter) else V3EngineAdapter()
        )
        self.engine = ImageEngine(self.adapter, self.v3_adapter)
        self.classifier = classifier or V26ClassifierAdapter(getattr(self.adapter, "v2_root", None))
        self._emit = emit or (lambda _request_id, _event_type, _payload: None)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._request_id = ""

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, request_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            if self.busy:
                raise RuntimeError("a generation job is already running")
            request_payload = dict(payload)
            request_payload["_request_id"] = request_id
            request = _generation_request(request_payload)
            request.validate()
            self._request_id = request_id
            self._cancel_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(request_id, request, self._cancel_event),
                daemon=True,
                name="pixelflow-generation",
            )
            self._thread.start()

    def start_classification(self, request_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            if self.busy:
                raise RuntimeError("a job is already running")
            request = _classification_request(payload)
            request.validate()
            self._request_id = request_id
            self._cancel_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run_classification,
                args=(request_id, request, self._cancel_event),
                daemon=True,
                name="pixelflow-classification",
            )
            self._thread.start()

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            if not self.busy or self._cancel_event is None:
                return False
            self._cancel_event.set()
            self._emit(request_id, "status", {"state": "cancelling", "job_id": self._request_id})
            return True

    def shutdown(self, timeout: float = 10.0) -> bool:
        """Request cancellation and wait before the process exits."""
        with self._lock:
            thread = self._thread
            if self._cancel_event is not None:
                self._cancel_event.set()
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
        return not self.busy

    def probe_models(self, model_root: str) -> dict[str, dict[str, str]]:
        """Probe optional models without starting inference when none exist."""
        from .model_service import get_model_process_service

        return get_model_process_service().probe(Path(model_root).expanduser())

    def clear_cache(self) -> dict[str, int]:
        if self.busy:
            raise RuntimeError("请等待当前任务结束后再清理缓存")
        from .legacy_engine.generate_main_images import clear_manifest_cache
        from .model_cache import clear_model_result_cache
        from .model_service import get_model_process_service

        result = {
            "manifest": clear_manifest_cache(),
            "model_results": clear_model_result_cache(),
        }
        with _preview_cache_lock:
            _preview_cache.clear()
        get_model_process_service().close()
        return result

    def _run(self, request_id: str, request: GenerationRequest, cancel_event: threading.Event) -> None:
        def report(completed: int, total: int, phase: str) -> None:
            self._emit(
                request_id,
                "progress",
                {"completed": completed, "total": total, "phase": phase},
            )

        try:
            result = self.engine.run(request, progress=report, cancel_event=cancel_event)
            event_type = "cancelled" if bool(result.get("cancelled")) else "done"
            self._emit(request_id, event_type, {"result": result})
        except Exception as error:  # worker boundary must report failures, not die silently
            self._emit(request_id, "error", {"code": "generation_failed", "message": str(error)})
        finally:
            with self._lock:
                self._thread = None
                self._cancel_event = None
                self._request_id = ""

    def _run_classification(
        self,
        request_id: str,
        request: ClassificationRequest,
        cancel_event: threading.Event,
    ) -> None:
        def report(
            completed: int,
            total: int,
            source: Path,
            decision: ClassificationDecision,
        ) -> None:
            if cancel_event.is_set():
                raise ClassificationCancelled()
            self._emit(
                request_id,
                "progress",
                {
                    "completed": completed,
                    "total": total,
                    "phase": "规则分类",
                    "source": source,
                    "decision": decision,
                },
            )

        def report_stage(stage: str, completed: int, total: int, source: Path) -> None:
            if cancel_event.is_set():
                raise ClassificationCancelled()
            self._emit(
                request_id,
                "progress",
                {
                    "completed": completed,
                    "total": total,
                    "phase": stage,
                    "source": source,
                },
            )

        try:
            # Collect the source list once and share it with preflight and the
            # classifier.  The old flow walked the input tree twice.
            sources = tuple(_material_sources(request.product_root))
            warnings = _preflight_materials(request, sources) if request.output_root else []
            category_decision = None
            v2_root = getattr(self.classifier, "v2_root", None)
            if v2_root is not None:
                from .legacy_engine.category_engine.category_classifier import RuleCategoryClassifier

                category_decision = RuleCategoryClassifier().classify(
                    request.product_root,
                    request.category,
                )
            classified_request = replace(
                request,
                sources=sources,
                category=category_decision.category if category_decision is not None else request.category,
                cancel_event=cancel_event,
                stage_progress=report_stage,
            )
            decisions = self.classifier.classify(
                classified_request,
                progress=report,
            )
            if cancel_event.is_set():
                self._emit(request_id, "cancelled", {"phase": "分类"})
            else:
                result: dict[str, Any] = {
                    "decisions": decisions,
                    "preflight_warnings": warnings,
                    "summary": classification_summary(decisions),
                }
                if v2_root is not None:
                    from .legacy_engine.local_model_assistant import category_compatibility

                    category = category_decision
                    if category is None:
                        from .legacy_engine.category_engine.category_classifier import RuleCategoryClassifier

                        category = RuleCategoryClassifier().classify(
                            request.product_root,
                            request.category,
                        )
                    result["category"] = asdict(category)
                    result["suggested_exclusions"] = [
                        decision.source
                        for decision in decisions
                        if decision.source_type in {"model", "model_detail"}
                        and isinstance(decision.metadata, dict)
                        and isinstance(decision.metadata.get("body_visibility"), dict)
                        and category_compatibility(category.category, decision.metadata["body_visibility"])[0] == "incompatible"
                    ]
                self._emit(request_id, "done", {"result": result})
        except ClassificationCancelled:
            self._emit(request_id, "cancelled", {"phase": "分类"})
        except Exception as error:  # worker boundary must report failures, not die silently
            self._emit(request_id, "error", {"code": "classification_failed", "message": str(error)})
        finally:
            with self._lock:
                self._thread = None
                self._cancel_event = None
                self._request_id = ""


def handle_message(message: Message) -> tuple[str, bool]:
    """Handle synchronous protocol commands kept for small clients/tests."""
    if message.type not in SUPPORTED_REQUESTS:
        return _event(message.request_id, "error", {"code": "unknown_request", "message": message.type}), False
    if message.type == "shutdown":
        return _event(message.request_id, "done", {"action": "shutdown"}), True
    if message.type == "ping":
        return _event(message.request_id, "ready", _ready_payload()), False
    return _event(
        message.request_id,
        "status",
        {"state": "scaffold", "message": "v2.6 core integration is pending baseline completion"},
    ), False


def main() -> int:
    output_lock = threading.Lock()
    preview_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pixelflow-preview")

    def emit(request_id: str, event_type: str, payload: dict[str, Any]) -> None:
        line = _event(request_id, event_type, payload)
        with output_lock:
            print(line, flush=True)

    runtime = WorkerRuntime(emit=emit)
    emit("worker", "ready", _ready_payload())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = Message.from_json(line)
            should_exit = False
            response: str | None = None
            if message.type == "start_generation":
                runtime.start(message.request_id, message.payload)
                response = _event(message.request_id, "status", {"state": "started"})
            elif message.type == "classify_materials":
                runtime.start_classification(message.request_id, message.payload)
                response = _event(message.request_id, "status", {"state": "started", "phase": "分类"})
            elif message.type == "cancel_generation":
                cancelled = runtime.cancel(message.request_id)
                response = _event(
                    message.request_id,
                    "status",
                    {"state": "cancelling" if cancelled else "idle"},
                )
            elif message.type == "shutdown":
                stopped = runtime.shutdown()
                preview_executor.shutdown(wait=True, cancel_futures=False)
                response = _event(
                    message.request_id,
                    "done" if stopped else "error",
                    {"action": "shutdown"} if stopped else {"code": "shutdown_timeout"},
                )
                should_exit = stopped
            elif message.type == "probe_models":
                model_root = str(message.payload.get("model_root", ""))
                emit(message.request_id, "status", {"state": "loading", "message": "正在检测外部模型…"})

                def probe_models_async(request_id: str = message.request_id, root: str = model_root) -> None:
                    try:
                        emit(request_id, "done", {"models": runtime.probe_models(root)})
                    except Exception as error:  # noqa: BLE001 - return probe failures through the protocol
                        emit(request_id, "error", {"code": "model_probe_failed", "message": str(error)})

                threading.Thread(
                    target=probe_models_async,
                    name="pixelflow-model-probe",
                    daemon=True,
                ).start()
                response = None
            elif message.type == "clear_cache":
                response = _event(
                    message.request_id,
                    "done",
                    {"cleared": runtime.clear_cache()},
                )
            elif message.type == "read_preview":
                def finish_preview(future: Any, request_id: str = message.request_id) -> None:
                    try:
                        emit(request_id, "done", future.result())
                    except Exception as error:  # noqa: BLE001 - return preview failures through the protocol
                        emit(request_id, "error", {"code": "preview_failed", "message": str(error)})

                preview_executor.submit(_read_preview, message.payload, True).add_done_callback(finish_preview)
            elif message.type == "retry_failed":
                runtime.start(message.request_id, message.payload)
                response = _event(message.request_id, "status", {"state": "started", "retry": True})
            else:
                response, should_exit = handle_message(message)
        except ProtocolError as error:
            response = _event("unknown", "error", {"code": "invalid_message", "message": str(error)})
            should_exit = False
        except Exception as error:
            response = _event(message.request_id if "message" in locals() else "unknown", "error", {"code": "invalid_request", "message": str(error)})
            should_exit = False
        if response is not None:
            print(response, flush=True)
        if should_exit:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
