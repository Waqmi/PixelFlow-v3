from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pixelflow_core.classification import (
    ClassificationDecision,
    ClassificationRequest,
    V26ClassifierAdapter,
    _merge_composition_metadata,
    classification_summary,
    _material_sources,
)
from pixelflow_core.worker import WorkerRuntime, _preflight_materials


class ClassificationAdapterTests(unittest.TestCase):
    def test_webp_is_a_supported_v3_material_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            from PIL import Image

            Image.new("RGB", (8, 8), "white").save(root / "source.webp", format="WEBP")
            self.assertEqual(_material_sources(root), [root / "source.webp"])

    def test_bundled_classifier_does_not_modify_import_path(self) -> None:
        before = list(sys.path)
        V26ClassifierAdapter(bundled_only=True)._load_classifier()
        self.assertEqual(sys.path, before)

    def test_missing_pose_model_is_explicit_fallback_and_typed(self) -> None:
        adapter = V26ClassifierAdapter()
        adapter._load_classifier()
        from pixelflow_core.legacy_engine.category_engine.source_type_classifier import SourceTypeDecision

        source = Path("模特图/model.jpg")
        decisions = {
            source: SourceTypeDecision("model", 0.9, "rules", "model"),
        }
        with patch("pixelflow_core.model_service.configured_model_candidates", return_value=[]):
            adapter._apply_optional_models(decisions, "/tmp/empty-models", "shoes", None)

        metadata = decisions[source].metadata or {}
        self.assertTrue(metadata["fallback"])
        self.assertIn("ONNX", str(metadata["fallback_reason"]))
        self.assertEqual(metadata["pose_evidence"]["status"], "not_run")
        plan = metadata["composition_plans"]["1440x1440"]
        self.assertTrue(plan["fallback"])
        self.assertTrue(plan["fallback_reason"])

    def test_typed_composition_plan_keeps_source_for_replay(self) -> None:
        source = "/tmp/shoe.jpg"
        metadata = _merge_composition_metadata(
            {},
            {"by_size": {"1440x1440": {"protected_crop_bbox": [1, 2, 10, 20]}}},
            "shoes",
            source=source,
            purpose="pose",
        )
        self.assertEqual(metadata["composition_plans"]["1440x1440"]["source"], source)

    def test_classification_summary_counts_skipped_and_fallback(self) -> None:
        decisions = [
            ClassificationDecision("a.jpg", "model", 0.9, "rules", "test", fallback=True),
            ClassificationDecision("b.jpg", "model", 0.9, "rules", "test", skipped_by_scope=True),
        ]
        self.assertEqual(
            classification_summary(decisions),
            {
                "count": 2,
                "fallback_count": 1,
                "skipped_by_scope_count": 1,
                "skipped_by_scope": ["b.jpg"],
            },
        )

    def test_preflight_warns_about_unreadable_material_and_blocks_file_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "broken.jpg").write_text("not an image", encoding="utf-8")
            output = root.parent / f"{root.name}-output"
            self.assertIn("无法读取 1 张图片", _preflight_materials(ClassificationRequest(root, output_root=output))[0])
            output.write_text("file", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "输出位置不是文件夹"):
                _preflight_materials(ClassificationRequest(root, output_root=output))

    def test_preflight_warns_when_output_disk_is_low(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            from PIL import Image

            Image.new("RGB", (8, 8), "white").save(root / "source.jpg")
            output = root.parent / f"{root.name}-output"
            with patch("pixelflow_core.worker.shutil.disk_usage") as disk_usage:
                disk_usage.return_value.free = 199 * 1024 * 1024
                warnings = _preflight_materials(ClassificationRequest(root, output_root=output))
            self.assertTrue(any("低于 200 MB" in warning for warning in warnings))

    def test_fixed_fixture_classifies_without_qt(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
        if not source_root.is_dir():
            self.skipTest("external fixture folder is not present: {}".format(source_root))
        manifest = json.loads((root / "fixtures/manifest.json").read_text(encoding="utf-8"))
        sources = [source_root / item["relative_path"] for item in manifest["fixtures"]]
        progress: list[tuple[int, int, str]] = []

        decisions = V26ClassifierAdapter().classify(
            ClassificationRequest(source_root, source_filter={str(path) for path in sources}),
            progress=lambda completed, total, source, decision: progress.append(
                (completed, total, decision.source_type)
            ),
        )

        self.assertEqual(len(decisions), 16)
        self.assertEqual(len(progress), 16)
        self.assertIn("main_product", {decision.source_type for decision in decisions})

    def test_existing_generated_output_is_not_reclassified(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
        if not source_root.is_dir():
            self.skipTest("external fixture folder is not present: {}".format(source_root))
        decisions = V26ClassifierAdapter().classify(ClassificationRequest(source_root))

        self.assertEqual(len(decisions), 36)
        self.assertTrue(all("/成品主图/" not in decision.source for decision in decisions))

    def test_invalid_product_root_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            V26ClassifierAdapter().classify(
                ClassificationRequest(Path("/path/that/does/not/exist"))
            )

    def test_manual_overrides_support_all_four_material_roles(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
        if not source_root.is_dir():
            self.skipTest("external fixture folder is not present: {}".format(source_root))
        sources = sorted(source_root.glob("FRPN015-1 男 暗夜黑/*"))[:4]
        roles = ("main_product", "detail", "model", "model_detail")
        decisions = V26ClassifierAdapter().classify(
            ClassificationRequest(
                source_root,
                source_filter={str(source) for source in sources},
                overrides={str(source): role for source, role in zip(sources, roles)},
            )
        )

        self.assertEqual([decision.source_type for decision in decisions], list(roles))

    def test_model_fixture_manifest_is_available_and_rule_classifies_model(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "fixtures/model-manifest.json").read_text(encoding="utf-8"))
        source_root = (root / manifest["source_root"]).resolve()
        sources = [source_root / item["relative_path"] for item in manifest["fixtures"]]

        # External fixture folder is machine-local; skip when absent.
        if not source_root.is_dir():
            self.skipTest("外部模特素材目录不存在 / missing fixture dir: {}".format(source_root))
        missing = [source for source in sources if not source.is_file()]
        if missing:
            self.skipTest("外部模特素材文件缺失 / missing fixture file: {}".format(missing[0]))
        decisions = V26ClassifierAdapter().classify(
            ClassificationRequest(source_root, source_filter={str(source) for source in sources})
        )

        self.assertEqual(len(decisions), 4)
        self.assertTrue(all(decision.source_type == "model" for decision in decisions))

    def test_product_folder_evidence_skips_pose_and_seg(self) -> None:
        adapter = V26ClassifierAdapter()
        adapter._load_classifier()
        from pixelflow_core.legacy_engine.category_engine.source_type_classifier import SourceTypeDecision

        product = Path("SKU-1 男 暗夜黑/detail.jpg")
        model = Path("模特图/model.jpg")
        calls = []

        class FakeService:
            def compose_for_sizes(self, _root, source, _sizes, product_category=None):
                calls.append(source)
                return {}

            def segment(self, _root, source):
                calls.append(source)
                return {}

        decisions = {
            product: SourceTypeDecision("detail", 0.86, "rules_product_folder", "product"),
            model: SourceTypeDecision("model", 0.96, "rules", "model"),
        }
        with patch("pixelflow_core.model_service.configured_model_candidates", return_value=[Path("model.onnx")]), patch("pixelflow_core.model_service.get_model_process_service", return_value=FakeService()):
            adapter._apply_optional_models(decisions, "/tmp/models", "long_pants", None)
        self.assertTrue(calls)
        self.assertTrue(all(source == model for source in calls))

    def test_optional_model_stages_report_pose_before_seg(self) -> None:
        adapter = V26ClassifierAdapter()
        adapter._load_classifier()
        from pixelflow_core.legacy_engine.category_engine.source_type_classifier import SourceTypeDecision

        source = Path("模特图/model.jpg")
        stages: list[tuple[str, int, int, Path]] = []

        class FakeService:
            def compose_for_sizes(self, _root, _source, _sizes, product_category=None, segmentation=None):
                return {}

            def segment(self, _root, _source):
                return {}

        decisions = {
            source: SourceTypeDecision("model", 0.50, "rules", "model"),
        }
        with patch("pixelflow_core.model_service.configured_model_candidates", return_value=[Path("model.onnx")]), patch("pixelflow_core.model_service.get_model_process_service", return_value=FakeService()):
            adapter._apply_optional_models(
                decisions,
                "/tmp/models",
                "long_pants",
                None,
                lambda stage, completed, total, path: stages.append((stage, completed, total, path)),
            )

        self.assertEqual([stage[0] for stage in stages], ["Pose", "Pose", "Pose", "Seg"])
        self.assertEqual(
            [(stage[1], stage[2]) for stage in stages],
            [(0, 1), (0, 1), (1, 1), (1, 1)],
        )

    def _sampled_detail_folder(self, model_indexes: set[int]) -> tuple[list[Path], dict, list[Path]]:
        adapter = V26ClassifierAdapter()
        adapter._load_classifier()
        from pixelflow_core.legacy_engine.category_engine.source_type_classifier import SourceTypeDecision

        folder = Path("素材")
        sources = [folder / f"image-{index:02d}.jpg" for index in range(12)]
        decisions = {
            source: SourceTypeDecision("detail", 0.86, "rules", "edge")
            for source in sources
        }
        calls = []

        class FakeService:
            def compose_for_sizes(self, _root, source, _sizes, product_category=None, segmentation=None):
                calls.append(source)
                return {}

        def resolve(decision, _composition, _category):
            source = calls[-1]
            if int(source.stem.split("-")[1]) in model_indexes:
                return type(decision)("model", 0.9, "mock_pose", "sample")
            return decision

        with patch("pixelflow_core.model_service.configured_model_candidates", return_value=[Path("model.onnx")]), patch(
            "pixelflow_core.model_service.get_model_process_service", return_value=FakeService()
        ), patch("pixelflow_core.legacy_engine.local_model_assistant.resolve_model_source_type", side_effect=resolve):
            adapter._apply_optional_models(decisions, "/tmp/models", "shirt", None)
        return sources, decisions, calls

    def test_detail_folder_samples_five_images_evenly(self) -> None:
        sources, decisions, calls = self._sampled_detail_folder(set())
        # 12 images -> indexes 0, 3, 6, 8, 11 (both ends included).
        self.assertEqual([source.name for source in calls], [f"image-{index:02d}.jpg" for index in (0, 3, 6, 8, 11)])
        self.assertTrue(all(decision.source_type == "detail" for decision in decisions.values()))

    def test_model_sample_runs_pose_for_every_sibling(self) -> None:
        sources, decisions, calls = self._sampled_detail_folder({6, 7})
        # Every image is checked individually; no sibling inherits a plan-less label.
        self.assertEqual(sorted(calls), sorted(sources))
        self.assertEqual(decisions[sources[7]].source_type, "model")
        self.assertEqual(decisions[sources[1]].source_type, "detail")

    def test_shoe_risk_from_nested_size_plan_is_promoted_to_metadata(self) -> None:
        adapter = V26ClassifierAdapter()
        adapter._load_classifier()
        from pixelflow_core.legacy_engine.category_engine.source_type_classifier import SourceTypeDecision

        source = Path("模特图/shoe.jpg")
        composition = {
            "model_view": "lower_body",
            "confidence": 0.9,
            "person_count": 1,
            "by_size": {
                "1440x1440": {
                    "composition_mode": "zoom_focus",
                    "target_crop_height_ratio": 0.72,
                    "protected_crop_bbox": [100.0, 900.0, 1900.0, 1990.0],
                    "shoe_risk": True,
                    "shoe_risk_reasons": ["腿部动作幅度大"],
                    "review_hint": "鞋部姿态复杂，已使用保守构图，请人工确认",
                }
            },
        }

        class FakeService:
            def compose_for_sizes(self, _root, _source, _sizes, product_category=None, segmentation=None):
                return composition

        decisions = {
            source: SourceTypeDecision("model", 0.9, "rules", "model"),
        }
        with patch("pixelflow_core.model_service.configured_model_candidates", return_value=[Path("model.onnx")]), patch(
            "pixelflow_core.model_service.get_model_process_service", return_value=FakeService()
        ):
            adapter._apply_optional_models(decisions, "/tmp/models", "shoes", None)

        metadata = decisions[source].metadata or {}
        self.assertTrue(metadata["shoe_risk"])
        self.assertEqual(metadata["shoe_risk_reasons"], ["腿部动作幅度大"])
        self.assertEqual(metadata["review_hint"], "鞋部姿态复杂，已使用保守构图，请人工确认")
        self.assertEqual(metadata["by_size"], composition["by_size"])
        self.assertTrue(metadata["shoe_composition"]["by_size"]["1440x1440"]["shoe_risk"])


class ClassificationRuntimeTests(unittest.TestCase):
    def test_runtime_reports_structured_preflight_errors(self) -> None:
        events: list[tuple[str, dict]] = []
        runtime = WorkerRuntime(emit=lambda _request_id, event_type, payload: events.append((event_type, payload)))

        runtime.start_classification("missing-input", {"product_root": "/path/that/does/not/exist"})
        self.assertEqual(events[-1][1]["code"], "input_path_missing")
        self.assertIn("输入路径不存在", events[-1][1]["message"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime.start_classification("empty-input", {"product_root": str(root)})
            self.assertEqual(events[-1][1]["code"], "no_images")

    def test_runtime_emits_classification_progress_and_done(self) -> None:
        events: list[tuple[str, dict]] = []

        class FakeClassifier:
            def classify(self, request, progress):
                decision = ClassificationDecision(
                    source="a.jpg",
                    source_type="main_product",
                    confidence=0.9,
                    classifier="rules",
                    reason="test",
                )
                progress(1, 1, Path("a.jpg"), decision)
                return [decision]

        with tempfile.TemporaryDirectory() as temp_dir:
            from PIL import Image

            Image.new("RGB", (8, 8), "white").save(Path(temp_dir) / "source.jpg")
            runtime = WorkerRuntime(
                classifier=FakeClassifier(),
                emit=lambda _request_id, event_type, payload: events.append((event_type, payload)),
            )
            runtime.start_classification("classify-1", {"product_root": temp_dir})
            deadline = time.monotonic() + 2
            while runtime.busy and time.monotonic() < deadline:
                time.sleep(0.01)

        self.assertFalse(runtime.busy)
        self.assertEqual([event[0] for event in events], ["progress", "done"])
        self.assertEqual(events[-1][1]["result"]["decisions"][0].source_type, "main_product")
        self.assertEqual(events[-1][1]["result"]["summary"]["count"], 1)

    def test_runtime_reports_classification_errors(self) -> None:
        events: list[tuple[str, dict]] = []

        class FailingClassifier:
            def classify(self, request, progress):
                raise RuntimeError("synthetic classification failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            from PIL import Image

            Image.new("RGB", (8, 8), "white").save(Path(temp_dir) / "source.jpg")
            runtime = WorkerRuntime(
                classifier=FailingClassifier(),
                emit=lambda _request_id, event_type, payload: events.append((event_type, payload)),
            )
            runtime.start_classification("classify-2", {"product_root": temp_dir})
            deadline = time.monotonic() + 2
            while runtime.busy and time.monotonic() < deadline:
                time.sleep(0.01)

        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["code"], "classification_failed")


class AnchoredCloseupSourceTypeTests(unittest.TestCase):
    def _resolve(self, category: str, mode: str, source_type: str):
        from pixelflow_core.legacy_engine.category_engine.source_type_classifier import SourceTypeDecision
        from pixelflow_core.model_assistant import resolve_model_source_type

        composition = {
            "model_view": "detail",
            "confidence": 0.8,
            "person_count": 1,
            "body_visibility": {
                "visible_keypoints": 3, "person_box_height_ratio": 0.5,
                "person_box_width_ratio": 0.9, "pose_geometry_score": 0.1,
                "relief_score": 0.6, "hands": True,
            },
            "by_size": {"1440x1440": {"composition_mode": mode, "crop_box": [0, 0, 1, 1]}},
        }
        rule = SourceTypeDecision(source_type, 0.6, "rules", "test")
        return resolve_model_source_type(rule, composition, category)

    def test_hat_and_gloves_anchored_closeups_are_model_without_hint(self) -> None:
        for category in ("hat", "gloves"):
            decision = self._resolve(category, "anchor_crop", "model_detail")
            self.assertEqual(decision.source_type, "model")
            self.assertNotIn("review_hint", decision.metadata)

    def test_other_categories_and_unanchored_stay_model_detail(self) -> None:
        self.assertEqual(self._resolve("shirt", "anchor_crop", "model_detail").source_type, "model_detail")
        self.assertEqual(self._resolve("hat", "zoom_focus", "model_detail").source_type, "model_detail")


if __name__ == "__main__":
    unittest.main()
