from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pixelflow_core.model_cache import (
    COMPOSITION_RULES_VERSION,
    build_cache_identity,
    clear_model_result_cache,
    load_model_result,
    lookup_model_result,
    store_model_result,
)
from pixelflow_core.model_service import (
    STATE_FALLBACK,
    STATE_READY,
    STATE_UNCONFIGURED,
    STATUS_FALLBACK,
    STATUS_UNCONFIGURED,
    ModelProcessCancelled,
    ModelProcessError,
    ModelProcessService,
    _worker_main,
)
import pixelflow_core.model_service as model_service


class ModelCacheTests(unittest.TestCase):
    def test_identity_includes_all_v3_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            pose = root / "pose.onnx"
            seg = root / "seg.onnx"
            source.write_bytes(b"source")
            pose.write_bytes(b"pose")
            seg.write_bytes(b"seg")
            identity = build_cache_identity(
                source,
                pose,
                "pose",
                "shoes",
                (1440, 1440),
                COMPOSITION_RULES_VERSION,
                seg_model_path=seg,
            )
            assert identity is not None
            self.assertEqual(identity["purpose"], "pose")
            self.assertEqual(identity["category"], "shoes")
            self.assertEqual(identity["output_size"], "1440x1440")
            self.assertEqual(identity["composition_rules_version"], COMPOSITION_RULES_VERSION)
            self.assertEqual(identity["pose_model"]["size"], 4)
            self.assertEqual(identity["seg_model"]["size"], 3)

    def test_detection_model_changes_identity_and_key(self) -> None:
        from pixelflow_core.model_cache import cache_key

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            pose = root / "pose.onnx"
            det = root / "det.onnx"
            source.write_bytes(b"source")
            pose.write_bytes(b"pose")
            base = cache_key(source, pose, "pose", "shoes", "all")
            identity = build_cache_identity(source, pose, "pose", "shoes", "all")
            assert identity is not None
            self.assertIsNone(identity["detection_model"])
            det.write_bytes(b"det-1")
            added = cache_key(source, pose, "pose", "shoes", "all", detection_model_path=det)
            self.assertNotEqual(base, added)
            det.write_bytes(b"detection-2")
            self.assertNotEqual(added, cache_key(source, pose, "pose", "shoes", "all", detection_model_path=det))

    def test_lookup_reports_missing_expired_and_corrupt_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            model = root / "pose.onnx"
            source.write_bytes(b"source")
            model.write_bytes(b"model")
            cache_dir = root / "cache"
            with patch("pixelflow_core.model_cache.model_cache_dir", return_value=cache_dir):
                self.assertEqual(
                    lookup_model_result(source, model, "pose", "shoes").status,
                    "miss",
                )
                self.assertTrue(store_model_result(source, model, "pose", "shoes", {"ok": True}))
                self.assertEqual(load_model_result(source, model, "pose", "shoes"), {"ok": True})
                key = next(cache_dir.glob("*.json"))
                payload = json.loads(key.read_text(encoding="utf-8"))
                payload["created_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
                key.write_text(json.dumps(payload), encoding="utf-8")
                self.assertEqual(lookup_model_result(source, model, "pose", "shoes").status, "expired")
                self.assertTrue(store_model_result(source, model, "pose", "shoes", {"ok": True}))
                key = next(cache_dir.glob("*.json"))
                key.write_text("broken", encoding="utf-8")
                self.assertEqual(lookup_model_result(source, model, "pose", "shoes").status, "invalid")

    def test_source_model_rule_and_output_changes_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            model = root / "pose.onnx"
            source.write_bytes(b"source")
            model.write_bytes(b"model")
            with patch("pixelflow_core.model_cache.model_cache_dir", return_value=root / "cache"):
                self.assertTrue(
                    store_model_result(
                        source, model, "pose", "shirt", {"ok": True}, "1440x1440", "rules-1"
                    )
                )
                self.assertIsNotNone(load_model_result(source, model, "pose", "shirt", "1440x1440", "rules-1"))
                self.assertIsNone(load_model_result(source, model, "pose", "shirt", "1440x1920", "rules-1"))
                self.assertIsNone(load_model_result(source, model, "pose", "shirt", "1440x1440", "rules-2"))
                source.write_bytes(b"source-v2")
                self.assertIsNone(load_model_result(source, model, "pose", "shirt", "1440x1440", "rules-1"))

    def test_clear_only_removes_private_cache_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.jpg"
            model = root / "pose.onnx"
            source.write_bytes(b"source")
            model.write_bytes(b"model")
            cache_dir = root / "cache"
            with patch("pixelflow_core.model_cache.model_cache_dir", return_value=cache_dir):
                self.assertTrue(store_model_result(source, model, "pose", "shirt", {"ok": True}))
                keep = root / "user-output.jpg"
                keep.write_bytes(b"keep")
                self.assertEqual(clear_model_result_cache(), 1)
                self.assertTrue(keep.exists())


class ModelServiceTests(unittest.TestCase):
    def test_probe_reads_metadata_without_instantiating_onnx_assistants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pose_dir = root / "pose"
            pose_dir.mkdir()
            model = pose_dir / "model.onnx"
            model.write_bytes(b"pose-model")
            with patch(
                "pixelflow_core.model_service._load_assistant",
                side_effect=AssertionError("probe must not instantiate an assistant"),
            ):
                service = ModelProcessService(timeout=1)
                try:
                    result = service.probe_models(root)
                finally:
                    service.close()
            self.assertEqual(result["pose"]["state"], STATE_READY)
            self.assertEqual(result["pose"]["model_version"]["size"], len(b"pose-model"))
            for _ in range(100):
                result = service.probe_models(root)
                if result["pose"]["model_version"].get("sha256"):
                    break
                time.sleep(0.01)
            self.assertEqual(result["pose"]["model_version"]["sha256"], hashlib.sha256(b"pose-model").hexdigest())

    def test_probe_reports_unavailable_when_model_file_cannot_be_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pose_dir = root / "pose"
            pose_dir.mkdir()
            (pose_dir / "model.onnx").write_bytes(b"pose-model")
            with patch("pixelflow_core.model_service.file_version", return_value=None):
                service = ModelProcessService(timeout=1)
                try:
                    result = service.probe_models(root)
                finally:
                    service.close()
            self.assertEqual(result["pose"]["state"], "不可用")
            self.assertEqual(result["pose"]["status"], "unavailable")

    def test_worker_probe_returns_readiness_metadata_without_digest(self) -> None:
        class Queue:
            def __init__(self, items=()):
                self.items = list(items)

            def get(self):
                return self.items.pop(0)

            def put(self, item):
                self.items.append(item)

        class Assistant:
            def __init__(self, model_path):
                self.model_path = model_path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_bytes = {"pose": b"pose-model", "segmentation": b"seg-model", "detection": b"detection-model"}
            for purpose, content in model_bytes.items():
                model_dir = root / purpose
                model_dir.mkdir()
                (model_dir / "model.onnx").write_bytes(content)
                if purpose == "detection":
                    (model_dir / "model.classes.json").write_text('{"classes": []}', encoding="utf-8")

            requests = Queue([
                {"task_id": "probe-1", "action": "probe", "model_root": str(root)},
                {"action": "shutdown"},
            ])
            responses = Queue()
            with patch("pixelflow_core.model_service._assistant_types", return_value=(Assistant, Assistant, Assistant)):
                _worker_main(requests, responses)

            result = responses.items[0]["result"]
            for purpose, content in model_bytes.items():
                self.assertEqual(result[purpose]["state"], "已就绪")
                self.assertEqual(result[purpose]["model_version"]["size"], len(content))
                self.assertNotIn("sha256", result[purpose]["model_version"])

    def test_probe_deduplicates_inflight_digest_and_returns_ready_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pose_dir = root / "pose"
            pose_dir.mkdir()
            model = pose_dir / "model.onnx"
            model.write_bytes(b"pose-model")
            real_file_version = model_service.file_version
            digest_started = threading.Event()
            digest_calls = 0
            digest_lock = threading.Lock()

            def slow_file_version(path, *, include_digest=False):
                nonlocal digest_calls
                if include_digest:
                    with digest_lock:
                        digest_calls += 1
                    digest_started.set()
                    time.sleep(0.15)
                return real_file_version(path, include_digest=include_digest)

            service = ModelProcessService(timeout=1)
            try:
                with patch.object(model_service, "file_version", side_effect=slow_file_version):
                    started = time.monotonic()
                    first = service.probe_models(root)
                    elapsed = time.monotonic() - started
                    second = service.probe_models(root)
                    self.assertLess(elapsed, 0.1)
                    self.assertEqual(first["pose"]["state"], STATE_READY)
                    self.assertEqual(second["pose"]["state"], STATE_READY)
                    self.assertNotIn("sha256", first["pose"]["model_version"])
                    self.assertTrue(digest_started.wait(1))
                    time.sleep(0.2)
                    final = service.probe_models(root)
                self.assertEqual(digest_calls, 1)
                self.assertEqual(final["pose"]["model_version"]["sha256"], hashlib.sha256(b"pose-model").hexdigest())
            finally:
                service.close()

    def test_concurrent_metadata_probes_share_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pose_dir = root / "pose"
            pose_dir.mkdir()
            (pose_dir / "model.onnx").write_bytes(b"pose-model")
            real_probe = model_service._probe_model_metadata
            calls = 0
            calls_lock = threading.Lock()

            def slow_probe(model_root):
                nonlocal calls
                with calls_lock:
                    calls += 1
                time.sleep(0.1)
                return real_probe(model_root)

            service = ModelProcessService(timeout=1)
            results = []
            try:
                with patch.object(model_service, "_probe_model_metadata", side_effect=slow_probe):
                    threads = [threading.Thread(target=lambda: results.append(service.probe_models(root))) for _ in range(2)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()
                self.assertEqual(calls, 1)
                self.assertEqual(len(results), 2)
                self.assertTrue(all(result["pose"]["state"] == STATE_READY for result in results))
            finally:
                service.close()

    def test_detection_probe_reports_unavailable_without_sidecar(self) -> None:
        class Assistant:
            def __init__(self, model_path):
                self.model_path = model_path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detection_dir = root / "detection"
            detection_dir.mkdir()
            (detection_dir / "model.onnx").write_bytes(b"detection-model")
            with patch("pixelflow_core.model_service._assistant_types", return_value=(Assistant, Assistant, Assistant)), patch.object(
                ModelProcessService,
                "_start",
            ) as start_process:
                service = ModelProcessService(timeout=1)
                try:
                    result = service.probe_models(root)
                finally:
                    service.close()
            start_process.assert_not_called()
            self.assertEqual(result["detection"]["state"], "不可用")
            self.assertIn("classes.json", result["detection"]["error"])

    def test_missing_models_report_unconfigured_without_starting_process(self) -> None:
        service = ModelProcessService(timeout=1)
        try:
            result = service.probe_models("/path/that/does/not/exist")
            self.assertEqual(result["pose"]["state"], STATE_UNCONFIGURED)
            self.assertEqual(result["pose"]["status"], STATUS_UNCONFIGURED)
            self.assertFalse(service.is_running)
            composition = service.compose("/path/that/does/not/exist", Path("missing.jpg"), {"1440x1440": ((1440, 1440), (255, 255, 255))})
            self.assertEqual(composition["state"], STATE_FALLBACK)
            self.assertEqual(composition["model_status"], STATUS_FALLBACK)
            self.assertTrue(composition["fallback"])
            self.assertEqual(composition["pose_evidence"]["status"], "not_run")
            self.assertTrue(composition["fallback_reason"])
            self.assertTrue(composition["by_size"]["1440x1440"]["fallback_reason"])
            self.assertEqual(service.model_state("/path/that/does/not/exist", "pose"), STATE_UNCONFIGURED)
        finally:
            service.close()

    def test_macos_model_requests_stay_in_worker_process(self) -> None:
        class Assistant:
            def __init__(self, model_path):
                self.model_path = model_path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for purpose in ("pose", "segmentation"):
                model_dir = root / purpose
                model_dir.mkdir()
                (model_dir / "model.onnx").write_bytes(purpose.encode())

            with patch("pixelflow_core.model_service.sys.platform", "darwin"), patch(
                "pixelflow_core.model_service._assistant_types",
                return_value=(Assistant, Assistant),
            ):
                service = ModelProcessService(timeout=1)
                try:
                    result = service.probe_models(root)
                    self.assertEqual(result["pose"]["state"], STATE_READY)
                    self.assertEqual(result["segmentation"]["state"], STATE_READY)
                    self.assertIsNone(service._process)
                    self.assertFalse(service.is_running)
                    for _ in range(100):
                        result = service.probe_models(root)
                        if all(
                            result[purpose]["model_version"].get("sha256")
                            for purpose in ("pose", "segmentation")
                        ):
                            break
                        time.sleep(0.01)
                    self.assertTrue(
                        all(
                            result[purpose]["model_version"].get("sha256")
                            for purpose in ("pose", "segmentation")
                        )
                    )
                finally:
                    service.close()

    def test_versioned_model_cache_key_changes_when_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pose_dir = root / "pose"
            pose_dir.mkdir()
            model = pose_dir / "model.onnx"
            model.write_bytes(b"v1")
            detection_dir = root / "detection"
            detection_dir.mkdir()
            detection = detection_dir / "model.onnx"
            detection.write_bytes(b"detection-v1")
            (detection_dir / "model.classes.json").write_text('{"classes": []}', encoding="utf-8")
            service = ModelProcessService(timeout=1)
            try:
                first = service.model_versions(root)["pose"]
                self.assertEqual(service.model_versions(root)["detection"]["size"], len(b"detection-v1"))
                model.write_bytes(b"v2")
                # Same size and a coarse filesystem clock (Windows) can keep mtime equal; move it on.
                stat = model.stat()
                os.utime(model, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
                second = service.model_versions(root)["pose"]
                self.assertNotEqual(first, second)
            finally:
                service.close()

    def test_default_model_request_timeout_is_below_classification_watchdog(self) -> None:
        service = ModelProcessService()
        try:
            self.assertEqual(service.timeout, 60.0)
        finally:
            service.close()

    def test_cancel_terminates_blocked_child_and_next_request_restarts_lazily(self) -> None:
        class FakeProcess:
            def __init__(self):
                self.alive = True
                self.terminated = False

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.terminated = True
                self.alive = False

            def join(self, timeout=None):
                return None

        class RequestQueue:
            def __init__(self):
                self.items = []

            def put(self, item):
                self.items.append(item)

            def cancel_join_thread(self):
                return None

            def close(self):
                return None

        class WaitingResponseQueue:
            def __init__(self, cancel_event):
                self.cancel_event = cancel_event

            def get(self, timeout):
                self.cancel_event.wait(timeout)
                raise queue.Empty

            def cancel_join_thread(self):
                return None

            def close(self):
                return None

        class SuccessResponseQueue:
            def __init__(self, request_queue):
                self.request_queue = request_queue

            def get(self, timeout):
                task_id = self.request_queue.items[-1]["task_id"]
                return {"task_id": task_id, "result": "ready", "error": ""}

            def cancel_join_thread(self):
                return None

            def close(self):
                return None

        cancel_event = threading.Event()
        process = FakeProcess()
        events = []
        starts = 0
        with patch("pixelflow_core.model_service.sys.platform", "win32"):
            service = ModelProcessService(timeout=1)
        service._process = process
        service._request_queue = RequestQueue()
        service._response_queue = WaitingResponseQueue(cancel_event)

        def start_next_request():
            nonlocal starts
            starts += 1
            next_process = FakeProcess()
            requests = RequestQueue()
            service._process = next_process
            service._request_queue = requests
            service._response_queue = SuccessResponseQueue(requests)

        service._start = start_next_request
        timer = threading.Timer(0.03, cancel_event.set)
        timer.start()
        try:
            with self.assertRaises(ModelProcessCancelled) as raised:
                service._request(
                    "warmup",
                    cancel_event=cancel_event,
                    request_id="classify-cancel",
                    trace_callback=lambda *event: events.append(event),
                    model_root="/tmp/models",
                    purpose="pose",
                )
            timer.join()
            self.assertEqual(raised.exception.code, "model_request_cancelled")
            self.assertTrue(process.terminated)
            self.assertIsNone(service._process)
            self.assertEqual(starts, 0)
            self.assertIn(("model_process", "pose", "model-1", "failure", "model_request_cancelled"), events)

            self.assertEqual(service._request("ping"), "ready")
            self.assertEqual(starts, 1)
        finally:
            timer.cancel()
            service.close()

    def test_blocked_request_timeout_stops_child_without_eager_restart(self) -> None:
        class FakeProcess:
            def __init__(self):
                self.alive = True
                self.terminated = False

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.terminated = True
                self.alive = False

            def join(self, timeout=None):
                return None

        class FakeQueue:
            def put(self, _item):
                return None

            def get(self, timeout):
                time.sleep(timeout)
                raise queue.Empty

            def cancel_join_thread(self):
                return None

            def close(self):
                return None

        with patch("pixelflow_core.model_service.sys.platform", "win32"):
            service = ModelProcessService(timeout=0.05)
        process = FakeProcess()
        service._process = process
        service._request_queue = FakeQueue()
        service._response_queue = FakeQueue()
        service._start = lambda: self.fail("timed out child must not restart in the same request")
        events = []
        try:
            with self.assertRaises(ModelProcessError) as raised:
                service._request(
                    "warmup",
                    request_id="classify-timeout",
                    trace_callback=lambda *event: events.append(event),
                    model_root="/tmp/models",
                    purpose="pose",
                )
            self.assertEqual(raised.exception.code, "model_request_timeout")
            self.assertTrue(process.terminated)
            self.assertIsNone(service._process)
            self.assertIn(("model_process", "pose", "model-1", "failure", "model_request_timeout"), events)
        finally:
            service.close()

    def test_child_exit_is_traced_and_not_restarted_in_the_failed_request(self) -> None:
        class FakeProcess:
            def is_alive(self):
                return False

            def terminate(self):
                raise AssertionError("dead process should not be terminated")

            def join(self, timeout=None):
                return None

        class FakeQueue:
            def put(self, _item):
                return None

            def get(self, timeout):
                raise queue.Empty

            def cancel_join_thread(self):
                return None

            def close(self):
                return None

        with patch("pixelflow_core.model_service.sys.platform", "win32"):
            service = ModelProcessService(timeout=1)
        starts = 0

        def start_dead_process():
            nonlocal starts
            starts += 1
            service._process = FakeProcess()
            service._request_queue = FakeQueue()
            service._response_queue = FakeQueue()

        service._start = start_dead_process
        events = []
        try:
            with self.assertRaises(ModelProcessError) as raised:
                service._request(
                    "warmup",
                    request_id="classify-exit",
                    trace_callback=lambda *event: events.append(event),
                    model_root="/tmp/models",
                    purpose="pose",
                )
            self.assertEqual(raised.exception.code, "model_process_exited")
            self.assertEqual(starts, 1)
            self.assertIsNone(service._process)
            self.assertIn(("model_process", "pose", "model-1", "failure", "model_process_exited"), events)
        finally:
            service.close()

    def test_worker_traces_session_create_start_success_and_safe_failure(self) -> None:
        class Queue:
            def __init__(self, items=()):
                self.items = list(items)

            def get(self):
                return self.items.pop(0)

            def put(self, item):
                self.items.append(item)

        class Assistant:
            def __init__(self, model_path):
                self.model_path = model_path
                self.session = object()

        class FailedAssistant:
            def __init__(self, model_path):
                raise RuntimeError("sensitive path: {}".format(model_path))

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for purpose in ("pose", "segmentation"):
                model_dir = root / purpose
                model_dir.mkdir()
                (model_dir / "model.onnx").write_bytes(b"model")
            requests = Queue([
                {"task_id": "model-1", "action": "warmup", "model_root": str(root), "purpose": "pose", "trace_enabled": True},
                {"task_id": "model-2", "action": "warmup", "model_root": str(root), "purpose": "segmentation", "trace_enabled": True},
                {"action": "shutdown"},
            ])
            responses = Queue()
            with patch(
                "pixelflow_core.model_service._assistant_types",
                return_value=(Assistant, FailedAssistant, FailedAssistant),
            ):
                _worker_main(requests, responses)

        trace_events = [item for item in responses.items if "trace_event" in item]
        self.assertEqual(
            [(item["trace_event"]["purpose"], item["trace_event"]["phase"]) for item in trace_events],
            [("pose", "start"), ("pose", "success"), ("segmentation", "start"), ("segmentation", "failure")],
        )
        self.assertTrue(all(set(item["trace_event"]) == {"event", "purpose", "phase", "error_code"} for item in trace_events))
        self.assertEqual(responses.items[-1]["error_code"], "model_session_create_failed")


if __name__ == "__main__":
    unittest.main()
