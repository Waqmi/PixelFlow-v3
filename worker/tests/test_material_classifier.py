from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from pixelflow_core.classification import ClassificationRequest
from pixelflow_core.material_classifier import CategoryClassifier, MaterialClassifier


class MaterialClassifierTests(unittest.TestCase):
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

    def test_rules_keep_the_confirmed_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "运动鞋"
            product.mkdir()
            source = product / "男_黑_01.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            decision = MaterialClassifier().classify(ClassificationRequest(product))[0]

        self.assertEqual(decision.source_type, "main_product")
        self.assertEqual(decision.classifier, "rules")
        self.assertEqual(decision.reason, "性别-颜色-序号命名的商品素材")

    def test_cut_sides_are_measured_for_the_confirm_slider(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "细节"
            product.mkdir()
            source = product / "detail.jpg"
            image = Image.new("RGB", (200, 200), "white")
            ImageDraw.Draw(image).rectangle((0, 0, 199, 199), fill="white")
            ImageDraw.Draw(image).rectangle((0, 40, 199, 160), fill="black")
            image.save(source)
            decision = MaterialClassifier().classify(ClassificationRequest(product))[0]

        self.assertEqual(decision.source_type, "detail")
        cut = (decision.metadata or {}).get("cut_sides")
        self.assertEqual(cut, {"left": True, "right": True, "top": False, "bottom": False})

    def test_category_uses_the_nearest_path_level(self) -> None:
        """A two-category path keeps the closest folder instead of ``other``."""
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "冲锋裤"
            nested = product / "上衣"
            nested.mkdir(parents=True)
            source = nested / "look.jpg"
            Image.new("RGB", (32, 32), "white").save(source)

            decision = CategoryClassifier().classify(product, [source])

        self.assertEqual(decision.category, "shirt")
        self.assertEqual(decision.matched_keyword, "上衣")

    def test_category_still_reports_a_same_level_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "混合素材"
            product.mkdir()
            shoes = product / "运动鞋.jpg"
            pants = product / "冲锋裤.jpg"
            for source in (shoes, pants):
                Image.new("RGB", (32, 32), "white").save(source)

            decision = CategoryClassifier().classify(product, [shoes, pants])

        self.assertEqual(decision.category, "other")
        self.assertEqual(decision.source, "conflict")

    def test_main_product_carries_the_baseline_canvas(self) -> None:
        """The confirm guide needs the photo rectangle for every output size."""
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "长裤"
            product.mkdir()
            source = product / "IMG_1.jpg"
            image = Image.new("RGB", (400, 800), "white")
            ImageDraw.Draw(image).rectangle((140, 60, 260, 740), fill="black")
            image.save(source)
            decision = MaterialClassifier().classify(ClassificationRequest(product))[0]

        metadata = decision.metadata or {}
        self.assertEqual(decision.source_type, "main_product")
        # The measurement runs on a 128px preview, so the aspect is coarse.
        self.assertAlmostEqual(float(metadata["subject_aspect"]), 120 / 680, delta=0.01)
        self.assertAlmostEqual(float(metadata["image_aspect"]), 0.5, delta=0.01)
        canvas = metadata["baseline_canvas"]
        self.assertEqual(set(canvas), {"1440x1440", "1440x1920", "1125x1500"})
        for frame in canvas.values():
            self.assertEqual(set(frame), {"box", "image", "origin"})
            self.assertEqual(len(frame["image"]), 4)
            self.assertEqual(len(frame["origin"]), 2)
            self.assertGreater(frame["box"][0], 0)
            self.assertLessEqual(max(frame["box"]), 1.0)
        # long_pants uses 0.748 for 1440x1920 and the subject is height-bound.
        self.assertAlmostEqual(canvas["1440x1920"]["box"][1], 0.748, places=3)

    def test_baseline_canvas_follows_the_renderer_formula(self) -> None:
        from pixelflow_core.renderer import subject_canvas_frame

        # A subject covering 20% x 80% of a square photo, height-limited in a
        # square canvas at 0.7: it fills 70% of the height and 17.5% of the
        # width, and the photo lands centred at 87.5% of the canvas.
        frame = subject_canvas_frame((0.4, 0.1, 0.6, 0.9), 1.0, (0.7, 0.5, 0.5), (1000, 1000))
        self.assertAlmostEqual(frame["box"][0], 0.175)
        self.assertAlmostEqual(frame["box"][1], 0.7)
        self.assertAlmostEqual(frame["image"][0], 0.0625)
        self.assertAlmostEqual(frame["image"][1], 0.0625)
        self.assertAlmostEqual(frame["image"][2], 0.875)
        self.assertAlmostEqual(frame["image"][3], 0.875)
        self.assertEqual(frame["origin"], [0.5, 0.5])

    def test_missing_pose_model_keeps_an_explicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "模特图"
            product.mkdir()
            source = product / "model.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            decision = MaterialClassifier().classify(
                ClassificationRequest(product, local_model_enabled=True, local_model_path="/tmp/models")
            )[0]

        metadata = decision.metadata or {}
        self.assertEqual(decision.source_type, "model")
        self.assertEqual(metadata["pose_evidence"]["status"], "not_run")
        self.assertTrue(metadata["fallback"])
        self.assertIn("ONNX", str(metadata["fallback_reason"]))

    def test_confident_white_background_source_is_not_sent_to_pose(self) -> None:
        """The merged classifier keeps the confirmation gate: only uncertain or
        model-looking sources reach Pose, so an approved product photo cannot be
        re-typed by the model."""
        class FakeModelService:
            def __init__(self) -> None:
                self.calls = 0

            def compose_for_sizes(self, *_args, **_kwargs):
                self.calls += 1
                return {"model_view": "upper_body", "confidence": 0.95, "by_size": {}}

            def segment(self, _root, _source):
                return {"available": True, "confidence": 0.8}

        service = FakeModelService()
        with tempfile.TemporaryDirectory() as root:
            product = Path(root) / "上衣"
            product.mkdir()
            # No model keyword in the name: only the pixels decide.
            source = product / "p1.jpg"
            image = Image.new("RGB", (100, 100), "white")
            ImageDraw.Draw(image).rectangle((25, 10, 75, 90), fill="black")
            image.save(source)
            request = ClassificationRequest(
                product,
                local_model_enabled=True,
                local_model_path=str(Path(root) / "models"),
                category="shirt",
            )
            with patch("pixelflow_core.model_service.configured_model_candidates", return_value=(Path("model.onnx"),)), patch(
                "pixelflow_core.model_service.get_model_process_service", return_value=service
            ):
                decision = MaterialClassifier().classify(request)[0]

        self.assertEqual(decision.source_type, "main_product")
        self.assertEqual(service.calls, 0)

    def test_native_model_facade_emits_typed_plan(self) -> None:
        class FakeModelService:
            def compose_for_sizes(self, _root, _source, _sizes, product_category=None, segmentation=None, **_kwargs):
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
                decision = MaterialClassifier().classify(request)[0]

        self.assertEqual(decision.source_type, "model")
        self.assertIn("1440x1440", decision.composition_plans)
        self.assertFalse(decision.composition_plans["1440x1440"].fallback)

    def test_native_model_facade_emits_typed_plan_for_non_shoe_models(self) -> None:
        class FakeModelService:
            def compose_for_sizes(self, _root, _source, _sizes, product_category=None, segmentation=None, **_kwargs):
                return {
                    "model_view": "upper_body",
                    "confidence": 0.92,
                    "person_count": 1,
                    "person_bboxes": [[10, 10, 90, 95]],
                    "body_visibility": {
                        "upper_body": True,
                        "visible_keypoints": 6,
                        "person_box_height_ratio": 0.85,
                        "pose_geometry_score": 0.8,
                        "relief_score": 0.8,
                    },
                    "by_size": {
                        "1440x1440": {
                            "composition_mode": "zoom_focus",
                            "scale_adjustment_percent": 25,
                            "target_crop_height_ratio": 0.7,
                            "position_x": 0.4,
                            "position_y": 0.45,
                            "protected_crop_bbox": [10, 10, 90, 80],
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
            # This folder shape is the real 1000-image batch convention and
            # initially classifies as ``rules_product_folder``.
            product = Path(root) / "FRTJ009-22 男 松石绿"
            product.mkdir()
            source = product / "model.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            request = ClassificationRequest(
                product,
                local_model_enabled=True,
                local_model_path=str(Path(root) / "models"),
                category="shirt",
            )
            with patch("pixelflow_core.model_service.configured_model_candidates", return_value=(Path("model.onnx"),)), patch(
                "pixelflow_core.model_service.get_model_process_service", return_value=FakeModelService()
            ):
                decision = MaterialClassifier().classify(request)[0]

        plan = decision.composition_plans["1440x1440"]
        self.assertAlmostEqual(plan.scale, 1.25)
        self.assertEqual(plan.position_x, 0.4)
        self.assertEqual(plan.position_y, 0.45)
        self.assertEqual(plan.crop_ratio, 0.7)
        self.assertEqual(plan.protected_regions[0].bbox, (10.0, 10.0, 90.0, 80.0))
        self.assertFalse(plan.fallback)


if __name__ == "__main__":
    unittest.main()
