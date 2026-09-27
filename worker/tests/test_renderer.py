from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pixelflow_core.contracts import RenderRequest
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


if __name__ == "__main__":
    unittest.main()
