from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pixelflow_core.classification import ClassificationRequest
from pixelflow_core.v3_classification import V3ClassifierAdapter


class V3ClassificationTests(unittest.TestCase):
    def test_native_rules_do_not_load_legacy_category_module(self) -> None:
        worker_root = Path(__file__).resolve().parents[1]
        script = """
import sys
import tempfile
from pathlib import Path
from PIL import Image
from pixelflow_core.engine import GenerationRequest, V3EngineAdapter
with tempfile.TemporaryDirectory() as root:
    product = Path(root) / '运动鞋'
    product.mkdir()
    Image.new('RGB', (32, 32), 'white').save(product / '男_黑_01.jpg')
    V3EngineAdapter().run(GenerationRequest(
        product_root=product,
        output_root=Path(root) / 'output',
        selected_sizes=('1440x1440',),
        include_logo=False,
        include_vip=False,
        include_model_images=True,
        local_model_enabled=True,
        local_model_path=str(Path(root) / 'models'),
        mode='v3',
    ))
assert 'pixelflow_core.legacy_engine.category_engine.source_type_classifier' not in sys.modules
assert 'pixelflow_core.legacy_engine.category_engine.category_classifier' not in sys.modules
assert 'pixelflow_core.legacy_engine.local_model_assistant' not in sys.modules
assert 'pixelflow_core.shoe_composition' not in sys.modules
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=worker_root,
            env={"PYTHONPATH": str(worker_root), **{key: value for key, value in os.environ.items() if key != "PYTHONPATH"}},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_rules_emit_source_and_category_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "运动鞋"
            product.mkdir()
            source = product / "男_黑_01.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            decision = V3ClassifierAdapter().classify(ClassificationRequest(product))[0]

        self.assertEqual(decision.source_type, "main_product")
        self.assertEqual(decision.classifier, "v3_rules")
        self.assertEqual((decision.metadata or {})["category"], "shoes")
        self.assertEqual((decision.metadata or {})["classification_facade"], "v3_rules")

    def test_model_request_uses_native_pose_seg_facade_and_explicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "模特图"
            product.mkdir()
            source = product / "model.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            decision = V3ClassifierAdapter().classify(
                ClassificationRequest(product, local_model_enabled=True, local_model_path="/tmp/models")
            )[0]

        metadata = decision.metadata or {}
        self.assertEqual(decision.source_type, "model")
        self.assertEqual(metadata["model_facade"]["status"], "available")
        self.assertEqual(metadata["pose_evidence"]["status"], "not_run")
        self.assertTrue(metadata["fallback"])
        self.assertIn("ONNX", str(metadata["fallback_reason"]))

    def test_native_model_facade_emits_typed_plan(self) -> None:
        class FakeModelService:
            def compose_for_sizes(self, _root, _source, _sizes, product_category=None):
                return {
                    "model_view": "lower_body",
                    "confidence": 0.92,
                    "person_count": 1,
                    "person_bboxes": [[10, 10, 90, 95]],
                    "body_visibility": {
                        "feet": True,
                        "visible_keypoints": 6,
                        "person_box_height_ratio": 0.85,
                        "person_box_width_ratio": 0.55,
                        "pose_geometry_score": 0.8,
                        "relief_score": 0.8,
                    },
                    "by_size": {
                        "1440x1440": {
                            "composition_mode": "zoom_focus",
                            "target_crop_height_ratio": 0.9,
                            "scale_adjustment_percent": 0,
                            "position_x": 0.5,
                            "position_y": 0.5,
                        }
                    },
                }

            def segment(self, _root, _source):
                return {
                    "available": True,
                    "confidence": 0.8,
                    "mask_area_ratio": 0.2,
                    "mask_edge_ratio": 0.1,
                }

        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "运动鞋"
            product.mkdir()
            source = product / "model.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            request = ClassificationRequest(
                product,
                local_model_enabled=True,
                local_model_path=str(Path(root) / "models"),
                category="shoes",
            )
            with patch("pixelflow_core.model_service.configured_model_candidates", return_value=(Path("model.onnx"),)), patch(
                "pixelflow_core.model_service.get_model_process_service", return_value=FakeModelService()
            ):
                decision = V3ClassifierAdapter().classify(request)[0]

        self.assertEqual(decision.source_type, "model")
        self.assertEqual(decision.metadata["model_facade"]["status"], "available")
        self.assertIn("1440x1440", decision.composition_plans)
        self.assertFalse(decision.composition_plans["1440x1440"].fallback)


if __name__ == "__main__":
    unittest.main()
