"""Optional Pose/Seg model service used by the v3 Worker.

The service keeps the v2 process boundary and call shape on non-macOS systems.
On macOS, model calls stay in the Worker process so TCC does not see a second
file-access identity. Model availability is explicit and assistants are
invalidated when a model file changes. Inference failures return conservative
fallback evidence by default so a model is never required to finish a task.
"""

from __future__ import annotations

import atexit
from concurrent.futures import Future
import multiprocessing
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

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

    def __init__(self, message: str, code: str = "model_failed") -> None:
        self.code = code
        super().__init__(message)


class ModelProcessCancelled(ModelProcessError):
    """A model request was stopped because its classification was cancelled."""

    def __init__(self) -> None:
        super().__init__("模型任务已取消", "model_request_cancelled")


_MODEL_TRACE_PHASES = {"start", "success", "failure"}
_MODEL_TRACE_PURPOSES = {"pose", "segmentation", "detection"}


def _emit_model_trace(
    callback: Optional[Callable[[str, str, str, str, Optional[str]], None]],
    event: str,
    purpose: str,
    task_id: str,
    phase: str,
    error_code: Optional[str] = None,
) -> None:
    if callback is None or event not in {"model_process", "session_create"}:
        return
    if purpose not in _MODEL_TRACE_PURPOSES or phase not in _MODEL_TRACE_PHASES:
        return
    safe_code = error_code if error_code and error_code.replace("_", "").isalnum() else None
    try:
        callback(event, purpose, task_id[:48], phase, safe_code)
    except Exception:  # noqa: BLE001 - diagnostics must not affect model calls
        return


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


def _readable_model_version(path: Path) -> Optional[Dict[str, Any]]:
    """Return stat metadata after a bounded read-open check."""
    version = file_version(path)
    if version is None:
        return None
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError:
        return None
    return version


