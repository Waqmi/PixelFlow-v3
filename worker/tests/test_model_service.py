from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pixelflow_core.model_service import (
    STATE_FOUND,
    STATE_READY,
    STATE_UNAVAILABLE,
    STATE_UNCONFIGURED,
    ModelProcessService,
)


class FastProbeTests(unittest.TestCase):
    """The default probe must answer from the filesystem alone.

    It used to create an ONNX Runtime session for every configured model and
    hash each file, which made the settings page wait for minutes on slower
    machines.  ``deep=True`` keeps that behaviour for the explicit load test.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.service = ModelProcessService()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, purpose: str, name: str, size: int = 16) -> Path:
        directory = self.root / purpose
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_bytes(b"\0" * size)
        return path

    def test_empty_root_reports_unconfigured_without_loading(self) -> None:
        result = self.service.probe_models(self.root)

        for purpose in ("pose", "segmentation", "detection"):
            self.assertEqual(result[purpose]["state"], STATE_UNCONFIGURED)
            self.assertFalse(result[purpose]["loaded"])

    def test_fast_probe_never_creates_a_model_session(self) -> None:
        self._write("pose", "yolo11n-pose.onnx")
        self._write("segmentation", "yolo11n-seg.onnx")

        def explode(*_args, **_kwargs):
            raise AssertionError("the fast probe must not load a model")

        self.service._request = explode  # type: ignore[method-assign]
        result = self.service.probe_models(self.root)

        self.assertEqual(result["pose"]["state"], STATE_FOUND)
        self.assertEqual(result["segmentation"]["state"], STATE_FOUND)
        self.assertFalse(result["pose"]["loaded"])
        self.assertEqual(result["pose"]["model"], "yolo11n-pose.onnx")

    def test_small_models_still_report_a_digest(self) -> None:
        self._write("pose", "yolo11n-pose.onnx")

        result = self.service.probe_models(self.root)

        self.assertIn("sha256", result["pose"]["model_version"])

    def test_detection_without_classes_file_is_unavailable(self) -> None:
        self._write("detection", "yolov8m-worldv2-pixelflow.onnx")

        result = self.service.probe_models(self.root)

        self.assertEqual(result["detection"]["state"], STATE_UNAVAILABLE)
        self.assertIn(".classes.json", result["detection"]["error"])

    def test_deep_probe_marks_loaded_models(self) -> None:
        self._write("pose", "yolo11n-pose.onnx")
        self._write("segmentation", "yolo11n-seg.onnx")

        self.service._request = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
            "pose": {"state": STATE_READY, "status": "ready", "fallback": False},
            "segmentation": {"state": STATE_UNAVAILABLE, "status": "unavailable", "error": "boom"},
            "detection": {"state": STATE_UNCONFIGURED, "status": "unconfigured", "fallback": True},
        }
        result = self.service.probe_models(self.root, deep=True)

        self.assertTrue(result["pose"]["loaded"])
        self.assertFalse(result["segmentation"]["loaded"])
        self.assertEqual(result["segmentation"]["state"], STATE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
