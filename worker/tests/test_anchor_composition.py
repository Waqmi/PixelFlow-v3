from __future__ import annotations

import unittest
import unittest.mock

from PIL import Image

from pixelflow_core.contracts import CompositionPlan, RenderRequest
from pixelflow_core.model_assistant import (
    ANCHOR_EDGE_MARGIN,
    ANCHOR_WAIST_ABOVE_HIP,
    ANCHOR_MAX_GROWTH,
    ANCHOR_SHORTS_BELOW_KNEE,
    ANCHOR_SHOE_FILL,
    ANCHOR_SHOE_FILL_MAX,
    LocalDetectionAssistant,
    LocalModelAssistant,
    LocalSegmentationAssistant,
    PersonDetection,
    anchor_crop_box,
)
from pixelflow_core.renderer import Renderer

IMAGE = (1000, 1500)
SQUARE = (1440, 1440)
PORTRAIT = (1440, 1920)


def person(points: dict[int, tuple[float, float]], bbox=(300.0, 100.0, 700.0, 1400.0)) -> PersonDetection:
    keypoints = [(0.0, 0.0, 0.0)] * 17
    for index, (x, y) in points.items():
        keypoints[index] = (x, y, 0.9)
    return PersonDetection(bbox, 0.9, tuple(keypoints))


# Standing model: hips y=700, knees y=1000, ankles y=1300, feet close together.
STANDING = {
    0: (500, 180), 5: (430, 350), 6: (570, 350),
    11: (460, 700), 12: (540, 700),
    13: (460, 1000), 14: (540, 1000),
    15: (470, 1300), 16: (530, 1300),
}


def rows(top: float, bottom: float, left: float, right: float) -> dict[str, object]:
    """Seg evidence with one uniform band, in image ratios."""
    return {"mask_rows": [[top, bottom, left, right]]}


