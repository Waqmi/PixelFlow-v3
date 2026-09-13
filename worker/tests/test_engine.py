from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from pixelflow_core.engine import GenerationRequest, V26EngineAdapter
from pixelflow_core.worker import WorkerRuntime, _generation_request


class EngineAdapterTests(unittest.TestCase):
    def test_request_requires_existing_product_root(self) -> None:
        request = GenerationRequest(
            product_root=Path("/path/that/does/not/exist"),
            output_root=Path("/tmp/output"),
            selected_sizes=("1440x1440",),
        )
        with self.assertRaises(ValueError):
            request.validate()

    def test_request_requires_material_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = GenerationRequest(
                product_root=Path(temp_dir),
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                material_confirmation_completed=False,
            )
            with self.assertRaises(ValueError):
                request.validate()

    def test_request_rejects_non_positive_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = GenerationRequest(
                product_root=Path(temp_dir),
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                max_size_mb=0,
            )
            with self.assertRaises(ValueError):
                request.validate()

    def test_generation_payload_keeps_v26_optional_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = _generation_request(
                {
                    "product_root": temp_dir,
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                    "logo_overrides": {"logo": "/tmp/logo.png"},
                    "folder_category_overrides": {"黑色": "long_pants"},
                    "include_selling_point_images": True,
                    "selling_points": ["防风", "显瘦"],
                }
            )
            self.assertEqual(request.logo_overrides["logo"], "/tmp/logo.png")
            self.assertEqual(request.folder_category_overrides["黑色"], "long_pants")
            self.assertTrue(request.include_selling_point_images)
            self.assertEqual(request.selling_points, ("防风", "显瘦"))

    def test_generation_payload_rejects_string_selling_points(self) -> None:
        with self.assertRaises(ValueError):
            _generation_request({"selected_sizes": ["1440x1440"], "selling_points": "防风"})

    def test_adapter_imports_v26_engine_without_qt(self) -> None:
        adapter = V26EngineAdapter()
        generate_images, resource_root = adapter._load_engine()
        self.assertTrue(callable(generate_images))
        self.assertTrue(resource_root.is_dir())

    def test_runtime_emits_progress_and_done(self) -> None:
        events: list[tuple[str, dict]] = []

        class FakeAdapter:
            def run(self, request, progress, cancel_event):
                progress(1, 2, "分类")
                progress(2, 2, "生成")
                return {"cancelled": False, "outputs": 2}

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = WorkerRuntime(
                adapter=FakeAdapter(),
                emit=lambda _request_id, event_type, payload: events.append((event_type, payload)),
            )
            runtime.start(
                "job-1",
                {
                    "product_root": temp_dir,
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                },
            )
            deadline = time.monotonic() + 2
            while runtime.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(runtime.busy)
            self.assertEqual([event[0] for event in events], ["progress", "progress", "done"])

    def test_runtime_cancel_sets_event_and_emits_cancelled(self) -> None:
        events: list[tuple[str, dict]] = []
        started = threading.Event()

        class FakeAdapter:
            def run(self, request, progress, cancel_event):
                started.set()
                while not cancel_event.is_set():
                    time.sleep(0.005)
                return {"cancelled": True}

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = WorkerRuntime(
                adapter=FakeAdapter(),
                emit=lambda _request_id, event_type, payload: events.append((event_type, payload)),
            )
            runtime.start(
                "job-2",
                {
                    "product_root": temp_dir,
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                },
            )
            self.assertTrue(started.wait(1))
            self.assertTrue(runtime.cancel("job-2"))
            deadline = time.monotonic() + 2
            while runtime.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn("cancelled", [event[0] for event in events])

    def test_runtime_reports_engine_errors(self) -> None:
        events: list[tuple[str, dict]] = []

        class FakeAdapter:
            def run(self, request, progress, cancel_event):
                raise RuntimeError("synthetic failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = WorkerRuntime(
                adapter=FakeAdapter(),
                emit=lambda _request_id, event_type, payload: events.append((event_type, payload)),
            )
            runtime.start(
                "job-3",
                {
                    "product_root": temp_dir,
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                },
            )
            deadline = time.monotonic() + 2
            while runtime.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(events[-1][0], "error")
            self.assertEqual(events[-1][1]["code"], "generation_failed")

    def test_probe_without_models_does_not_start_inference(self) -> None:
        runtime = WorkerRuntime(adapter=V26EngineAdapter())
        result = runtime.probe_models("/path/that/does/not/exist")
        self.assertEqual(result["pose"]["state"], "未配置")
        self.assertEqual(result["segmentation"]["state"], "未配置")


if __name__ == "__main__":
    unittest.main()
