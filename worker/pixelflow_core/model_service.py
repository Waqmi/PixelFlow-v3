"""Optional Pose/Seg model service used by the v3 Worker.

The service keeps the v2 process boundary and call shape on non-macOS systems.
On macOS, model calls stay in the Worker process so TCC does not see a second
file-access identity. Model availability is explicit and assistants are
invalidated when a model file changes. Inference failures return conservative
fallback evidence by default so a model is never required to finish a task.
"""

from __future__ import annotations

import atexit
import multiprocessing
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .model_cache import file_version


MODEL_PURPOSES = ("pose", "segmentation", "detection")
STATE_UNCONFIGURED = "未配置"
STATE_LOADING = "加载中…"
STATE_READY = "已就绪"
STATE_UNAVAILABLE = "不可用"
STATE_FALLBACK = "fallback"

STATUS_UNCONFIGURED = "unconfigured"
STATUS_LOADING = "loading"
STATUS_READY = "ready"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FALLBACK = "fallback"


class ModelProcessError(RuntimeError):
    """A model task failed without taking down the calling process."""


def _normal_purpose(purpose: str) -> str:
    value = str(purpose or "").strip().lower()
    if value == "seg":
        value = "segmentation"
    if value not in MODEL_PURPOSES + ("detection",):
        raise ValueError("未知模型用途：{}".format(purpose))
    return value


def configured_model_candidates(model_root: Union[str, Path], purpose: str) -> Tuple[Path, ...]:
    """List configured ONNX files without importing the inference runtime."""
    try:
        normalized = _normal_purpose(purpose)
    except ValueError:
        return ()
    if not str(model_root).strip():
        return ()
    model_dir = Path(model_root).expanduser() / normalized
    return tuple(sorted(model_dir.glob("*.onnx"))) if model_dir.is_dir() else ()


def model_candidates(model_root: Union[str, Path], purpose: str) -> Tuple[Path, ...]:
    return configured_model_candidates(model_root, purpose)


def _assistant_types() -> Tuple[type, type, type]:
    """Load the bundled assistant without depending on an external checkout."""
    try:
        from .model_assistant import LocalDetectionAssistant, LocalModelAssistant, LocalSegmentationAssistant
    except ImportError as error:
        raise ModelProcessError("内置 Pose/Seg 模块不可用：{}".format(error)) from error
    return LocalModelAssistant, LocalSegmentationAssistant, LocalDetectionAssistant


def _assistant_model_candidates(model_root: Union[str, Path], purpose: str) -> Tuple[Path, ...]:
    normalized = _normal_purpose(purpose)
    candidates = configured_model_candidates(model_root, normalized)
    if normalized == "detection":
        return tuple(path for path in candidates if path.with_suffix(".classes.json").is_file())
    return candidates


def _model_cache_key(model_root: Union[str, Path], purpose: str, model_path: Path) -> Tuple[Any, ...]:
    version = file_version(model_path) or {"path": str(model_path.resolve())}
    return (
        str(Path(model_root).expanduser().resolve()),
        _normal_purpose(purpose),
        version.get("path"),
        version.get("mtime_ns"),
        version.get("size"),
    )


def _load_assistant(cache: Dict[Tuple[Any, ...], object], model_root: str, purpose: str) -> object:
    normalized = _normal_purpose(purpose)
    candidates = _assistant_model_candidates(model_root, normalized)
    if not candidates:
        if normalized == "detection" and configured_model_candidates(model_root, normalized):
            raise ModelProcessError("detection/ 中缺少与 ONNX 同名的 .classes.json")
        raise ModelProcessError("{}/ 中没有 ONNX 模型".format(normalized))
    assistant_type = _assistant_types()[("pose", "segmentation", "detection").index(normalized)]
    errors: List[str] = []
    for candidate in candidates:
        key = _model_cache_key(model_root, normalized, candidate)
        if key in cache:
            return cache[key]
        try:
            assistant = assistant_type(candidate)
            # One active assistant per root and purpose.  The versioned key
            # above ensures replacing an ONNX file cannot reuse old state.
            for cached_key in tuple(cache):
                if cached_key[:2] == key[:2]:
                    del cache[cached_key]
            cache[key] = assistant
            return assistant
        except Exception as error:  # noqa: BLE001 - try the next model
            errors.append("{}: {}".format(candidate.name, error))
    detail = "；".join(errors) if errors else "{} 模型加载失败".format(normalized)
    raise ModelProcessError(detail)