class AnchorCropTests(unittest.TestCase):
    def window(self, result):
        left, top, right, bottom = result["crop_box"]
        return left * IMAGE[0], top * IMAGE[1], right * IMAGE[0], bottom * IMAGE[1]

    def shoe_fill(self, result, left_ratio: float, right_ratio: float) -> float:
        left, _top, right, _bottom = self.window(result)
        return (right_ratio - left_ratio) * IMAGE[0] / (right - left)

    def assert_shoes_inside(self, result, left_ratio: float, right_ratio: float) -> None:
        left, _top, right, bottom = self.window(result)
        self.assertLessEqual(left, left_ratio * IMAGE[0] + 1)
        self.assertGreaterEqual(right, right_ratio * IMAGE[0] - 1)
        self.assertGreaterEqual(bottom, 1400.0 - 1)  # sole

    def test_shoes_default_fill_leaves_side_whitespace(self) -> None:
        # Seg outline of the shoes: 240px wide, sole at y=1400, knee at y=1000.
        result = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "shoes", rows(0.85, 0.9333, 0.38, 0.62))
        self.assertAlmostEqual(self.shoe_fill(result, 0.38, 0.62), ANCHOR_SHOE_FILL, places=2)
        self.assert_shoes_inside(result, 0.38, 0.62)
        self.assertGreater(self.window(result)[1], 1000.0)  # knee out of frame
        self.assertEqual(result["review_reasons"], [])

    def test_shoes_zoom_in_within_limit_to_hide_knee(self) -> None:
        # 250px shoes: 58% fill would show the knee, <=63% fill avoids it.
        result = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "shoes", rows(0.85, 0.9333, 0.375, 0.625))
        fill = self.shoe_fill(result, 0.375, 0.625)
        self.assertGreater(fill, ANCHOR_SHOE_FILL)
        self.assertLessEqual(fill, ANCHOR_SHOE_FILL_MAX + 0.001)
        self.assertAlmostEqual(self.window(result)[1], 1000.0, delta=1.0)  # top just at the knee
        self.assert_shoes_inside(result, 0.375, 0.625)

    def test_shoes_never_exceed_max_fill_or_get_cut(self) -> None:
        # 400px shoes cannot avoid the knee at <=70% fill: the knee may show.
        for size in (SQUARE, PORTRAIT):
            result = anchor_crop_box([person(STANDING)], IMAGE, size, "shoes", rows(0.85, 0.9333, 0.30, 0.70))
            self.assertAlmostEqual(self.shoe_fill(result, 0.30, 0.70), ANCHOR_SHOE_FILL_MAX, places=2)
            self.assert_shoes_inside(result, 0.30, 0.70)
            self.assertLess(self.window(result)[1], 1000.0)
            self.assertEqual(result["review_reasons"], [])

    def test_shoe_sole_uses_the_outer_of_pose_box_and_seg(self) -> None:
        detection = person(STANDING, bbox=(300.0, 100.0, 700.0, 1250.0))
        seg = rows(0.80, 0.95, 0.45, 0.55)  # outline reaches y=1425
        result = anchor_crop_box([detection], IMAGE, SQUARE, "shoes", seg)
        self.assertAlmostEqual(result["anchors"]["bottom"], 0.95, places=3)
        self.assertIn("鞋底边界存疑", result["review_reasons"])

    def test_shoes_without_knee_use_default_fill(self) -> None:
        detection = person({15: (400, 1300), 16: (600, 1300)})
        result = anchor_crop_box([detection], IMAGE, SQUARE, "shoes")
        # No Seg: shoe width = ankles +/- 1.6x ankle-to-sole height (160px).
        self.assertAlmostEqual(self.shoe_fill(result, 0.24, 0.76), ANCHOR_SHOE_FILL, places=2)

    def test_shirt_wide_subject_grows_up_to_limit_then_crops_sides(self) -> None:
        base = anchor_crop_box([person(STANDING)], IMAGE, PORTRAIT, "shirt")
        result = anchor_crop_box([person(STANDING)], IMAGE, PORTRAIT, "shirt", rows(0.10, 0.52, 0.0, 1.0))
        base_height = base["crop_box"][3] - base["crop_box"][1]
        height = result["crop_box"][3] - result["crop_box"][1]
        self.assertAlmostEqual(height / base_height, ANCHOR_MAX_GROWTH, places=2)
        self.assertAlmostEqual(result["crop_box"][1], base["crop_box"][1], places=3)  # head stays anchored
        self.assertIn("主体过宽，左右已裁切", result["review_reasons"])

    def test_shirt_small_side_crop_beyond_limit_has_no_hint(self) -> None:
        # Sleeves 750px wide vs a 686px capped window: ~9% cut (< 15%).
        result = anchor_crop_box([person(STANDING)], IMAGE, PORTRAIT, "shirt", rows(0.10, 0.52, 0.125, 0.875))
        self.assertLess((result["crop_box"][2] - result["crop_box"][0]) * IMAGE[0], 750.0)
        self.assertEqual(result["review_reasons"], [])

    def test_shirt_slightly_wide_subject_grows_without_hint(self) -> None:
        result = anchor_crop_box([person(STANDING)], IMAGE, PORTRAIT, "shirt", rows(0.10, 0.52, 0.20, 0.80))
        self.assertEqual(result["review_reasons"], [])

    def test_shirt_spans_head_top_to_thigh_root_with_top_margin(self) -> None:
        seg = rows(0.05, 0.50, 0.40, 0.60)  # hair above the pose box top
        result = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "shirt", seg)
        left, top, right, bottom = self.window(result)
        self.assertAlmostEqual(result["anchors"]["top"], 0.05, places=3)
        self.assertAlmostEqual(bottom, 820.0, delta=1.0)  # hip + 40% of hip->knee
        span = 820.0 - 75.0
        self.assertAlmostEqual(top, 75.0 - ANCHOR_EDGE_MARGIN * span / (1 - ANCHOR_EDGE_MARGIN), delta=1.0)

    def test_shirt_without_knees_estimates_thigh_from_torso(self) -> None:
        no_knees = {index: point for index, point in STANDING.items() if index < 13}
        result = anchor_crop_box([person(no_knees)], IMAGE, SQUARE, "shirt")
        # torso 350 -> thigh 280 -> 40% = 112px below the hip.
        self.assertAlmostEqual(result["anchors"]["bottom"], 812.0 / IMAGE[1], places=3)

    def test_shirt_without_hips_keeps_source_bottom(self) -> None:
        upper = {index: point for index, point in STANDING.items() if index < 11}
        result = anchor_crop_box([person(upper)], IMAGE, PORTRAIT, "shirt")
        self.assertEqual(result["anchors"]["bottom"], 1.0)
        self.assertNotIn("原图比例不足，可能裁切", result["review_reasons"])

    def test_shirt_group_uses_highest_head_and_lowest_thigh_root(self) -> None:
        second = {index: (x + 50, y + 60) for index, (x, y) in STANDING.items()}
        detections = [
            person(STANDING, bbox=(300.0, 100.0, 700.0, 1400.0)),
            person(second, bbox=(350.0, 160.0, 750.0, 1460.0)),
        ]
        result = anchor_crop_box(detections, IMAGE, SQUARE, "shirt")
        self.assertAlmostEqual(result["anchors"]["top"], 100.0 / IMAGE[1], places=3)
        self.assertAlmostEqual(result["anchors"]["bottom"], 880.0 / IMAGE[1], places=3)

    def test_shirt_group_centres_on_both_people_when_seg_outlines_one(self) -> None:
        # Seg covers only the right person (x 0.55-0.80); the left one spans x 100-450.
        left_person = {index: (x - 300, y) for index, (x, y) in STANDING.items()}
        right_person = {index: (x + 150, y) for index, (x, y) in STANDING.items()}
        detections = [
            person(left_person, bbox=(100.0, 100.0, 450.0, 1400.0)),
            person(right_person, bbox=(550.0, 100.0, 800.0, 1400.0)),
        ]
        result = anchor_crop_box(detections, IMAGE, SQUARE, "shirt", rows(0.10, 0.52, 0.55, 0.80))
        left, _top, right, _bottom = self.window(result)
        self.assertAlmostEqual((left + right) / 2, (100.0 + 800.0) / 2, delta=1.0)

    def test_unsupported_cases_keep_previous_composition(self) -> None:
        self.assertIsNone(anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "hat"))
        no_ankles = {index: point for index, point in STANDING.items() if index not in (15, 16)}
        self.assertIsNone(anchor_crop_box([person(no_ankles)], IMAGE, SQUARE, "shoes"))

    def test_detector_boxes_extend_the_shoe_window(self) -> None:
        # Raised foot at the right, missed by pose: boxes are source px (l, t, r, b).
        seg = rows(0.85, 0.9333, 0.38, 0.62)
        base = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "shoes", seg)
        raised = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "shoes", seg, [(600.0, 1100.0, 800.0, 1400.0)])
        self.assertLess(self.window(base)[2], 800.0)
        self.assertGreaterEqual(self.window(raised)[2], 800.0 - 1)
        self.assertEqual(anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "shoes", seg, []), base)

    def test_group_shoes_knee_down_when_narrow(self) -> None:
        # A 2:1 window leaves room to hide the knees behind narrow shoes.
        pair = [person(STANDING), person({**STANDING, 15: (480, 1300), 16: (520, 1300)})]
        result = anchor_crop_box(pair, IMAGE, (2000, 1000), "shoes")
        self.assertGreaterEqual(self.window(result)[1], 1000.0 - 1)
        self.assertLess(result["anchors"]["top"], 0.9)
        self.assertGreater(result["anchors"]["top"], 0.7)

    def test_group_shoes_full_body_keeps_the_head(self) -> None:
        left = {index: (x - 150, y) for index, (x, y) in STANDING.items()}
        right = {index: (x + 150, y) for index, (x, y) in STANDING.items()}
        pair = [person(left, bbox=(150.0, 100.0, 550.0, 1400.0)), person(right, bbox=(450.0, 100.0, 850.0, 1400.0))]
        # Narrow 1:2 window: the full bodies fit the source width, no fill.
        result = anchor_crop_box(pair, IMAGE, (1000, 2000), "shoes")
        _left, top, _right, bottom = self.window(result)
        self.assertLessEqual(top, 100.0)
        self.assertGreaterEqual(bottom, 1400.0 - 1)
        self.assertNotIn("side_fill", result)
        # Square: the full bodies are wider than the source and the sides are filled.
        wide = anchor_crop_box(pair, IMAGE, SQUARE, "shoes")
        self.assertLess(wide["crop_box"][0], 0.0)
        self.assertGreater(wide["crop_box"][2], 1.0)
        self.assertLessEqual(self.window(wide)[1], 100.0)
        self.assertIn("已自动补边", wide["review_reasons"])

    def test_group_full_body_without_fill_is_skipped(self) -> None:
        # Bodies touch the source sides, so the wide full-body window cannot be filled.
        left = {index: (x - 450, y) for index, (x, y) in STANDING.items()}
        right = {index: (x + 450, y) for index, (x, y) in STANDING.items()}
        pair = [person(left, bbox=(5.0, 100.0, 400.0, 1400.0)), person(right, bbox=(600.0, 100.0, 995.0, 1400.0))]
        result = anchor_crop_box(pair, IMAGE, SQUARE, "shoes")
        self.assertEqual(result, {"skip_reason": "双人全身装不下，且不适合补边"})
        by_size = {"s": {"composition_mode": "zoom_focus", "confidence": 0.5}}
        LocalModelAssistant._apply_anchor_crops(by_size, pair, IMAGE, {"s": (SQUARE, (255, 255, 255))}, "shoes", None)
        self.assertEqual(by_size["s"]["composition_mode"], "skipped")
        self.assertEqual(by_size["s"]["evidence"]["skip_reason"], "双人全身装不下，且不适合补边")

    def test_window_always_stays_inside_source(self) -> None:
        for category in ("shoes", "shirt"):
            for size in (SQUARE, PORTRAIT, (1125, 1500)):
                box = anchor_crop_box([person(STANDING)], IMAGE, size, category)["crop_box"]
                self.assertTrue(-0.5 <= box[0] < box[2] <= 1.5 and 0.0 <= box[1] < box[3] <= 1.0)
                width = (box[2] - box[0]) * IMAGE[0]
                height = (box[3] - box[1]) * IMAGE[1]
                self.assertAlmostEqual(width / height, size[0] / size[1], places=2)


