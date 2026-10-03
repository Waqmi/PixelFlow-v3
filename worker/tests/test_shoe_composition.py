from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

from pixelflow_core.shoe_composition import (
    PoseDetection,
    compose_shoe_composition,
    normalize_shoe_composition,
)


SIZES = {
    "1440x1440": ((1440, 1440), (255, 255, 255)),
    "1440x1920": ((1440, 1920), (239, 239, 239)),
}


def _points(**overrides: tuple[float, float, float]) -> tuple[tuple[float, float, float], ...]:
    points = [(0.0, 0.0, 0.0)] * 17
    for index, point in overrides.items():
        points[int(index)] = point
    return tuple(points)


class ShoeCompositionTests(unittest.TestCase):
    def test_compose_does_not_pollute_import_path(self) -> None:
        detection = PoseDetection((400.0, 100.0, 1600.0, 1800.0), 0.92)
        before = list(sys.path)
        compose_shoe_composition(detection, (2000, 2000), SIZES)
        self.assertEqual(sys.path, before)

    def test_real_shoe_matrix_is_auditable_and_keeps_skipped_fixtures_explicit(self) -> None:
        root = Path(__file__).resolve().parents[2]
        matrix_path = root / "fixtures" / "shoe-matrix.json"
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        source_root = (root / matrix["source_root"]).resolve()
        if not source_root.is_dir():
            self.skipTest(f"shoe fixtures are unavailable: {source_root}")

        fixtures = matrix["fixtures"]
        self.assertEqual(matrix["fixture_count"], len(fixtures))
        self.assertGreater(len(fixtures), 0)
        fixture_ids = {item["id"] for item in fixtures}
        for item in fixtures:
            source = source_root / item["relative_path"]
            self.assertTrue(source.is_file(), source)
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                item["sha256"],
            )

        expected_cases = {
            "normal_stance",
            "large_bend",
            "single_foot",
            "crossed_legs",
            "foot_occlusion",
            "sole_facing_camera",
            "person_frame_edge",
            "multiple_people",
        }
        cases = {item["case"]: item for item in matrix["cases"]}
        self.assertEqual(set(cases), expected_cases)
        self.assertEqual(cases["multiple_people"]["status"], "skipped")
        self.assertEqual(cases["multiple_people"]["output_status"], "skipped")
        for case in cases.values():
            self.assertTrue(set(case["source_ids"]).issubset(fixture_ids))
            self.assertIn(case["pose_evidence"]["status"], {"not_run", "missing_fixture", "skipped"})
            self.assertIn(case["segmentation_evidence"]["status"], {"not_run", "missing_fixture", "skipped"})

        evidence = json.loads((root / "baselines" / "archive" / "2026-09-27-v3-recheck" / "shoe-composition-evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "replayable_cached_model_evidence")
        self.assertEqual(evidence["counts"]["pose"], 20)
        self.assertEqual(evidence["counts"]["segmentation"], 2)
        self.assertEqual(evidence["counts"]["typed_plans"], 60)
        self.assertEqual(
            {entry["source_sha256"] for entry in evidence["entries"] if entry.get("pose_cache")},
            {item["sha256"] for item in fixtures},
        )

    def test_normal_stance_protects_both_feet_without_risk(self) -> None:
        detection = PoseDetection(
            (400.0, 100.0, 1600.0, 1800.0),
            0.92,
            _points(
                **{
                    "15": (800.0, 1700.0, 0.95),
                    "16": (1200.0, 1720.0, 0.95),
                }
            ),
        )
        result = compose_shoe_composition(detection, (2000, 2000), SIZES)

        self.assertFalse(result.shoe_risk)
        self.assertIsNone(result.review_hint)
        self.assertEqual(
            result.composition_plans["1440x1440"].evidence["composition_rules_version"],
            "v3-composition-10",
        )
        protected = result.by_size["1440x1440"].protected_crop_bbox
        self.assertIsNotNone(protected)
        assert protected is not None
        self.assertLess(protected[0], 800.0)
        self.assertGreater(protected[2], 1200.0)
        self.assertGreater(protected[3], detection.bbox[3])

    def test_aggressive_action_uses_wider_review_crop(self) -> None:
        detection = PoseDetection(
            (120.0, 100.0, 1950.0, 1985.0),
            0.9,
            _points(
                **{
                    "11": (700.0, 500.0, 0.9),
                    "13": (900.0, 900.0, 0.9),
                    "15": (1000.0, 700.0, 0.9),
                }
            ),
        )
        result = compose_shoe_composition(detection, (2000, 2000), SIZES)

        self.assertTrue(result.shoe_risk)
        self.assertEqual(result.review_hint, "鞋部姿态复杂，已使用保守构图，请人工确认")
        self.assertIn("腿部动作幅度大", result.shoe_risk_reasons)
        self.assertIn("人物框贴边", result.shoe_risk_reasons)
        self.assertGreaterEqual(result.by_size["1440x1440"].target_crop_height_ratio or 0.0, 0.72)

    def test_occluded_edge_foot_is_conservative(self) -> None:
        detection = PoseDetection(
            (0.0, 100.0, 1800.0, 1990.0),
            0.88,
            _points(
                **{
                    "11": (700.0, 500.0, 0.9),
                    "13": (900.0, 900.0, 0.9),
                    "15": (1000.0, 700.0, 0.9),
                }
            ),
        )
        result = compose_shoe_composition(detection, (2000, 2000), SIZES)

        self.assertTrue(result.shoe_risk)
        self.assertIn("单脚或脚部遮挡", result.shoe_risk_reasons)
        self.assertIn("人物框贴边", result.shoe_risk_reasons)
        self.assertTrue(all(plan.review_hint for plan in result.by_size.values()))

    def test_crossed_legs_and_sole_facing_are_flagged(self) -> None:
        detection = PoseDetection(
            (200.0, 100.0, 1800.0, 1900.0),
            0.9,
            _points(
                **{
                    "11": (900.0, 700.0, 0.95),
                    "12": (1100.0, 700.0, 0.95),
                    "13": (800.0, 1100.0, 0.95),
                    "14": (1200.0, 1100.0, 0.95),
                    "15": (1250.0, 900.0, 0.95),
                    "16": (750.0, 900.0, 0.95),
                }
            ),
        )
        result = compose_shoe_composition(detection, (2000, 2000), SIZES)

        self.assertTrue(result.shoe_risk)
        self.assertIn("交叉腿", result.shoe_risk_reasons)
        self.assertIn("鞋底朝镜头", result.shoe_risk_reasons)
        self.assertTrue(all(plan.requires_review for plan in result.composition_plans.values()))

    def test_multi_person_and_segmentation_gate_are_auditable(self) -> None:
        raw = {
            "person_count": 2,
            "person_bboxes": [[0, 100, 900, 1900], [1000, 100, 2000, 1900]],
            "by_size": {
                "1440x1440": {
                    "protected_crop_bbox": [300, 1200, 800, 1900],
                    "confidence": 0.8,
                }
            },
        }
        generic = normalize_shoe_composition(
            {**raw, "segmentation": {"mask_bbox_ratio": [0.0, 0.0, 1.0, 1.0]}}
        )
        reliable = normalize_shoe_composition(
            raw,
            segmentation_evidence={
                "reliable_shoe_contour": {"reliable": True, "bbox": [450, 1300, 850, 1850]}
            },
        )

        self.assertIn("双人/多人", generic.shoe_risk_reasons)
        self.assertTrue(generic.skipped_by_scope)
        self.assertTrue(generic.composition_plans["1440x1440"].skipped_by_scope)
        self.assertTrue(generic.composition_plans["1440x1440"].review_hint)
        self.assertEqual(
            generic.composition_plans["1440x1440"].protected_regions[0].bbox,
            (300.0, 1200.0, 800.0, 1900.0),
        )
        self.assertEqual(
            reliable.composition_plans["1440x1440"].protected_regions[0].bbox,
            (450.0, 1300.0, 850.0, 1850.0),
        )


if __name__ == "__main__":
    unittest.main()
