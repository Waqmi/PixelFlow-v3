from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pixelflow_core.compare import (
    ACCEPTABLE_MAX_DIFF,
    aggregate_tree_sha256,
    compare_output_trees,
    image_diff_metrics,
)


class CompareTests(unittest.TestCase):
    def test_image_metrics_include_max_mae_psnr_and_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            left = root / "left.png"
            right = root / "right.png"
            Image.new("RGBA", (4, 4), (0, 0, 0, 255)).save(left)
            image = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            image.putpixel((1, 1), (10, 0, 0, 255))
            image.save(right)

            metrics = image_diff_metrics(left, right)
            self.assertIsNotNone(metrics)
            assert metrics is not None
            self.assertEqual(metrics["max_diff"], 10)
            self.assertAlmostEqual(metrics["mae"], 10 / 64)
            self.assertTrue(math.isfinite(metrics["psnr"]))
            self.assertEqual(metrics["diff_bbox"], [1, 1, 2, 2])

    def test_compare_reports_metrics_and_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "legacy"
            v3 = root / "v3"
            left = Image.new("RGBA", (2, 2), (0, 0, 0, 255))
            right = left.copy()
            right.putpixel((0, 0), (ACCEPTABLE_MAX_DIFF, 0, 0, 255))
            for output_root, image in ((legacy, left), (v3, right)):
                path = output_root / "产品主图" / "1440x1440" / "sample.png"
                path.parent.mkdir(parents=True)
                image.save(path)

            report = compare_output_trees(legacy, v3)
            item = report["file_differences"][0]
            self.assertEqual(item["classification"], "acceptable_implementation_difference")
            self.assertIn("max_diff", item)
            self.assertEqual(report["pixel_metrics"]["max_diff"], ACCEPTABLE_MAX_DIFF)

    def test_tree_hash_ignores_python_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "pkg").mkdir()
            (root / "pkg" / "module.py").write_text("one", encoding="utf-8")
            (root / "pkg" / "__pycache__").mkdir()
            (root / "pkg" / "__pycache__" / "module.pyc").write_bytes(b"cache")
            first = aggregate_tree_sha256(root)
            (root / "pkg" / "__pycache__" / "other.pyc").write_bytes(b"ignored")
            self.assertEqual(first, aggregate_tree_sha256(root))


if __name__ == "__main__":
    unittest.main()