def _fallback_composition(sizes: Optional[Dict[str, Any]], reason: str) -> Dict[str, object]:
    by_size: Dict[str, Dict[str, object]] = {}
    for name in (sizes or {}):
        by_size[str(name)] = {
            "composition_mode": "fallback",
            "target_crop_height_ratio": 1.0,
            "shoe_risk": True,
            "review_hint": "模型不可用，已使用保守构图，请人工确认",
            "fallback": True,
            "fallback_reason": reason,
        }
    return {
        "available": False,
        "model_status": STATUS_FALLBACK,
        "state": STATE_FALLBACK,
        "fallback": True,
        "fallback_reason": reason,
        "pose_evidence": {"status": "not_run", "reason": reason},
        "segmentation_evidence": {"status": "not_run", "reason": reason},
        "person_count": 0,
        "person_bboxes": [],
        "by_size": by_size,
    }


def _fallback_segmentation(reason: str) -> Dict[str, object]:
    return {
        "available": False,
        "model_status": STATUS_FALLBACK,
        "state": STATE_FALLBACK,
        "fallback": True,
        "fallback_reason": reason,
        "segmentation_evidence": {"status": "not_run", "reason": reason},
        "mask_area_ratio": 0.0,
        "mask_edge_ratio": 0.0,
        "mask_bbox_ratio": None,
    }


def _worker_main(request_queue: Any, response_queue: Any) -> None:
    assistants: Dict[Tuple[Any, ...], object] = {}
    while True:
        request = request_queue.get()
        task_id = str(request.get("task_id", ""))
        action = request.get("action")
        if action == "shutdown":
            return
        try:
            if action == "ping":
                result: Any = "ready"
            elif action == "warmup":
                purpose = _normal_purpose(request["purpose"])
                assistant = _load_assistant(assistants, request["model_root"], purpose)
                result = {
                    "model": Path(getattr(assistant, "model_path", "")).name,
                    "purpose": purpose,
                    "status": STATUS_READY,
                    "state": STATE_READY,
                    "model_version": file_version(getattr(assistant, "model_path", None)),
                }
            elif action == "compose":
                assistant = _load_assistant(assistants, request["model_root"], "pose")
                result = assistant.compose_for_sizes(
                    Path(request["source"]),
                    request["sizes"],
                    product_category=request.get("product_category"),
                    segmentation=request.get("segmentation"),
                    shoe_boxes=request.get("shoe_boxes"),
                    detector_boxes=request.get("detector_boxes"),
                )
                result = dict(result)
                result.setdefault("model_status", STATUS_READY)
                result.setdefault("state", STATE_READY)
                result.setdefault("fallback", False)
            elif action == "segment":
                assistant = _load_assistant(assistants, request["model_root"], "segmentation")
                result = dict(assistant.segment(Path(request["source"])))
                result.setdefault("model_status", STATUS_READY)
                result.setdefault("state", STATE_READY)
                result.setdefault("fallback", False)
            elif action == "detect_shoes":
                assistant = _load_assistant(assistants, request["model_root"], "detection")
                result = assistant.shoe_boxes(Path(request["source"]))
            elif action == "detect_boxes":
                assistant = _load_assistant(assistants, request["model_root"], "detection")
                result = assistant.boxes_for(Path(request["source"]), str(request.get("category", "shoes")))
            elif action == "probe":
                result = {}
                for purpose in MODEL_PURPOSES:
                    candidates = configured_model_candidates(request["model_root"], purpose)
                    if not candidates:
                        result[purpose] = {
                            "state": STATE_UNCONFIGURED,
                            "status": STATUS_UNCONFIGURED,
                            "fallback": True,
                        }
                        continue
                    assistant_candidates = _assistant_model_candidates(request["model_root"], purpose)
                    if not assistant_candidates:
                        result[purpose] = {
                            "state": STATE_UNAVAILABLE,
                            "status": STATUS_UNAVAILABLE,
                            "fallback": True,
                            "error": "detection/ 中缺少与 ONNX 同名的 .classes.json",
                        }
                        continue
                    try:
                        assistant = _load_assistant(assistants, request["model_root"], purpose)
                        model_path = getattr(assistant, "model_path", assistant_candidates[0])
                        result[purpose] = {
                            "state": STATE_READY,
                            "status": STATUS_READY,
                            "fallback": False,
                            "model": Path(model_path).name,
                            "model_version": file_version(Path(model_path), include_digest=True),
                        }
                    except Exception as error:  # noqa: BLE001 - report each optional model
                        result[purpose] = {
                            "state": STATE_UNAVAILABLE,
                            "status": STATUS_UNAVAILABLE,
                            "fallback": True,
                            "error": str(error),
                        }
            else:
                raise ModelProcessError("未知模型任务：{}".format(action))
            response_queue.put({"task_id": task_id, "result": result, "error": ""})
        except Exception as error:  # noqa: BLE001 - isolate every inference failure
            response_queue.put(
                {
                    "task_id": task_id,
                    "result": None,
                    "error": "{}\n{}".format(error, traceback.format_exc()),
                }
            )


