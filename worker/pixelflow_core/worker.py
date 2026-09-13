from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from .contracts import Message, ProtocolError, PROTOCOL_VERSION
from .engine import GenerationRequest, V26EngineAdapter


SUPPORTED_REQUESTS = {
    "ping",
    "probe_models",
    "classify_materials",
    "start_generation",
    "cancel_generation",
    "retry_failed",
    "clear_cache",
    "shutdown",
}


def _event(request_id: str, event_type: str, payload: dict) -> str:
    return Message(PROTOCOL_VERSION, request_id, event_type, _json_safe(payload)).to_json()


def _json_safe(value: Any) -> Any:
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
    source_filter = payload.get("source_filter")
    if source_filter is not None and not isinstance(source_filter, (list, tuple, set)):
        raise ValueError("source_filter must be an array")
    return GenerationRequest(
        product_root=Path(str(payload.get("product_root", ""))).expanduser(),
        output_root=Path(str(payload.get("output_root", ""))).expanduser(),
        selected_sizes=tuple(str(item) for item in selected_sizes),
        include_logo=bool(payload.get("include_logo", True)),
        include_vip=bool(payload.get("include_vip", True)),
        include_model_images=bool(payload.get("include_model_images", True)),
        category_override=payload.get("category_override"),
        enable_category_template=bool(payload.get("enable_category_template", False)),
        enable_material_understanding=bool(payload.get("enable_material_understanding", False)),
        source_type_overrides={str(key): str(value) for key, value in paths_map("source_type_overrides").items()},
        source_metadata_overrides=paths_map("source_metadata_overrides"),
        source_scale_adjustments={str(key): float(value) for key, value in paths_map("source_scale_adjustments").items()},
        excluded_model_sources={str(item) for item in payload.get("excluded_model_sources", [])},
        source_filter={str(item) for item in source_filter} if source_filter is not None else None,
        naming_mode=str(payload.get("naming_mode", "short")),
        local_model_enabled=bool(payload.get("local_model_enabled", False)),
        local_model_path=str(payload["local_model_path"]) if payload.get("local_model_path") else None,
        material_confirmation_completed=bool(payload.get("material_confirmation_completed", True)),
    )


class WorkerRuntime:
    """Own one asynchronous generation job and its cancellation event."""

    def __init__(
        self,
        adapter: V26EngineAdapter | None = None,
        emit: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.adapter = adapter or V26EngineAdapter()
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
            request = _generation_request(payload)
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

    def _run(self, request_id: str, request: GenerationRequest, cancel_event: threading.Event) -> None:
        def report(completed: int, total: int, phase: str) -> None:
            self._emit(
                request_id,
                "progress",
                {"completed": completed, "total": total, "phase": phase},
            )

        try:
            result = self.adapter.run(request, progress=report, cancel_event=cancel_event)
            event_type = "cancelled" if bool(result.get("cancelled")) else "done"
            self._emit(request_id, event_type, {"result": result})
        except Exception as error:  # worker boundary must report failures, not die silently
            self._emit(request_id, "error", {"code": "generation_failed", "message": str(error)})
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
        return _event(message.request_id, "ready", {"worker": "pixelflow-v3", "protocol": PROTOCOL_VERSION}), False
    return _event(
        message.request_id,
        "status",
        {"state": "scaffold", "message": "v2.6 core integration is pending baseline completion"},
    ), False


def main() -> int:
    output_lock = threading.Lock()

    def emit(request_id: str, event_type: str, payload: dict[str, Any]) -> None:
        line = _event(request_id, event_type, payload)
        with output_lock:
            print(line, flush=True)

    runtime = WorkerRuntime(emit=emit)
    emit("worker", "ready", {"worker": "pixelflow-v3", "protocol": PROTOCOL_VERSION})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = Message.from_json(line)
            should_exit = False
            if message.type == "start_generation":
                runtime.start(message.request_id, message.payload)
                response = _event(message.request_id, "status", {"state": "started"})
            elif message.type == "cancel_generation":
                cancelled = runtime.cancel(message.request_id)
                response = _event(
                    message.request_id,
                    "status",
                    {"state": "cancelling" if cancelled else "idle"},
                )
            elif message.type == "shutdown":
                stopped = runtime.shutdown()
                response = _event(
                    message.request_id,
                    "done" if stopped else "error",
                    {"action": "shutdown"} if stopped else {"code": "shutdown_timeout"},
                )
                should_exit = stopped
            else:
                response, should_exit = handle_message(message)
        except ProtocolError as error:
            response = _event("unknown", "error", {"code": "invalid_message", "message": str(error)})
            should_exit = False
        except Exception as error:
            response = _event(message.request_id if "message" in locals() else "unknown", "error", {"code": "invalid_request", "message": str(error)})
            should_exit = False
        print(response, flush=True)
        if should_exit:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
