from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pixelflow_core.contracts import CompositionPlan, ProtectedRegion, RenderRequest
from pixelflow_core.renderer import RenderConfig, Renderer, _light_foreground_touches_edge


class RendererTests(unittest.TestCase):
    def test_light_foreground_edge_detection_matches_cover_safety_rule(self) -> None:
        touching = Image.new("RGB", (100, 100), "white")
        touching.paste((30, 30, 30), (0, 25, 55, 75))
        centered = Image.new("RGB", (100, 100), "white")
        centered.paste((30, 30, 30), (20, 25, 75, 75))
        self.assertTrue(_light_foreground_touches_edge(touching, (255, 255, 255)))
        self.assertFalse(_light_foreground_touches_edge(centered, (255, 255, 255)))

    def test_renderer_has_no_legacy_runtime_import(self) -> None:
        source = Path(__file__).resolve().parents[1] / "pixelflow_core" / "renderer.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("_legacy_helpers", text)
        self.assertNotIn("import generate_main_images", text)
        self.assertNotIn("legacy.build_canvas", text)

    def test_typed_main_and_transparent_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (800, 600), (225, 30, 30)).save(source)
            renderer = Renderer()

            main = renderer.render(
                RenderRequest(str(source), str(root / "out"), "1440x1440", include_logo=False)
            )
            self.assertTrue(main.success, main.errors)
            with Image.open(main.outputs[0]) as image:
                self.assertEqual((image.size, image.mode), ((1440, 1440), "RGB"))

            transparent = renderer.render(
                RenderRequest(
                    str(source),
                    str(root / "out"),
                    "800x800",
                    output_kind="transparent",
                    transparent=True,
                )
            )
            self.assertTrue(transparent.success, transparent.errors)
            with Image.open(transparent.outputs[0]) as image:
                self.assertEqual((image.size, image.mode), ((800, 800), "RGBA"))

    def test_compatibility_call_keeps_typed_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGBA", (400, 400), (0, 80, 200, 255)).save(source)
            output = root / "rendered.jpg"
            result = Renderer().render(
                source,
                output,
                RenderConfig(1125, 1500, logo_state="无Logo"),
            )
            self.assertTrue(result.success, result.error)
            with Image.open(output) as image:
                self.assertEqual((image.size, image.mode), ((1125, 1500), "RGB"))

    def test_transparent_jpeg_removes_light_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            image = Image.new("RGB", (200, 160), "white")
            image.paste((220, 30, 30), (70, 50, 130, 110))
            image.save(source)
            result = Renderer().render(
                RenderRequest(
                    str(source),
                    str(root / "out"),
                    "800x800",
                    output_kind="transparent",
                    transparent=True,
                    include_logo=False,
                )
            )
            self.assertTrue(result.success, result.errors)
            with Image.open(result.outputs[0]) as output:
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.getpixel((0, 0))[3], 0)
                self.assertEqual(output.getpixel((400, 400))[3], 255)

    def test_no_logo_main_output_uses_explicit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (20, 20), "red").save(source)
            result = Renderer().render(
                RenderRequest(str(source), str(root / "out"), "1440x1440", include_logo=False)
            )
            self.assertTrue(result.success, result.errors)
            self.assertIn("无Logo", Path(result.outputs[0]).parts)

    def test_source_batch_reuses_analysis_and_reports_stage_timings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (800, 600), "white").save(source)
            renderer = Renderer()
            with patch.object(renderer, "_analyze_source", wraps=Renderer._analyze_source) as analyze:
                results, timings = renderer.render_source_batch(
                    (
                        RenderRequest(str(source), str(root / "out"), "1440x1440", include_logo=False),
                        RenderRequest(str(source), str(root / "out"), "1440x1920", include_logo=False),
                        RenderRequest(
                            str(source),
                            str(root / "out"),
                            "800x800",
                            transparent=True,
                            output_kind="transparent",
                        ),
                    )
                )
            self.assertEqual(analyze.call_count, 1)
            self.assertEqual(len(results), 3)
            self.assertTrue(all(result.success for result in results), [result.errors for result in results])
            for key in ("decode_seconds", "analysis_seconds", "compose_seconds", "overlay_seconds", "encode_seconds", "write_seconds", "total_seconds"):
                self.assertIn(key, timings)

    def test_source_batch_keeps_per_output_write_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (100, 100), "red").save(source)
            requests = (
                RenderRequest(str(source), str(root / "out"), "1440x1440", include_logo=False),
                RenderRequest(str(source), str(root / "out"), "1440x1920", include_logo=False),
            )
            with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
                results, _timings = Renderer().render_source_batch(requests)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(not result.success for result in results))
            self.assertTrue(all("write failed" in result.errors[0] for result in results))

    def test_png_size_limit_is_enforced_when_compression_can_meet_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (40, 40), "white").save(source)
            limit_mb = 0.01
            result = Renderer().render(
                RenderRequest(
                    str(source),
                    str(root / "out"),
                    "800x800",
                    output_kind="transparent",
                    transparent=True,
                    max_size_mb=limit_mb,
                )
            )
            self.assertTrue(result.success, result.errors)
            self.assertLessEqual(Path(result.outputs[0]).stat().st_size, limit_mb * 1024 * 1024)

    def test_validate_existing_does_not_rewrite_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (20, 20), "red").save(source)
            output = root / "out.jpg"
            source.replace(output)
            before = output.read_bytes()
            result = Renderer().validate_existing(
                RenderRequest(
                    str(output),
                    str(root),
                    "20x20",
                    mode="v3",
                    metadata={"output_path": str(output)},
                )
            )
            self.assertTrue(result.success, result.errors)
            self.assertEqual(before, output.read_bytes())

    def test_formal_sizes_and_default_logo_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (400, 300), (230, 230, 230)).save(source)
            renderer = Renderer()
            for size in ("1440x1440", "1440x1920", "1125x1500"):
                result = renderer.render(RenderRequest(str(source), str(root / "out"), size))
                self.assertTrue(result.success, result.errors)
                with Image.open(result.outputs[0]) as image:
                    self.assertEqual(image.size, tuple(int(item) for item in size.split("x")))
                    self.assertEqual(image.mode, "RGB")

    def test_vip_render_uses_bundled_border(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (40, 40), "white").save(source)
            result = Renderer().render(RenderRequest(str(source), str(root / "out"), "1440x1440", output_kind="vip", include_vip=True, include_logo=False))
            self.assertTrue(result.success, result.errors)
            self.assertIn("唯品专享1440", Path(result.outputs[0]).parts)

    def test_model_plan_scales_protected_region_with_pre_scale(self) -> None:
        captured = {}

        def capture(image, target, focus, protected, extra_scale):
            captured.update(
                image_size=image.size,
                focus=focus,
                protected=protected,
                extra_scale=extra_scale,
            )
            return Image.new("RGB", target, "white")

        plan = CompositionPlan(
            source="model.jpg",
            size="100x100",
            composition_mode="zoom_focus",
            scale=1.5,
            position_x=0.4,
            position_y=0.6,
            protected_regions=(
                ProtectedRegion("subject", (10, 20, 90, 160)),
            ),
        )
        request = RenderRequest(
            source="model.jpg",
            output_root="/tmp/output",
            size="100x100",
            mode="v3",
            plan=plan,
            source_type="model",
            include_logo=False,
        )
        with patch("pixelflow_core.renderer._resize_cover", side_effect=capture):
            Renderer()._compose(Image.new("RGB", (100, 200), "white"), request, (100, 100), False)

        self.assertEqual(captured["image_size"], (100, 200))
        self.assertEqual(captured["focus"], (0.4, 0.6))
        self.assertEqual(captured["protected"], (10.0, 20.0, 90.0, 160.0))
        self.assertEqual(captured["extra_scale"], 1.5)

    def test_model_square_crop_ratio_matches_v26_direct_scale(self) -> None:
        image = Image.new("RGB", (100, 200), "red")
        image.paste("blue", (0, 100, 100, 200))
        plan = CompositionPlan(
            source="model.jpg",
            size="100x100",
            composition_mode="zoom_focus",
            crop_ratio=0.5,
            position_y=0.5,
        )
        result = Renderer()._compose(
            image,
            RenderRequest(
                source="model.jpg",
                output_root="/tmp/output",
                size="100x100",
                mode="v3",
                plan=plan,
                source_type="model",
                include_logo=False,
            ),
            (100, 100),
            False,
        )
        self.assertEqual(result.size, (100, 100))
        self.assertEqual(result.getpixel((50, 0)), (255, 0, 0))
        self.assertEqual(result.getpixel((50, 99)), (0, 0, 255))

    def test_model_detail_uses_detail_cover_path(self) -> None:
        with patch("pixelflow_core.renderer._resize_cover", return_value=Image.new("RGB", (100, 100), "green")) as cover:
            result = Renderer()._compose(
                Image.new("RGB", (100, 200), "white"),
                RenderRequest(
                    source="model-detail.jpg",
                    output_root="/tmp/output",
                    size="100x100",
                    mode="v3",
                    source_type="model_detail",
                    include_logo=False,
                ),
                (100, 100),
                False,
            )
        self.assertEqual(result.getpixel((0, 0)), (0, 128, 0))
        self.assertEqual(len(cover.call_args.args), 2)

    def test_model_detail_with_anchor_plan_uses_anchor_crop(self) -> None:
        plan = CompositionPlan(
            source="model-detail.jpg",
            size="100x100",
            composition_mode="anchor_crop",
            crop_box=(0.0, 0.0, 1.0, 0.5),
        )
        image = Image.new("RGB", (100, 200), "blue")
        image.paste((255, 0, 0), (0, 0, 100, 100))
        result = Renderer()._compose(
            image,
            RenderRequest(
                source="model-detail.jpg",
                output_root="/tmp/output",
                size="100x100",
                mode="v3",
                plan=plan,
                source_type="model_detail",
                include_logo=False,
            ),
            (100, 100),
            False,
        )
        self.assertEqual(result.getpixel((50, 99)), (255, 0, 0))

    def test_model_square_crop_ratio_caps_protected_region(self) -> None:
        plan = CompositionPlan(
            source="model.jpg",
            size="100x100",
            composition_mode="zoom_focus",
            crop_ratio=0.5,
            protected_regions=(ProtectedRegion("subject", (10, 20, 90, 180)),),
        )
        result = Renderer()._compose(
            Image.new("RGB", (100, 200), "white"),
            RenderRequest(
                source="model.jpg",
                output_root="/tmp/output",
                size="100x100",
                mode="v3",
                plan=plan,
                source_type="model",
                include_logo=False,
            ),
            (100, 100),
            False,
        )
        self.assertEqual(result.size, (100, 100))

    def test_product_source_scale_adjustment_changes_foreground_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "product.jpg"
            image = Image.new("RGB", (100, 100), "white")
            image.paste((220, 30, 30), (30, 30, 70, 70))
            image.save(source)
            renderer = Renderer()
            widths = []
            for adjustment in (0, 50):
                result = renderer.render(
                    RenderRequest(
                        source=str(source),
                        output_root=str(root / f"out-{adjustment}"),
                        size="200x200",
                        mode="v3",
                        source_type="main_product",
                        include_logo=False,
                        metadata={"source_scale_adjustment": adjustment},
                    )
                )
                self.assertTrue(result.success, result.errors)
                with Image.open(result.outputs[0]) as output:
                    pixels = output.convert("RGB")
                    foreground = [
                        (x, y)
                        for y in range(pixels.height)
                        for x in range(pixels.width)
                        if pixels.getpixel((x, y))[0] > 120
                        and pixels.getpixel((x, y))[0] > pixels.getpixel((x, y))[1] * 1.8
                    ]
                widths.append(max(x for x, _ in foreground) - min(x for x, _ in foreground))
            self.assertGreater(widths[1], widths[0])


if __name__ == "__main__":
    unittest.main()