class ModelProcessService:
    """Synchronous client used from Worker background threads."""

    def __init__(self, timeout: float = 180.0) -> None:
        self.timeout = timeout
        # macOS TCC treats a multiprocessing-spawned frozen worker as a new
        # file-access identity.  Keep model reads in the already-authorized
        # Worker process there; other platforms retain the isolated process.
        self._direct = sys.platform == "darwin"
        self._context = multiprocessing.get_context("spawn")
        self._request_queue: Any = None
        self._response_queue: Any = None
        self._process: Any = None
        self._direct_assistants: Dict[Tuple[Any, ...], object] = {}
        self._lock = threading.Lock()
        self._counter = 0
        self._failures: List[Dict[str, str]] = []
        self._states: Dict[Tuple[str, str], str] = {}
        self._statuses: Dict[Tuple[str, str], str] = {}
        self._reasons: Dict[Tuple[str, str], str] = {}

    @property
    def is_running(self) -> bool:
        return bool(self._process is not None and self._process.is_alive())

    @property
    def failures(self) -> Tuple[Dict[str, str], ...]:
        return tuple(self._failures)

    def _start(self) -> None:
        self._request_queue = self._context.Queue()
        self._response_queue = self._context.Queue()
        self._process = self._context.Process(
            target=_worker_main,
            args=(self._request_queue, self._response_queue),
            name="PixelFlowModel",
            daemon=True,
        )
        self._process.start()

    def _restart(self) -> None:
        if self._direct:
            self._direct_assistants.clear()
            self._states.clear()
            self._statuses.clear()
            self._reasons.clear()
            return
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
        self._states.clear()
        self._statuses.clear()
        self._reasons.clear()
        self._start()

    @staticmethod
    def _state_key(model_root: Union[str, Path], purpose: str) -> Tuple[str, str]:
        return str(Path(model_root).expanduser().resolve()), _normal_purpose(purpose)

    def model_state(self, model_root: Union[str, Path], purpose: str) -> str:
        key = self._state_key(model_root, purpose)
        if key not in self._states and not configured_model_candidates(model_root, purpose):
            return STATE_UNCONFIGURED
        return self._states.get(key, STATE_UNAVAILABLE)

    def model_status(self, model_root: Union[str, Path], purpose: str) -> Dict[str, str]:
        key = self._state_key(model_root, purpose)
        state = self.model_state(model_root, purpose)
        status = self._statuses.get(key)
        if status is None:
            status = {
                STATE_UNCONFIGURED: STATUS_UNCONFIGURED,
                STATE_LOADING: STATUS_LOADING,
                STATE_READY: STATUS_READY,
                STATE_UNAVAILABLE: STATUS_UNAVAILABLE,
                STATE_FALLBACK: STATUS_FALLBACK,
            }.get(state, STATUS_UNAVAILABLE)
        result = {"state": state, "status": status}
        if key in self._reasons:
            result["fallback_reason"] = self._reasons[key]
        return result

    def model_versions(self, model_root: Union[str, Path]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for purpose in MODEL_PURPOSES:
            candidates = configured_model_candidates(model_root, purpose)
            result[purpose] = file_version(candidates[0]) if candidates else None
        return result

    def _set_state(self, model_root: Union[str, Path], purpose: str, state: str, reason: str = "") -> None:
        key = self._state_key(model_root, purpose)
        self._states[key] = state
        self._statuses[key] = {
            STATE_UNCONFIGURED: STATUS_UNCONFIGURED,
            STATE_LOADING: STATUS_LOADING,
            STATE_READY: STATUS_READY,
            STATE_UNAVAILABLE: STATUS_UNAVAILABLE,
            STATE_FALLBACK: STATUS_FALLBACK,
        }.get(state, STATUS_UNAVAILABLE)
        if reason:
            self._reasons[key] = reason
        else:
            self._reasons.pop(key, None)

    def _request(self, action: str, **payload: Any) -> Any:
        if self._direct:
            return self._request_in_process(action, **payload)
        with self._lock:
            if not self.is_running:
                self._start()
            self._counter += 1
            task_id = "model-{}".format(self._counter)
            assert self._request_queue is not None and self._response_queue is not None
            self._request_queue.put({"task_id": task_id, "action": action, **payload})
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if not self.is_running:
                    message = "推理子进程异常退出，任务 {} 已记录为失败；服务已自动重启".format(task_id)
                    self._failures.append({"task_id": task_id, "error": message})
                    self._restart()
                    raise ModelProcessError(message)
                try:
                    response = self._response_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if response.get("task_id") != task_id:
                    continue
                if response.get("error"):
                    raise ModelProcessError(str(response["error"]).splitlines()[0])
                return response.get("result")
            message = "模型任务 {} 超时，服务已自动重启".format(task_id)
            self._failures.append({"task_id": task_id, "error": message})
            self._restart()
            raise ModelProcessError(message)

    def _request_in_process(self, action: str, **payload: Any) -> Any:
        """Run one model request without spawning a second macOS process."""
        with self._lock:
            if action == "ping":
                return "ready"
            if action == "warmup":
                purpose = _normal_purpose(payload["purpose"])
                assistant = _load_assistant(self._direct_assistants, payload["model_root"], purpose)
                return {
                    "model": Path(getattr(assistant, "model_path", "")).name,
                    "purpose": purpose,
                    "status": STATUS_READY,
                    "state": STATE_READY,
                    "model_version": file_version(getattr(assistant, "model_path", None)),
                }
            if action == "compose":
                assistant = _load_assistant(self._direct_assistants, payload["model_root"], "pose")
                result = dict(
                    assistant.compose_for_sizes(
                        Path(payload["source"]),
                        payload["sizes"],
                        product_category=payload.get("product_category"),
                        segmentation=payload.get("segmentation"),
                        shoe_boxes=payload.get("shoe_boxes"),
                        detector_boxes=payload.get("detector_boxes"),
                    )
                )
                result.setdefault("model_status", STATUS_READY)
                result.setdefault("state", STATE_READY)
                result.setdefault("fallback", False)
                return result
            if action == "segment":
                assistant = _load_assistant(self._direct_assistants, payload["model_root"], "segmentation")
                result = dict(assistant.segment(Path(payload["source"])))
                result.setdefault("model_status", STATUS_READY)
                result.setdefault("state", STATE_READY)
                result.setdefault("fallback", False)
                return result
            if action == "detect_shoes":
                assistant = _load_assistant(self._direct_assistants, payload["model_root"], "detection")
                return assistant.shoe_boxes(Path(payload["source"]))
            if action == "detect_boxes":
                assistant = _load_assistant(self._direct_assistants, payload["model_root"], "detection")
                return assistant.boxes_for(Path(payload["source"]), str(payload.get("category", "shoes")))
            if action == "probe":
                result: Dict[str, Dict[str, Any]] = {}
                for purpose in MODEL_PURPOSES:
                    candidates = configured_model_candidates(payload["model_root"], purpose)
                    if not candidates:
                        result[purpose] = {
                            "state": STATE_UNCONFIGURED,
                            "status": STATUS_UNCONFIGURED,
                            "fallback": True,
                        }
                        continue
                    assistant_candidates = _assistant_model_candidates(payload["model_root"], purpose)
                    if not assistant_candidates:
                        result[purpose] = {
                            "state": STATE_UNAVAILABLE,
                            "status": STATUS_UNAVAILABLE,
                            "fallback": True,
                            "error": "detection/ 中缺少与 ONNX 同名的 .classes.json",
                        }
                        continue
                    try:
                        assistant = _load_assistant(
                            self._direct_assistants,
                            payload["model_root"],
                            purpose,
                        )
                        model_path = getattr(assistant, "model_path", assistant_candidates[0])
                        result[purpose] = {
                            "state": STATE_READY,
                            "status": STATUS_READY,
                            "fallback": False,
                            "model": Path(model_path).name,
                            "model_version": file_version(Path(model_path), include_digest=True),
                        }
                    except Exception as error:  # noqa: BLE001 - keep probe per-model
                        result[purpose] = {
                            "state": STATE_UNAVAILABLE,
                            "status": STATUS_UNAVAILABLE,
                            "fallback": True,
                            "error": str(error),
                        }
                return result
            raise ModelProcessError("未知模型任务：{}".format(action))

    def ping(self) -> str:
        return self._request("ping")

    def probe_models(self, model_root: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
        """Probe configured models and avoid starting inference when none exist."""
        root = str(model_root)
        if not any(configured_model_candidates(root, purpose) for purpose in MODEL_PURPOSES):
            return {
                purpose: {
                    "state": STATE_UNCONFIGURED,
                    "status": STATUS_UNCONFIGURED,
                    "fallback": True,
                }
                for purpose in MODEL_PURPOSES
            }
        for purpose in MODEL_PURPOSES:
            self._set_state(root, purpose, STATE_LOADING)
        try:
            result = self._request("probe", model_root=root)
        except Exception as error:
            reason = str(error)
            for purpose in MODEL_PURPOSES:
                if configured_model_candidates(root, purpose):
                    self._set_state(root, purpose, STATE_UNAVAILABLE, reason)
                else:
                    self._set_state(root, purpose, STATE_UNCONFIGURED)
            return {
                purpose: {
                    "state": self.model_state(root, purpose),
                    "status": self._statuses.get(self._state_key(root, purpose), STATUS_UNAVAILABLE),
                    "fallback": True,
                    "error": reason,
                }
                for purpose in MODEL_PURPOSES
            }
        for purpose in MODEL_PURPOSES:
            status = result.get(purpose, {})
            state = str(status.get("state", STATE_UNAVAILABLE))
            self._set_state(root, purpose, state, str(status.get("error", "")))
        return result

    def probe(self, model_root: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
        return self.probe_models(model_root)

    def warmup(self, model_root: Union[str, Path], purpose: str, strict: bool = False) -> Dict[str, Any]:
        normalized = _normal_purpose(purpose)
        root = str(model_root)
        if not configured_model_candidates(root, normalized):
            reason = "{}/ 中没有 ONNX 模型".format(normalized)
            self._set_state(root, normalized, STATE_UNCONFIGURED, reason)
            if strict:
                raise ModelProcessError(reason)
            return {
                "status": STATUS_UNCONFIGURED,
                "state": STATE_UNCONFIGURED,
                "fallback": True,
                "fallback_reason": reason,
            }
        self._set_state(root, normalized, STATE_LOADING)
        try:
            result = self._request("warmup", model_root=root, purpose=normalized)
        except Exception as error:
            reason = str(error)
            self._set_state(root, normalized, STATE_UNAVAILABLE, reason)
            if strict:
                raise
            return {
                "status": STATUS_FALLBACK,
                "state": STATE_FALLBACK,
                "fallback": True,
                "fallback_reason": reason,
            }
        self._set_state(root, normalized, STATE_READY)
        return dict(result or {})

    def compose(
        self,
        model_root: Union[str, Path],
        source: Path,
        sizes: Optional[Dict[str, Any]] = None,
        product_category: Optional[str] = None,
        strict: bool = False,
        **kwargs: Any
    ) -> Dict[str, object]:
        root = str(model_root)
        sizes = sizes or {}
        if not configured_model_candidates(root, "pose"):
            reason = "pose/ 中没有 ONNX 模型"
            self._set_state(root, "pose", STATE_UNCONFIGURED, reason)
            if strict:
                raise ModelProcessError(reason)
            return _fallback_composition(sizes, reason)
        self._set_state(root, "pose", STATE_LOADING)
        try:
            result = self._request(
                "compose",
                model_root=root,
                source=str(source),
                sizes=sizes,
                product_category=product_category or kwargs.get("category"),
                segmentation=kwargs.get("segmentation"),
                shoe_boxes=kwargs.get("shoe_boxes"),
                detector_boxes=kwargs.get("detector_boxes"),
            )
        except Exception as error:
            reason = str(error)
            self._set_state(root, "pose", STATE_UNAVAILABLE, reason)
            if strict:
                raise
            return _fallback_composition(sizes, reason)
        self._set_state(root, "pose", STATE_READY)
        return dict(result or {})

    def compose_for_sizes(
        self,
        model_root: Union[str, Path],
        source: Path,
        sizes: Dict[str, Any],
        product_category: Optional[str] = None,
        strict: bool = False,
        segmentation: Optional[Dict[str, object]] = None,
        shoe_boxes: Optional[List[List[float]]] = None,
        detector_boxes: Optional[List[List[float]]] = None,
    ) -> Dict[str, object]:
        return self.compose(
            model_root, source, sizes, product_category, strict=strict,
            segmentation=segmentation, shoe_boxes=shoe_boxes, detector_boxes=detector_boxes,
        )

    def detect_shoes(self, model_root: Union[str, Path], source: Path) -> List[List[float]]:
        """Shoe boxes (source px) from the optional detector; empty when it is off or fails."""
        root = str(model_root)
        candidates = _assistant_model_candidates(root, "detection")
        if not candidates:
            return []
        try:
            return list(self._request("detect_shoes", model_root=root, source=str(source)) or [])
        except Exception:  # noqa: BLE001 - the detector is optional
            return []

    def detect_boxes(self, model_root: Union[str, Path], source: Path, category: str) -> List[List[float]]:
        """Category-mapped detector boxes; failures return an empty list."""
        root = str(model_root)
        candidates = _assistant_model_candidates(root, "detection")
        if not candidates:
            return []
        try:
            return list(self._request("detect_boxes", model_root=root, source=str(source), category=category) or [])
        except Exception:  # noqa: BLE001 - detector is optional
            return []

    def segment(
        self,
        model_root: Union[str, Path],
        source: Path,
        strict: bool = False,
    ) -> Dict[str, object]:
        root = str(model_root)
        if not configured_model_candidates(root, "segmentation"):
            reason = "segmentation/ 中没有 ONNX 模型"
            self._set_state(root, "segmentation", STATE_UNCONFIGURED, reason)
            if strict:
                raise ModelProcessError(reason)
            return _fallback_segmentation(reason)
        self._set_state(root, "segmentation", STATE_LOADING)
        try:
            result = self._request(
                "segment",
                model_root=root,
                source=str(source),
            )
        except Exception as error:
            reason = str(error)
            self._set_state(root, "segmentation", STATE_UNAVAILABLE, reason)
            if strict:
                raise
            return _fallback_segmentation(reason)
        self._set_state(root, "segmentation", STATE_READY)
        return dict(result or {})

    def close(self) -> None:
        with self._lock:
            if self._direct:
                self._direct_assistants.clear()
                self._states.clear()
                self._statuses.clear()
                self._reasons.clear()
                return
            process = self._process
            if process is None:
                return
            if process.is_alive() and self._request_queue is not None:
                self._request_queue.put({"task_id": "", "action": "shutdown"})
                process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            self._process = None
            self._states.clear()
            self._statuses.clear()
            self._reasons.clear()


ModelService = ModelProcessService
_SHARED_SERVICE = ModelProcessService()
atexit.register(_SHARED_SERVICE.close)


def get_model_process_service() -> ModelProcessService:
    return _SHARED_SERVICE


def get_model_service() -> ModelProcessService:
    return _SHARED_SERVICE


__all__ = [
    "MODEL_PURPOSES",
    "ModelProcessError",
    "ModelProcessService",
    "ModelService",
    "configured_model_candidates",
    "get_model_process_service",
    "get_model_service",
    "model_candidates",
    "STATE_FALLBACK",
    "STATE_LOADING",
    "STATE_READY",
    "STATE_UNAVAILABLE",
    "STATE_UNCONFIGURED",
    "STATUS_FALLBACK",
    "STATUS_LOADING",
    "STATUS_READY",
    "STATUS_UNAVAILABLE",
    "STATUS_UNCONFIGURED",
]