def _probe_model_metadata(model_root: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
    """Report configured model files without constructing ONNX Runtime sessions.

    Model probing runs while the desktop settings page is open.  Loading an
    ONNX session here makes a read-only status check depend on the platform's
    frozen runtime, antivirus, and external-drive latency.  Actual inference
    still loads the assistant through ``_load_assistant`` when it is needed.
    """
    result: Dict[str, Dict[str, Any]] = {}
    root = str(model_root)
    for purpose in MODEL_PURPOSES:
        candidates = configured_model_candidates(root, purpose)
        if not candidates:
            result[purpose] = {
                "state": STATE_UNCONFIGURED,
                "status": STATUS_UNCONFIGURED,
                "fallback": True,
            }
            continue
        assistant_candidates = _assistant_model_candidates(root, purpose)
        if not assistant_candidates:
            result[purpose] = {
                "state": STATE_UNAVAILABLE,
                "status": STATUS_UNAVAILABLE,
                "fallback": True,
                "error": "detection/ 中缺少与 ONNX 同名的 .classes.json",
            }
            continue
        model_path = assistant_candidates[0]
        version = _readable_model_version(model_path)
        if version is None:
            result[purpose] = {
                "state": STATE_UNAVAILABLE,
                "status": STATUS_UNAVAILABLE,
                "fallback": True,
                "error": "模型文件不可读取",
            }
            continue
        result[purpose] = {
            "state": STATE_READY,
            "status": STATUS_READY,
            "fallback": False,
            "model": model_path.name,
            "model_version": version,
        }
    return result


def _model_cache_key(model_root: Union[str, Path], purpose: str, model_path: Path) -> Tuple[Any, ...]:
    version = file_version(model_path) or {"path": str(model_path.resolve())}
    return (
        str(Path(model_root).expanduser().resolve()),
        _normal_purpose(purpose),
        version.get("path"),
        version.get("mtime_ns"),
        version.get("size"),
    )


def _load_assistant(
    cache: Dict[Tuple[Any, ...], object],
    model_root: str,
    purpose: str,
    trace_event: Optional[Callable[[str, Optional[str]], None]] = None,
) -> object:
    normalized = _normal_purpose(purpose)
    candidates = _assistant_model_candidates(model_root, normalized)
    if not candidates:
        if normalized == "detection" and configured_model_candidates(model_root, normalized):
            raise ModelProcessError("detection/ 中缺少与 ONNX 同名的 .classes.json", "model_configuration_invalid")
        raise ModelProcessError("{}/ 中没有 ONNX 模型".format(normalized), "model_not_found")
    assistant_index = ("pose", "segmentation", "detection").index(normalized)
    errors: List[str] = []
    for candidate in candidates:
        key = _model_cache_key(model_root, normalized, candidate)
        if key in cache:
            return cache[key]
        if trace_event is not None:
            trace_event("start", None)
        try:
            assistant_type = _assistant_types()[assistant_index]
            assistant = assistant_type(candidate)
            if getattr(assistant, "session", None) is None:
                raise ModelProcessError("ONNX session was not initialized", "model_session_create_failed")
            # One active assistant per root and purpose.  The versioned key
            # above ensures replacing an ONNX file cannot reuse old state.
            for cached_key in tuple(cache):
                if cached_key[:2] == key[:2]:
                    del cache[cached_key]
            cache[key] = assistant
            if trace_event is not None:
                trace_event("success", None)
            return assistant
        except Exception as error:  # noqa: BLE001 - try the next model
            if trace_event is not None:
                trace_event("failure", "model_session_create_failed")
            errors.append("{}: {}".format(candidate.name, error))
    detail = "；".join(errors) if errors else "{} 模型加载失败".format(normalized)
    raise ModelProcessError(detail, "model_session_create_failed")


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

        def load_assistant(purpose: str) -> object:
            def report_session(phase: str, error_code: Optional[str]) -> None:
                if request.get("trace_enabled"):
                    response_queue.put(
                        {
                            "task_id": task_id,
                            "trace_event": {
                                "event": "session_create",
                                "purpose": purpose,
                                "phase": phase,
                                "error_code": error_code,
                            },
                        }
                    )

            return _load_assistant(assistants, request["model_root"], purpose, report_session)

        try:
            if action == "ping":
                result: Any = "ready"
            elif action == "warmup":
                purpose = _normal_purpose(request["purpose"])
                assistant = load_assistant(purpose)
                result = {
                    "model": Path(getattr(assistant, "model_path", "")).name,
                    "purpose": purpose,
                    "status": STATUS_READY,
                    "state": STATE_READY,
                    "model_version": file_version(getattr(assistant, "model_path", None)),
                }
            elif action == "compose":
                assistant = load_assistant("pose")
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
                assistant = load_assistant("segmentation")
                result = dict(assistant.segment(Path(request["source"])))
                result.setdefault("model_status", STATUS_READY)
                result.setdefault("state", STATE_READY)
                result.setdefault("fallback", False)
            elif action == "detect_shoes":
                assistant = load_assistant("detection")
                result = assistant.shoe_boxes(Path(request["source"]))
            elif action == "detect_boxes":
                assistant = load_assistant("detection")
                result = assistant.boxes_for(Path(request["source"]), str(request.get("category", "shoes")))
            elif action == "probe":
                result = _probe_model_metadata(request["model_root"])
            else:
                raise ModelProcessError("未知模型任务：{}".format(action))
            response_queue.put({"task_id": task_id, "result": result, "error": "", "error_code": ""})
        except Exception as error:  # noqa: BLE001 - isolate every inference failure
            if isinstance(error, ModelProcessError):
                error_code = error.code
            elif action == "warmup":
                error_code = "model_session_create_failed"
            else:
                error_code = "model_inference_failed"
            response_queue.put(
                {
                    "task_id": task_id,
                    "result": None,
                    "error": "{}\n{}".format(error, traceback.format_exc()),
                    "error_code": error_code,
                }
            )


class ModelProcessService:
    """Synchronous client used from Worker background threads."""

    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout
        # macOS TCC treats a multiprocessing-spawned frozen worker as a new
        # file-access identity.  Keep model reads in the already-authorized
        # Worker process there; other platforms retain the isolated process.
        # PyInstaller's frozen Windows executable is already an isolated
        # Worker process.  Spawning the same frozen entry point again can
        # re-enter the JSONL stdin loop instead of reaching _worker_main.
        # Keep model sessions in-process for frozen builds; development
        # Windows retains the existing child-process boundary.
        self._direct = sys.platform == "darwin" or bool(getattr(sys, "frozen", False))
        self._context = multiprocessing.get_context("spawn")
        self._request_queue: Any = None
        self._response_queue: Any = None
        self._process: Any = None
        self._direct_assistants: Dict[Tuple[Any, ...], object] = {}
        self._lock = threading.Lock()
        self._counter_lock = threading.Lock()
        self._counter = 0
        self._failures: List[Dict[str, str]] = []
        self._states: Dict[Tuple[str, str], str] = {}
        self._statuses: Dict[Tuple[str, str], str] = {}
        self._reasons: Dict[Tuple[str, str], str] = {}
        self._probe_lock = threading.RLock()
        self._probe_inflight: Dict[str, Future] = {}
        self._probe_digest_cache: Dict[Tuple[str, int, int], str] = {}
        self._probe_digest_inflight: Dict[Tuple[str, int, int], Future] = {}

    @property
    def is_running(self) -> bool:
        return bool(self._process is not None and self._process.is_alive())

    @property
    def failures(self) -> Tuple[Dict[str, str], ...]:
        return tuple(self._failures)

    def _start(self) -> None:
        if self._process is not None or self._request_queue is not None or self._response_queue is not None:
            self._stop_process()
        self._request_queue = self._context.Queue()
        self._response_queue = self._context.Queue()
        self._process = self._context.Process(
            target=_worker_main,
            args=(self._request_queue, self._response_queue),
            name="PixelFlowModel",
            daemon=True,
        )
        self._process.start()

    def _stop_process(self) -> None:
        if self._direct:
            self._direct_assistants.clear()
            self._states.clear()
            self._statuses.clear()
            self._reasons.clear()
            return
        process = self._process
        if process is not None:
            if process.is_alive():
                process.terminate()
            try:
                process.join(timeout=2)
            except AssertionError:  # the process object was created but start() failed
                pass
        for model_queue in (self._request_queue, self._response_queue):
            if model_queue is None:
                continue
            try:
                model_queue.cancel_join_thread()
                model_queue.close()
            except (AttributeError, OSError, ValueError):
                pass
        self._process = None
        self._request_queue = None
        self._response_queue = None
        self._states.clear()
        self._statuses.clear()
        self._reasons.clear()

    @staticmethod
    def _action_purpose(action: str, payload: Dict[str, Any]) -> str:
        if action == "warmup":
            return _normal_purpose(str(payload.get("purpose", "")))
        return {
            "compose": "pose",
            "segment": "segmentation",
            "detect_shoes": "detection",
            "detect_boxes": "detection",
        }.get(action, "")

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

    @staticmethod
    def _probe_key(model_root: Union[str, Path]) -> str:
        return str(Path(model_root).expanduser().resolve())

    @staticmethod
    def _probe_digest_key(version: Dict[str, Any]) -> Optional[Tuple[str, int, int]]:
        try:
            return str(version["path"]), int(version["mtime_ns"]), int(version["size"])
        except (KeyError, TypeError, ValueError):
            return None

    def _start_probe_digest(self, key: Tuple[str, int, int], path: Path, future: Future) -> None:
        """Compute one real model digest without holding the model request lock."""
        digest: Optional[str] = None
        try:
            version = file_version(path, include_digest=True)
            if version is not None and self._probe_digest_key(version) == key:
                digest = str(version.get("sha256") or "") or None
        except Exception:  # noqa: BLE001 - a digest is optional for readiness
            digest = None
        with self._probe_lock:
            if digest:
                self._probe_digest_cache[key] = digest
            self._probe_digest_inflight.pop(key, None)
        future.set_result(digest)

    def _attach_probe_digests(self, result: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Attach cached SHA-256 values and schedule missing ones in the background."""
        for status in result.values():
            version = status.get("model_version")
            if not isinstance(version, dict):
                continue
            key = self._probe_digest_key(version)
            if key is None:
                continue
            path = Path(key[0])
            start = False
            with self._probe_lock:
                digest = self._probe_digest_cache.get(key)
                if digest:
                    version["sha256"] = digest
                elif key not in self._probe_digest_inflight:
                    future = Future()
                    self._probe_digest_inflight[key] = future
                    start = True
            if start:
                threading.Thread(
                    target=self._start_probe_digest,
                    args=(key, path, future),
                    daemon=True,
                    name="pixelflow-model-digest",
                ).start()
        return result

    def _probe_metadata(self, model_root: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
        """Deduplicate concurrent metadata probes while keeping them lock-free."""
        root = str(model_root)
        key = self._probe_key(root)
        with self._probe_lock:
            future = self._probe_inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._probe_inflight[key] = future
        assert future is not None
        if owner:
            try:
                result = _probe_model_metadata(root)
            except Exception as error:  # noqa: BLE001 - preserve existing probe fallback
                future.set_exception(error)
                with self._probe_lock:
                    self._probe_inflight.pop(key, None)
                raise
            future.set_result(result)
            with self._probe_lock:
                self._probe_inflight.pop(key, None)
        else:
            result = future.result()
        return self._attach_probe_digests(result)

    def _request(
        self,
        action: str,
        *,
        cancel_event: Any = None,
        request_id: str = "",
        trace_callback: Optional[Callable[[str, str, str, str, Optional[str]], None]] = None,
        **payload: Any,
    ) -> Any:
        purpose = self._action_purpose(action, payload)
        if self._direct:
            with self._counter_lock:
                self._counter += 1
                task_id = "model-{}".format(self._counter)
            if cancel_event is not None and cancel_event.is_set():
                raise ModelProcessCancelled()
            _emit_model_trace(trace_callback, "model_process", purpose, task_id, "start")
            try:
                result = self._request_in_process(
                    action,
                    trace_callback=trace_callback,
                    task_id=task_id,
                    **payload,
                )
            except Exception as error:
                code = error.code if isinstance(error, ModelProcessError) else "model_inference_failed"
                _emit_model_trace(trace_callback, "model_process", purpose, task_id, "failure", code)
                raise
            if cancel_event is not None and cancel_event.is_set():
                _emit_model_trace(trace_callback, "model_process", purpose, task_id, "failure", "model_request_cancelled")
                raise ModelProcessCancelled()
            _emit_model_trace(trace_callback, "model_process", purpose, task_id, "success")
            return result
        with self._lock:
            if cancel_event is not None and cancel_event.is_set():
                raise ModelProcessCancelled()
            with self._counter_lock:
                self._counter += 1
                task_id = "model-{}".format(self._counter)
            _emit_model_trace(trace_callback, "model_process", purpose, task_id, "start")
            if not self.is_running:
                try:
                    self._start()
                except Exception as error:
                    _emit_model_trace(trace_callback, "model_process", purpose, task_id, "failure", "model_process_start_failed")
                    self._stop_process()
                    raise ModelProcessError("推理子进程无法启动", "model_process_start_failed") from error
            assert self._request_queue is not None and self._response_queue is not None
            request = {"task_id": task_id, "action": action, **payload}
            if trace_callback is not None and request_id:
                request["trace_enabled"] = True
            try:
                self._request_queue.put(request)
            except Exception as error:
                _emit_model_trace(trace_callback, "model_process", purpose, task_id, "failure", "model_process_start_failed")
                self._stop_process()
                raise ModelProcessError("模型任务无法提交", "model_process_start_failed") from error
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    _emit_model_trace(trace_callback, "model_process", purpose, task_id, "failure", "model_request_cancelled")
                    self._failures.append({"task_id": task_id, "error": "model_request_cancelled"})
                    self._stop_process()
                    raise ModelProcessCancelled()
                if not self.is_running:
                    _emit_model_trace(trace_callback, "model_process", purpose, task_id, "failure", "model_process_exited")
                    self._failures.append({"task_id": task_id, "error": "model_process_exited"})
                    self._stop_process()
                    raise ModelProcessError("推理子进程异常退出", "model_process_exited")
                try:
                    response = self._response_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if response.get("task_id") != task_id:
                    continue
                trace_event = response.get("trace_event")
                if isinstance(trace_event, dict):
                    _emit_model_trace(
                        trace_callback,
                        str(trace_event.get("event", "")),
                        str(trace_event.get("purpose", "")),
                        task_id,
                        str(trace_event.get("phase", "")),
                        str(trace_event.get("error_code") or "") or None,
                    )
                    continue
                if response.get("error"):
                    code = str(response.get("error_code") or "model_failed")
                    _emit_model_trace(trace_callback, "model_process", purpose, task_id, "failure", code)
                    raise ModelProcessError(str(response["error"]).splitlines()[0], code)
                _emit_model_trace(trace_callback, "model_process", purpose, task_id, "success")
                return response.get("result")
            _emit_model_trace(trace_callback, "model_process", purpose, task_id, "failure", "model_request_timeout")
            self._failures.append({"task_id": task_id, "error": "model_request_timeout"})
            self._stop_process()
            raise ModelProcessError("模型任务超时", "model_request_timeout")

    def _request_in_process(
        self,
        action: str,
        *,
        trace_callback: Optional[Callable[[str, str, str, str, Optional[str]], None]] = None,
        task_id: str = "",
        **payload: Any,
    ) -> Any:
        """Run one model request without spawning a second macOS process."""
        if action == "probe":
            return self._probe_metadata(payload["model_root"])

        def load_assistant(purpose: str) -> object:
            def report_session(phase: str, error_code: Optional[str]) -> None:
                _emit_model_trace(trace_callback, "session_create", purpose, task_id, phase, error_code)

            return _load_assistant(self._direct_assistants, payload["model_root"], purpose, report_session)

        with self._lock:
            if action == "ping":
                return "ready"
            if action == "warmup":
                purpose = _normal_purpose(payload["purpose"])
                assistant = load_assistant(purpose)
                return {
                    "model": Path(getattr(assistant, "model_path", "")).name,
                    "purpose": purpose,
                    "status": STATUS_READY,
                    "state": STATE_READY,
                    "model_version": file_version(getattr(assistant, "model_path", None)),
                }
            if action == "compose":
                assistant = load_assistant("pose")
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
                assistant = load_assistant("segmentation")
                result = dict(assistant.segment(Path(payload["source"])))
                result.setdefault("model_status", STATUS_READY)
                result.setdefault("state", STATE_READY)
                result.setdefault("fallback", False)
                return result
            if action == "detect_shoes":
                assistant = load_assistant("detection")
                return assistant.shoe_boxes(Path(payload["source"]))
            if action == "detect_boxes":
                assistant = load_assistant("detection")
                return assistant.boxes_for(Path(payload["source"]), str(payload.get("category", "shoes")))
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
            # Probe metadata outside the inference lock.  A frozen Windows
            # sidecar or a large model digest must not delay readiness.
            result = self._probe_metadata(root)
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

    def warmup(
        self,
        model_root: Union[str, Path],
        purpose: str,
        strict: bool = False,
        *,
        cancel_event: Any = None,
        request_id: str = "",
        trace_callback: Optional[Callable[[str, str, str, str, Optional[str]], None]] = None,
    ) -> Dict[str, Any]:
        normalized = _normal_purpose(purpose)
        root = str(model_root)
        if not configured_model_candidates(root, normalized):
            reason = "{}/ 中没有 ONNX 模型".format(normalized)
            self._set_state(root, normalized, STATE_UNCONFIGURED, reason)
            if strict:
                raise ModelProcessError(reason, "model_not_found")
            return {
                "status": STATUS_UNCONFIGURED,
                "state": STATE_UNCONFIGURED,
                "fallback": True,
                "fallback_reason": reason,
                "error_code": "model_not_found",
                "failed_stage": "session_create",
            }
        self._set_state(root, normalized, STATE_LOADING)
        try:
            result = self._request(
                "warmup",
                cancel_event=cancel_event,
                request_id=request_id,
                trace_callback=trace_callback,
                model_root=root,
                purpose=normalized,
            )
        except ModelProcessCancelled:
            raise
        except Exception as error:
            error_code = error.code if isinstance(error, ModelProcessError) else "model_session_create_failed"
            reason = "{} 模型初始化失败，已使用规则回退".format(normalized)
            self._set_state(root, normalized, STATE_FALLBACK, error_code)
            if strict:
                raise
            return {
                "status": STATUS_FALLBACK,
                "state": STATE_FALLBACK,
                "fallback": True,
                "fallback_reason": reason,
                "error_code": error_code,
                "failed_stage": "session_create",
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
        cancel_event: Any = None,
        request_id: str = "",
        trace_callback: Optional[Callable[[str, str, str, str, Optional[str]], None]] = None,
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
                cancel_event=cancel_event,
                request_id=request_id,
                trace_callback=trace_callback,
                model_root=root,
                source=str(source),
                sizes=sizes,
                product_category=product_category or kwargs.get("category"),
                segmentation=kwargs.get("segmentation"),
                shoe_boxes=kwargs.get("shoe_boxes"),
                detector_boxes=kwargs.get("detector_boxes"),
            )
        except ModelProcessCancelled:
            raise
        except Exception as error:
            error_code = error.code if isinstance(error, ModelProcessError) else "model_inference_failed"
            reason = "Pose 模型失败，已使用规则回退"
            self._set_state(root, "pose", STATE_FALLBACK, error_code)
            if strict:
                raise
            result = _fallback_composition(sizes, reason)
            result["error_code"] = error_code
            result["failed_stage"] = "inference"
            return result
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
        cancel_event: Any = None,
        request_id: str = "",
        trace_callback: Optional[Callable[[str, str, str, str, Optional[str]], None]] = None,
    ) -> Dict[str, object]:
        return self.compose(
            model_root, source, sizes, product_category, strict=strict,
            cancel_event=cancel_event, request_id=request_id, trace_callback=trace_callback,
            segmentation=segmentation, shoe_boxes=shoe_boxes, detector_boxes=detector_boxes,
        )

    def detect_shoes(
        self,
        model_root: Union[str, Path],
        source: Path,
        *,
        cancel_event: Any = None,
        request_id: str = "",
        trace_callback: Optional[Callable[[str, str, str, str, Optional[str]], None]] = None,
    ) -> List[List[float]]:
        """Shoe boxes (source px) from the optional detector; empty when it is off or fails."""
        root = str(model_root)
        candidates = _assistant_model_candidates(root, "detection")
        if not candidates:
            if configured_model_candidates(root, "detection"):
                self._set_state(root, "detection", STATE_FALLBACK, "model_configuration_invalid")
            else:
                self._set_state(root, "detection", STATE_UNCONFIGURED)
            return []
        try:
            result = list(self._request(
                "detect_shoes", cancel_event=cancel_event, request_id=request_id,
                trace_callback=trace_callback, model_root=root, source=str(source),
            ) or [])
            self._set_state(root, "detection", STATE_READY)
            return result
        except ModelProcessCancelled:
            raise
        except Exception as error:  # noqa: BLE001 - the detector is optional
            code = error.code if isinstance(error, ModelProcessError) else "model_inference_failed"
            self._set_state(root, "detection", STATE_FALLBACK, code)
            return []

    def detect_boxes(
        self,
        model_root: Union[str, Path],
        source: Path,
        category: str,
        *,
        cancel_event: Any = None,
        request_id: str = "",
        trace_callback: Optional[Callable[[str, str, str, str, Optional[str]], None]] = None,
    ) -> List[List[float]]:
        """Category-mapped detector boxes; failures return an empty list."""
        root = str(model_root)
        candidates = _assistant_model_candidates(root, "detection")
        if not candidates:
            if configured_model_candidates(root, "detection"):
                self._set_state(root, "detection", STATE_FALLBACK, "model_configuration_invalid")
            else:
                self._set_state(root, "detection", STATE_UNCONFIGURED)
            return []
        try:
            result = list(self._request(
                "detect_boxes", cancel_event=cancel_event, request_id=request_id,
                trace_callback=trace_callback, model_root=root, source=str(source), category=category,
            ) or [])
            self._set_state(root, "detection", STATE_READY)
            return result
        except ModelProcessCancelled:
            raise
        except Exception as error:  # noqa: BLE001 - detector is optional
            code = error.code if isinstance(error, ModelProcessError) else "model_inference_failed"
            self._set_state(root, "detection", STATE_FALLBACK, code)
            return []

    def segment(
        self,
        model_root: Union[str, Path],
        source: Path,
        strict: bool = False,
        cancel_event: Any = None,
        request_id: str = "",
        trace_callback: Optional[Callable[[str, str, str, str, Optional[str]], None]] = None,
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
                cancel_event=cancel_event,
                request_id=request_id,
                trace_callback=trace_callback,
                model_root=root,
                source=str(source),
            )
        except ModelProcessCancelled:
            raise
        except Exception as error:
            error_code = error.code if isinstance(error, ModelProcessError) else "model_inference_failed"
            reason = "Seg 模型失败，已使用保守回退"
            self._set_state(root, "segmentation", STATE_FALLBACK, error_code)
            if strict:
                raise
            result = _fallback_segmentation(reason)
            result["error_code"] = error_code
            result["failed_stage"] = "inference"
            return result
        self._set_state(root, "segmentation", STATE_READY)
        return dict(result or {})

    def close(self) -> None:
        with self._lock:
            if self._direct:
                self._stop_process()
                return
            process = self._process
            if process is not None and process.is_alive() and self._request_queue is not None:
                self._request_queue.put({"task_id": "", "action": "shutdown"})
                process.join(timeout=2)
            self._stop_process()


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