class PantsAnchorTests(unittest.TestCase):
    """Waist to sole (long pants) or below the knee (shorts); STANDING: hips 700, knees 1000, ankles 1300, sole 1400."""

    def window(self, result):
        left, top, right, bottom = result["crop_box"]
        return left * IMAGE[0], top * IMAGE[1], right * IMAGE[0], bottom * IMAGE[1]

    def legs(self, spread: float = 0.0, **extra):
        points = {**STANDING, 13: (460 - spread, 1000), 14: (540 + spread, 1000), 15: (470 - spread, 1300), 16: (530 + spread, 1300)}
        return person(points, **extra)

    def test_waist_above_hips_with_top_margin(self) -> None:
        result = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "long_pants")
        waist = 700.0 - ANCHOR_WAIST_ABOVE_HIP * 300.0
        self.assertAlmostEqual(result["anchors"]["top"], waist / IMAGE[1], places=3)
        span = 1400.0 - waist
        self.assertAlmostEqual(self.window(result)[1], waist - ANCHOR_EDGE_MARGIN * span / (1 - 2 * ANCHOR_EDGE_MARGIN), delta=1.0)

    def test_source_starting_at_the_waist_has_no_top_margin(self) -> None:
        high = {index: (x, y - 600) for index, (x, y) in STANDING.items() if index >= 11}
        result = anchor_crop_box([person(high, bbox=(300.0, 0.0, 700.0, 800.0))], IMAGE, SQUARE, "long_pants")
        self.assertEqual(result["crop_box"][1], 0.0)

    def test_long_pants_end_at_the_sole(self) -> None:
        result = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "long_pants", rows(0.1, 0.9333, 0.4, 0.6))
        self.assertEqual(result["bottom_rule"], "sole")
        self.assertAlmostEqual(result["anchors"]["bottom"], 1400.0 / IMAGE[1], places=3)
        self.assertGreater(self.window(result)[3], 1400.0)  # bottom margin

    def test_pants_matches_long_pants_and_cut_feet_use_the_source_bottom(self) -> None:
        for seg in (None, rows(0.1, 0.9333, 0.4, 0.6)):
            self.assertEqual(
                anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "pants", seg),
                anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "long_pants", seg),
            )
        cut = {index: point for index, point in STANDING.items() if index < 15}
        result = anchor_crop_box([person(cut, bbox=(300.0, 100.0, 700.0, 1100.0))], IMAGE, SQUARE, "long_pants")
        self.assertEqual((result["bottom_rule"], result["anchors"]["bottom"]), ("source", 1.0))

    def test_shorts_end_just_below_the_knee(self) -> None:
        result = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "shorts")
        self.assertEqual(result["bottom_rule"], "knee")
        self.assertAlmostEqual(result["anchors"]["bottom"], (1000.0 + ANCHOR_SHORTS_BELOW_KNEE * 300.0) / IMAGE[1], places=3)

    def test_shorts_extend_to_the_calf_then_the_sole_never_between(self) -> None:
        # Mid-calf is y=1150: a moderate stance needs a hem a little below the knee (calf), a wide one reaches past it.
        calf = anchor_crop_box([self.legs(160)], IMAGE, SQUARE, "shorts")
        self.assertEqual(calf["bottom_rule"], "calf")
        self.assertTrue(1045.0 < calf["anchors"]["bottom"] * IMAGE[1] <= 1150.0)
        sole = anchor_crop_box([self.legs(260)], IMAGE, SQUARE, "shorts")
        self.assertEqual(sole["bottom_rule"], "sole")
        self.assertAlmostEqual(sole["anchors"]["bottom"], 1400.0 / IMAGE[1], places=3)
        for spread in range(0, 400, 20):
            bottom = anchor_crop_box([self.legs(spread)], IMAGE, SQUARE, "shorts")["anchors"]["bottom"] * IMAGE[1]
            self.assertFalse(1150.0 < bottom < 1400.0 - 1.0, (spread, bottom))

    def test_close_ups_without_a_plausible_hip_to_knee_span_are_not_anchored(self) -> None:
        no_knees = {index: point for index, point in STANDING.items() if index not in (13, 14)}
        long_thigh = {**STANDING, 13: (460, 1450), 14: (540, 1450)}  # thigh 750px = 50% of the image: still plausible
        for category in ("long_pants", "pants", "shorts"):
            self.assertIsNone(anchor_crop_box([person(no_knees)], IMAGE, SQUARE, category))
            self.assertIsNone(anchor_crop_box([person({**STANDING, 13: (460, 1600), 14: (540, 1600)})], IMAGE, SQUARE, category))
            self.assertIsNotNone(anchor_crop_box([person(long_thigh)], IMAGE, SQUARE, category))

    def test_raised_knee_keeps_the_anchor(self) -> None:
        raised = {**STANDING, 13: (460, 600)}  # one knee above the hip
        self.assertEqual(anchor_crop_box([person(raised)], IMAGE, SQUARE, "long_pants")["bottom_rule"], "sole")

    def test_width_deficit_with_clean_strips_is_filled(self) -> None:
        # A 2:1 window (waist to sole) is wider than the 2:3 source.
        wide = anchor_crop_box([self.legs()], IMAGE, (2000, 1000), "long_pants", rows(0.1, 0.9333, 0.4, 0.6))
        self.assertLess(wide["crop_box"][0], 0.0)
        self.assertIn("已自动补边", wide["review_reasons"])
        self.assertAlmostEqual(wide["side_fill"]["strip_left"], 0.4 - 0.003, places=3)

    def test_width_deficit_without_a_gap_is_skipped_and_applied_as_skipped_plan(self) -> None:
        touching = rows(0.1, 0.9333, 0.005, 0.6)
        result = anchor_crop_box([self.legs()], IMAGE, (2000, 1000), "long_pants", touching)
        self.assertEqual(result, {"skip_reason": "原图比例不足，且不适合补边"})
        by_size = {"s": {"composition_mode": "zoom_focus", "confidence": 0.5}}
        LocalModelAssistant._apply_anchor_crops(
            by_size, [self.legs()], IMAGE, {"s": ((2000, 1000), (255, 255, 255))}, "long_pants", touching,
        )
        self.assertEqual(by_size["s"]["composition_mode"], "skipped")
        self.assertEqual(by_size["s"]["evidence"]["skip_reason"], "原图比例不足，且不适合补边")

    def test_no_hips_keeps_the_previous_composition(self) -> None:
        upper = {index: point for index, point in STANDING.items() if index < 11}
        for category in ("long_pants", "pants", "shorts"):
            self.assertIsNone(anchor_crop_box([person(upper)], IMAGE, SQUARE, category))

    def test_raised_foot_starts_at_the_shoulders(self) -> None:
        planted = (450.0, 1300.0, 560.0, 1400.0)
        raised = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "long_pants", None, [planted, (200.0, 700.0, 400.0, 900.0)])
        self.assertEqual(raised["top_rule"], "shoulder")
        self.assertAlmostEqual(raised["anchors"]["top"], 350.0 / IMAGE[1], places=3)
        self.assertGreater(raised["crop_box"][1], 0.0)  # shoulder margin
        lifted = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "long_pants", None, [planted, (600.0, 1250.0, 700.0, 1350.0)])
        self.assertEqual(lifted["top_rule"], "waist")
        shorts = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "shorts", None, [planted, (200.0, 700.0, 400.0, 900.0)])
        self.assertEqual(shorts["top_rule"], "shoulder")

    def test_raised_foot_without_shoulders_starts_at_the_source_top(self) -> None:
        no_shoulders = {index: point for index, point in STANDING.items() if index not in (5, 6)}
        result = anchor_crop_box([person(no_shoulders)], IMAGE, SQUARE, "long_pants", None, [(450.0, 1300.0, 560.0, 1400.0), (200.0, 700.0, 400.0, 900.0)])
        self.assertEqual((result["top_rule"], result["anchors"]["top"], result["crop_box"][1]), ("source", 0.0, 0.0))

    def test_detector_shoe_box_extends_the_sole_and_width(self) -> None:
        base = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "long_pants")
        boxed = anchor_crop_box([person(STANDING)], IMAGE, SQUARE, "long_pants", None, [(450.0, 1350.0, 800.0, 1460.0)])
        self.assertAlmostEqual(boxed["anchors"]["bottom"], 1460.0 / IMAGE[1], places=3)
        self.assertGreaterEqual(self.window(boxed)[2], 800.0)
        self.assertLess(base["anchors"]["bottom"], boxed["anchors"]["bottom"])


