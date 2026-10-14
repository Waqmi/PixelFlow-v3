"""Lazy single-process boundary for the existing Pose/Seg assistants."""

from __future__ import annotations

import atexit
import multiprocessing
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Any


class ModelProcessError(RuntimeError):
    """A model task failed without taking down the calling process."""


def configured_model_candidates(model_root: str | Path, purpose: str) -> tuple[Path, ...]:
    """List configured files without importing ONNX Runtime or loading a model."""
    if purpose not in {"pose", "segmentation"} or not str(model_root).strip():
        return ()
    root = Path(model_root).expanduser()
    for model_root_candidate in (root, root / "models"):
        model_dir = model_root_candidate / purpose
        if not model_dir.is_dir():
            continue
        candidates = tuple(
            sorted(
                (
                    path
                    for path in model_dir.iterdir()
                    if path.is_file() and path.suffix.lower() == ".onnx"
                ),
                key=lambda path: path.name.casefold(),
            )
        )
        if candidates:
            return candidates
    return ()


def _load_assistant(cache: dict[tuple[str, str], object], model_root: str, purpose: str):
    key = (str(Path(model_root).expanduser()), purpose)
    if key in cache:
        return cache[key]
    from local_model_assistant import LocalModelAssistant, LocalSegmentationAssistant

    assistant_type = LocalModelAssistant if purpose == "pose" else LocalSegmentationAssistant
    errors = []
    for candidate in configured_model_candidates(model_root, purpose):
        try:
            assistant = assistant_type(candidate)
            for cached_key in tuple(cache):
                if cached_key[0] != key[0]:
                    del cache[cached_key]
            cache[key] = assistant
            return assistant
        except Exception as error:  # noqa: BLE001 - try the next configured model
            errors.append(f"{candidate.name}: {error}")
    detail = "；".join(errors) if errors else f"{purpose}/ 中没有 ONNX 模型"
    raise ModelProcessError(detail)


def _worker_main(request_queue, response_queue) -> None:
    assistants: dict[tuple[str, str], object] = {}
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
                assistant = _load_assistant(assistants, request["model_root"], request["purpose"])
                result = {"model": assistant.model_path.name}
            elif action == "compose":
                assistant = _load_assistant(assistants, request["model_root"], "pose")
                result = assistant.compose_for_sizes(
                    Path(request["source"]),
                    request["sizes"],
                    product_category=request.get("product_category"),
                )
            elif action == "segment":
                assistant = _load_assistant(assistants, request["model_root"], "segmentation")
                result = assistant.segment(Path(request["source"]))
            elif action == "probe":
                result = {}
                for purpose in ("pose", "segmentation"):
                    try:
                        assistant = _load_assistant(assistants, request["model_root"], purpose)
                        result[purpose] = {"state": "已就绪", "model": assistant.model_path.name}
                    except Exception as error:  # noqa: BLE001 - report each optional model separately
                        result[purpose] = {"state": "不可用", "error": str(error)}
            else:
                raise ModelProcessError(f"未知模型任务：{action}")
            response_queue.put({"task_id": task_id, "result": result, "error": ""})
        except Exception as error:  # noqa: BLE001 - isolate every inference failure
            response_queue.put(
                {
                    "task_id": task_id,
                    "result": None,
                    "error": f"{error}\n{traceback.format_exc()}",
                }
            )


class ModelProcessService:
    """Synchronous client used from PixelFlow's existing background threads."""

    def __init__(self, timeout: float = 180.0) -> None:
        self.timeout = timeout
        self._context = multiprocessing.get_context("spawn")
        self._request_queue = None
        self._response_queue = None
        self._process = None
        self._lock = threading.Lock()
        self._counter = 0
        self._failures: list[dict[str, str]] = []
        self._states: dict[tuple[str, str], str] = {}

    @property
    def is_running(self) -> bool:
        return bool(self._process is not None and self._process.is_alive())

    @property
    def failures(self) -> tuple[dict[str, str], ...]:
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
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
        self._states.clear()
        self._start()

    @staticmethod
    def _state_key(model_root: str | Path, purpose: str) -> tuple[str, str]:
        return str(Path(model_root).expanduser()), purpose

    def model_state(self, model_root: str | Path, purpose: str) -> str:
        return self._states.get(self._state_key(model_root, purpose), "未加载")

    def _request(self, action: str, **payload):
        with self._lock:
            if not self.is_running:
                self._start()
            self._counter += 1
            task_id = f"model-{self._counter}"
            assert self._request_queue is not None and self._response_queue is not None
            self._request_queue.put({"task_id": task_id, "action": action, **payload})
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if not self.is_running:
                    message = f"推理子进程异常退出，任务 {task_id} 已记录为失败；服务已自动重启"
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
            message = f"模型任务 {task_id} 超时，服务已自动重启"
            self._failures.append({"task_id": task_id, "error": message})
            self._restart()
            raise ModelProcessError(message)

    def warmup(self, model_root: str | Path, purpose: str) -> dict[str, str]:
        """Load one configured model before the first real inference."""
        if purpose not in {"pose", "segmentation"}:
            raise ValueError(f"未知模型用途：{purpose}")
        key = self._state_key(model_root, purpose)
        self._states[key] = "加载中…"
        try:
            result = self._request(
                "warmup", model_root=str(model_root), purpose=purpose
            )
        except Exception:
            self._states[key] = "不可用"
            raise
        self._states[key] = "已就绪"
        return result

    def compose_for_sizes(
        self,
        model_root: str | Path,
        source: Path,
        sizes: dict[str, tuple[tuple[int, int], tuple[int, int]]],
        product_category: str | None = None,
    ) -> dict[str, object]:
        key = self._state_key(model_root, "pose")
        self._states[key] = "加载中…"
        try:
            result = self._request(
                "compose",
                model_root=str(model_root),
                source=str(source),
                sizes=sizes,
                product_category=product_category,
            )
        except Exception:
            self._states[key] = "不可用"
            raise
        self._states[key] = "已就绪"
        return result

    def segment(self, model_root: str | Path, source: Path) -> dict[str, object]:
        key = self._state_key(model_root, "segmentation")
        self._states[key] = "加载中…"
        try:
            result = self._request(
                "segment",
                model_root=str(model_root),
                source=str(source),
            )
        except Exception:
            self._states[key] = "不可用"
            raise
        self._states[key] = "已就绪"
        return result

    def probe(self, model_root: str | Path) -> dict[str, dict[str, str]]:
        for purpose in ("pose", "segmentation"):
            self._states[self._state_key(model_root, purpose)] = "加载中…"
        result = self._request("probe", model_root=str(model_root))
        for purpose, status in result.items():
            self._states[self._state_key(model_root, purpose)] = str(status.get("state", "不可用"))
        return result

    def ping(self) -> str:
        return self._request("ping")

    def close(self) -> None:
        with self._lock:
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


_SHARED_SERVICE = ModelProcessService()
atexit.register(_SHARED_SERVICE.close)


def get_model_process_service() -> ModelProcessService:
    return _SHARED_SERVICE
