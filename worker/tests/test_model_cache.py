from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import tempfile
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
    ModelProcessService,
    _worker_main,
)


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
    def test_probe_returns_sha256_for_configured_models(self) -> None:
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
                self.assertEqual(result[purpose]["model_version"]["sha256"], hashlib.sha256(content).hexdigest())

    def test_detection_probe_reports_unavailable_without_sidecar(self) -> None:
        class Assistant:
            def __init__(self, model_path):
                self.model_path = model_path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detection_dir = root / "detection"
            detection_dir.mkdir()
            (detection_dir / "model.onnx").write_bytes(b"detection-model")
            with patch("pixelflow_core.model_service._assistant_types", return_value=(Assistant, Assistant, Assistant)):
                service = ModelProcessService(timeout=1)
                try:
                    result = service.probe_models(root)
                finally:
                    service.close()
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


if __name__ == "__main__":
    unittest.main()