class SideFillTests(unittest.TestCase):
    """2:3 source, square shirt window wider than the source."""

    def box(self, left: float, right: float, category: str = "shirt"):
        # Hips at y=1000: the square window (~1150) is wider than the 1000px source.
        low = {index: (x, y + 300) for index, (x, y) in STANDING.items() if index != 15 and index != 16}
        low.update({15: (470, 1450), 16: (530, 1450)})
        detection = person(low, bbox=(300.0, 20.0, 700.0, 1480.0))
        return anchor_crop_box([detection], IMAGE, SQUARE, category, rows(0.02, 0.8, left, right))

    def test_background_gaps_are_filled(self) -> None:
        result = self.box(0.3, 0.7)
        left, _top, right, _bottom = result["crop_box"]
        self.assertLess(left, 0.0)
        self.assertGreater(right, 1.0)
        self.assertIn("已自动补边", result["review_reasons"])
        self.assertNotIn("原图比例不足，可能裁切", result["review_reasons"])
        self.assertAlmostEqual(result["side_fill"]["strip_left"], 0.3 - 0.003, places=3)

    def test_subject_touching_source_edge_keeps_old_behaviour(self) -> None:
        result = self.box(0.003, 0.7)
        left, _top, right, _bottom = result["crop_box"]
        self.assertTrue(0.0 <= left < right <= 1.0)
        self.assertNotIn("side_fill", result)
        self.assertNotIn("已自动补边", result["review_reasons"])

    def test_shoes_that_fit_after_shrinking_zoom_in_instead_of_filling(self) -> None:
        result = self.box(0.3, 0.7, "shoes")
        left, _top, right, _bottom = result["crop_box"]
        self.assertTrue(0.0 <= left < right <= 1.0)
        self.assertNotIn("side_fill", result)

    def test_shoes_wider_than_the_source_are_filled(self) -> None:
        # Seg outline of the shoes spans 96% of the width: no window can keep them with margins.
        detection = person({**STANDING, 15: (200, 1300), 16: (800, 1300)})
        result = anchor_crop_box([detection], IMAGE, SQUARE, "shoes", rows(0.85, 0.9333, 0.02, 0.98))
        self.assertLess(result["crop_box"][0], 0.0)
        self.assertIn("已自动补边", result["review_reasons"])

    def test_structure_gate_skips_busy_strips(self) -> None:
        import tempfile
        from pathlib import Path

        from PIL import ImageDraw

        box = [-0.2, 0.05, 1.2, 0.8]
        fill = {"strip_left": 0.25, "strip_right": 0.25}
        with tempfile.TemporaryDirectory() as temp_dir:
            plain = Image.linear_gradient("L").resize(IMAGE).point(lambda v: 200 + v // 16).convert("RGB")
            busy = plain.copy()
            draw = ImageDraw.Draw(busy)
            for x in range(10, 200, 40):
                draw.line((x, 0, x + 30, 1500), fill=(20, 20, 20), width=6)
            draw.rectangle((780, 400, 900, 900), fill=(30, 60, 90))
            for name, image in (("plain", plain), ("busy", busy)):
                path = Path(temp_dir) / f"{name}.jpg"
                image.save(path, quality=95)
                by_size: dict[str, dict[str, object]] = {"s": {"confidence": 0.5}}
                with unittest.mock.patch(
                    "pixelflow_core.model_assistant.anchor_crop_box",
                    return_value={"crop_box": box, "anchors": {"top": 0.1, "bottom": 0.7}, "review_reasons": ["已自动补边"], "side_fill": fill},
                ):
                    LocalModelAssistant._apply_anchor_crops(
                        by_size, [person(STANDING)], IMAGE, {"s": (SQUARE, (255, 255, 255))}, "shirt", None, path,
                    )
                if name == "plain":
                    self.assertEqual(by_size["s"]["composition_mode"], "anchor_crop")
                    self.assertEqual(by_size["s"]["evidence"]["side_fill"], fill)
                else:
                    self.assertEqual(by_size["s"]["composition_mode"], "skipped")
                    self.assertEqual(by_size["s"]["evidence"]["skip_reason"], "背景不适合补边")

    def test_skipped_plan_survives_typed_conversion(self) -> None:
        from pathlib import Path

        from pixelflow_core.classification import _typed_plan_from_mapping

        plan = _typed_plan_from_mapping(
            Path("a.jpg"), "1440x1440",
            {"composition_mode": "skipped", "evidence": {"skip_reason": "背景不适合补边"}},
        )
        self.assertEqual(plan.composition_mode, "skipped")
        self.assertEqual(plan.evidence["skip_reason"], "背景不适合补边")

    def test_renderer_side_fill_keeps_geometry(self) -> None:
        import numpy as np

        # Red channel encodes the source column (x * 2) so placement and stretch are measurable.
        columns = np.arange(100, dtype=np.float32)
        pixels = np.zeros((150, 100, 3), np.float32)
        pixels[..., 0] = columns[None, :] * 2
        pixels[..., 1:] = 120
        image = Image.fromarray(pixels.astype(np.uint8))
        for box, first, last in (
            ((-0.25, 0.0, 1.25, 1.0), 25, 125),  # both sides
            ((-0.5, 0.0, 1.0, 1.0), 50, 150),  # left only
            ((0.0, 0.0, 1.5, 1.0), 0, 100),  # right only
        ):
            plan = CompositionPlan(
                "a.jpg", "1440x1440", composition_mode="anchor_crop", crop_box=box,
                evidence={"side_fill": {"strip_left": 0.1, "strip_right": 0.1}},
            )
            request = RenderRequest("a.jpg", "out", "1440x1440", plan=plan, source_type="model", include_logo=False)
            output = np.asarray(Renderer()._compose(image, request, (150, 150), False), dtype=np.float32)
            red = output[75, :, 0]
            # Source column 0 / 99 land at the expected output columns, one source px = one output px.
            self.assertAlmostEqual(red[first + 1], 2.0, delta=3.0)
            self.assertAlmostEqual(red[last - 2], 2.0 * 98, delta=3.0)
            self.assertAlmostEqual(red[first + 50], 100.0, delta=3.0)

    def test_renderer_fills_sides_without_seams(self) -> None:
        import numpy as np

        rng = np.random.default_rng(1)
        ramp = np.linspace(170, 200, 400, dtype=np.float32)[None, :, None]
        pixels = np.broadcast_to(ramp, (600, 400, 3)) + rng.normal(0, 2, (600, 400, 3))
        image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
        plan = CompositionPlan(
            "a.jpg", "1440x1440", composition_mode="anchor_crop",
            crop_box=(-0.2, 0.0, 1.2, 0.93), evidence={"side_fill": {"strip_left": 0.1, "strip_right": 0.1}},
        )
        request = RenderRequest("a.jpg", "out", "1440x1440", plan=plan, source_type="model", include_logo=False)
        output = Renderer()._compose(image, request, (400, 400), False)
        self.assertEqual(output.size, (400, 400))
        data = np.asarray(output, dtype=np.float32)
        self.assertGreater(data.mean(axis=(0, 2)).min(), 100)  # no black columns
        seams = [int(400 * 0.2 / 1.4), int(400 * 1.2 / 1.4)]
        for seam in seams:
            jump = abs(data[:, seam - 3:seam, :].mean() - data[:, seam:seam + 3, :].mean())
            self.assertLess(jump, 6)
        again = Renderer()._compose(image, request, (400, 400), False)
        self.assertEqual(output.tobytes(), again.tobytes())


class StructureTests(unittest.TestCase):
    def strip(self, draw) -> Image.Image:
        from PIL import ImageDraw

        image = Image.linear_gradient("L").resize((200, 1500)).point(lambda v: 200 + v // 16).convert("RGB")
        draw(ImageDraw.Draw(image))
        return image

    def test_horizontal_lines_pass_vertical_and_diagonal_fail(self) -> None:
        from pixelflow_core.side_fill import STRUCTURE_MAX_SHARE, strip_structure

        tape = self.strip(lambda d: d.rectangle((0, 700, 200, 720), fill=(90, 90, 90)))
        floor = self.strip(lambda d: d.rectangle((0, 900, 200, 1500), fill=(120, 120, 120)))
        vertical = self.strip(lambda d: d.line((100, 0, 100, 1500), fill=(20, 20, 20), width=6))
        diagonal = self.strip(lambda d: d.line((10, 0, 40, 1500), fill=(20, 20, 20), width=6))
        self.assertLessEqual(strip_structure(tape), STRUCTURE_MAX_SHARE)
        self.assertLessEqual(strip_structure(floor), STRUCTURE_MAX_SHARE)
        self.assertGreater(strip_structure(vertical), STRUCTURE_MAX_SHARE)
        self.assertGreater(strip_structure(diagonal), STRUCTURE_MAX_SHARE)


class FillQualityTests(unittest.TestCase):
    def test_wall_floor_step_and_tape_line_stay_on_their_rows(self) -> None:
        import numpy as np

        from pixelflow_core.side_fill import extend_side

        rng = np.random.default_rng(5)
        region = np.full((600, 120, 3), 200.0, np.float32)
        region[400:] = 110.0  # sharp wall/floor step
        region[450:456] = 235.0  # 6px bright tape
        region += rng.normal(0, 1.5, region.shape).astype(np.float32)
        for side in ("left", "right"):
            filled = extend_side(region, 120, 200, side)
            source = region[:, :120] if side == "left" else region
            low, high = source.min(1), source.max(1)
            self.assertLess(np.abs(filled.mean(1) - source.mean(1)).max(), 4.0)
            self.assertLess((low - filled.min(1)).max(), 12.0)
            self.assertLess((filled.max(1) - high).max(), 12.0)


    def test_blob_inside_the_strip_is_not_copied_as_blocks(self) -> None:
        import numpy as np

        from pixelflow_core.side_fill import extend_side

        rng = np.random.default_rng(3)
        region = (220.0 + rng.normal(0, 1.5, (600, 200, 3))).astype(np.float32)
        region[300:308, 100:130] = 250.0  # 30x8 bright blob near the strip's inner edge (right side: inner = left)
        filled = extend_side(region, 200, 300, "right")
        self.assertLess(np.abs(filled - 220.0).max(), 12.0)

    def test_line_covering_only_the_inner_half_does_not_streak(self) -> None:
        import numpy as np

        from pixelflow_core.side_fill import extend_side

        rng = np.random.default_rng(6)
        region = (220.0 + rng.normal(0, 1.5, (600, 200, 3))).astype(np.float32)
        region[300:310, :100] = 120.0  # tape end: inner half of the right strip only
        filled = extend_side(region, 200, 300, "right")
        self.assertLess(np.abs(filled.mean((1, 2)) - 220.0).max(), 6.0)

    def test_partial_feature_strip_fails_the_gate_and_full_line_passes(self) -> None:
        from pixelflow_core.side_fill import PARTIAL_MAX_SHARE, strip_partial

        def strip(draw) -> Image.Image:
            from PIL import ImageDraw

            image = Image.linear_gradient("L").resize((200, 1500)).point(lambda v: 200 + v // 16).convert("RGB")
            draw(ImageDraw.Draw(image))
            return image

        self.assertLessEqual(strip_partial(strip(lambda d: None)), PARTIAL_MAX_SHARE)
        self.assertLessEqual(strip_partial(strip(lambda d: d.rectangle((0, 700, 200, 740), fill=(90, 90, 90)))), PARTIAL_MAX_SHARE)
        self.assertGreater(strip_partial(strip(lambda d: d.rectangle((0, 700, 100, 740), fill=(90, 90, 90)))), PARTIAL_MAX_SHARE)

    def test_seam_has_no_tone_step_and_grain_matches(self) -> None:
        import numpy as np

        from pixelflow_core.side_fill import blur_f, extend_side

        rng = np.random.default_rng(8)
        x = np.arange(240, dtype=np.float32)[None, :, None]
        y = np.arange(600, dtype=np.float32)[:, None, None]
        vignette = 150 + 40 * x / 240 - 25 * ((y - 300) / 300) ** 2  # light falloff plus a vertical vignette
        region = (np.broadcast_to(vignette, (600, 240, 3)) + rng.normal(0, 2.0, (600, 240, 3))).astype(np.float32)
        sigma = lambda a: 1.4826 * np.median(np.abs((a - blur_f(a, 4)) - np.median(a - blur_f(a, 4))))
        for side in ("left", "right"):
            filled = extend_side(region, 200, 240, side)
            near_source, near_fill, inside = (
                (region[:, :8], filled[:, -8:], region[:, 8:16]) if side == "left" else (region[:, -8:], filled[:, :8], region[:, -16:-8])
            )
            block = lambda a: a.mean(1)
            step = np.abs(block(near_source) - block(near_fill)).mean()
            base = np.abs(block(near_source) - block(inside)).mean()
            self.assertLessEqual(step, 1.5 * base)
            strip = region[:, :200] if side == "left" else region[:, -200:]
            self.assertAlmostEqual(sigma(filled) / sigma(strip), 1.0, delta=0.15)

    def test_plain_backdrop_gets_no_row_streaks(self) -> None:
        import numpy as np

        from pixelflow_core.side_fill import extend_side

        rng = np.random.default_rng(4)
        ramp = np.linspace(150, 190, 120, dtype=np.float32)[None, :, None]
        region = np.broadcast_to(ramp, (600, 120, 3)) + rng.normal(0, 3.0, (600, 120, 3)).astype(np.float32)
        source_std = region.mean((1, 2)).std()
        for side in ("left", "right"):
            filled = extend_side(region, 120, 200, side)
            self.assertLessEqual(filled.mean((1, 2)).std(), 1.2 * source_std)

    def test_subject_edge_grazing_the_strip_does_not_smear(self) -> None:
        import numpy as np

        from pixelflow_core.side_fill import extend_side

        rng = np.random.default_rng(2)
        region = (235.0 + rng.normal(0, 1.0, (300, 200, 3))).astype(np.float32)
        # A dark subject sliver on the strip's inner 3 columns for 40 rows.
        region[100:140, 80:83] = 40.0
        region[100:140, 117:120] = 40.0
        left = extend_side(region[:, :83], 83, 150, "left")
        right = extend_side(region[:, 117:], 83, 150, "right")
        for filled in (left, right):
            self.assertLess(np.abs(filled.mean((1, 2)) - 235.0).max(), 4.0)


class DetectionDecodeTests(unittest.TestCase):
    def assistant(self) -> LocalDetectionAssistant:
        import numpy as np

        assistant = LocalDetectionAssistant.__new__(LocalDetectionAssistant)
        assistant._np = np
        assistant.input_width = assistant.input_height = 640
        assistant.classes = ["shoe", "sock"]
        assistant.category_map = {"shoe": "shoes", "sock": "socks"}
        assistant.score_threshold, assistant.nms_iou = 0.25, 0.5
        return assistant

    def test_decode_maps_back_through_letterbox_and_applies_nms(self) -> None:
        import numpy as np

        output = np.zeros((1, 6, 4), np.float32)
        # (cx, cy, w, h) in letterbox px; scores for shoe, sock.
        for column, (box, scores) in enumerate((
            ((320, 320, 100, 100), (0.9, 0.0)),
            ((324, 322, 100, 100), (0.6, 0.0)),  # duplicate of the first
            ((320, 320, 100, 100), (0.1, 0.0)),  # below threshold
            ((500, 500, 40, 40), (0.0, 0.5)),
        )):
            output[0, :4, column] = box
            output[0, 4:, column] = scores
        found = self.assistant()._decode(output, (1000, 1500))
        self.assertEqual([item[0] for item in found], ["shoe", "sock"])
        scale = 640 / 1500
        pad_x = round((640 - round(1000 * scale)) / 2 - 0.1)
        self.assertAlmostEqual(found[0][2][0], (270 - pad_x) / scale, places=1)
        self.assertAlmostEqual(found[0][2][1], 270 / scale, places=1)

    def test_shoe_boxes_keep_shoes_only_and_drop_implausible_areas(self) -> None:
        import tempfile
        from pathlib import Path

        assistant = self.assistant()
        found = [
            ("shoe", 0.9, (100.0, 100.0, 300.0, 200.0)),
            ("sock", 0.8, (100.0, 50.0, 300.0, 100.0)),
            ("shoe", 0.7, (0.0, 0.0, 900.0, 900.0)),  # more than 40% of the image
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "a.jpg"
            Image.new("RGB", (1000, 1000)).save(path)
            with unittest.mock.patch.object(LocalDetectionAssistant, "detect", return_value=found):
                self.assertEqual(assistant.shoe_boxes(path), [[100.0, 100.0, 300.0, 200.0]])


class AnchorPlumbingTests(unittest.TestCase):
    def test_apply_anchor_crops_replaces_supported_plans(self) -> None:
        by_size = {"1440x1440": {"composition_mode": "zoom_focus", "confidence": 0.9}}
        LocalModelAssistant._apply_anchor_crops(
            by_size, [person(STANDING)], IMAGE, {"1440x1440": (SQUARE, (255, 255, 255))}, "shoes", None,
        )
        plan = by_size["1440x1440"]
        self.assertEqual(plan["composition_mode"], "anchor_crop")
        self.assertNotIn("target_crop_height_ratio", plan)
        self.assertEqual(len(plan["crop_box"]), 4)

    def test_mask_rows_map_back_through_letterbox_and_box(self) -> None:
        import numpy as np

        assistant = LocalSegmentationAssistant.__new__(LocalSegmentationAssistant)
        assistant._np = np
        assistant.input_width = assistant.input_height = 640
        mask = np.zeros((160, 160), dtype=bool)
        mask[:, :] = True  # leaks everywhere, including the letterbox padding
        # 1000x1500 source -> scale 0.4267, horizontal padding ~107px.
        rows = assistant._mask_rows(mask, (320.0, 320.0, 200.0, 640.0), (1000, 1500))
        self.assertTrue(rows)
        for top, bottom, left, right in rows:
            self.assertTrue(0.0 <= top < bottom <= 1.0)
            self.assertTrue(0.0 <= left < right <= 1.0)
        self.assertAlmostEqual(min(row[2] for row in rows), (220 - 107) / 640 / (1000 / 1500), places=1)

    def test_composition_plan_validates_crop_box(self) -> None:
        plan = CompositionPlan("a.jpg", "1440x1440", crop_box=[0.1, 0.2, 0.5, 0.6])
        self.assertEqual(plan.crop_box, (0.1, 0.2, 0.5, 0.6))
        with self.assertRaises(ValueError):
            CompositionPlan("a.jpg", "1440x1440", crop_box=(0.5, 0.2, 0.1, 0.6))
        side = CompositionPlan("a.jpg", "1440x1440", crop_box=(-0.1, 0.2, 1.1, 0.6))
        self.assertEqual(side.crop_box, (-0.1, 0.2, 1.1, 0.6))
        with self.assertRaises(ValueError):
            CompositionPlan("a.jpg", "1440x1440", crop_box=(-0.6, 0.2, 1.1, 0.6))
        with self.assertRaises(ValueError):
            CompositionPlan("a.jpg", "1440x1440", crop_box=(0.0, -0.1, 1.0, 0.6))

    def test_renderer_crops_model_window_directly(self) -> None:
        image = Image.new("RGB", (100, 200), (0, 0, 255))
        image.paste((255, 0, 0), (0, 100, 100, 200))  # bottom half red
        plan = CompositionPlan(
            "a.jpg", "1440x1440", composition_mode="anchor_crop", crop_box=(0.0, 0.5, 1.0, 1.0),
        )
        request = RenderRequest("a.jpg", "out", "1440x1440", plan=plan, source_type="model", include_logo=False)
        output = Renderer()._compose(image, request, (144, 144), False)
        self.assertEqual(output.size, (144, 144))
        self.assertEqual(output.getpixel((72, 5)), (255, 0, 0))
        self.assertEqual(output.getpixel((72, 138)), (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
