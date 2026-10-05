from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from pixelflow_core.contracts import CompositionPlan, ProtectedRegion, RenderRequest
from pixelflow_core.renderer import (
    RenderConfig,
    Renderer,
    _background_to_alpha,
    _encode_png,
    _light_foreground_touches_edge,
)


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

    def test_source_batch_reuses_compose_for_logo_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "model.jpg"
            Image.new("RGB", (96, 144), (220, 40, 40)).save(source)
            plan = CompositionPlan(
                source=str(source),
                size="200x200",
                composition_mode="anchor_crop",
                crop_box=(0.0, 0.0, 1.0, 1.0),
            )
            cached_requests = (
                RenderRequest(
                    str(source),
                    str(root / "cached"),
                    "200x200",
                    mode="v3",
                    plan=plan,
                    source_type="model",
                    include_logo=True,
                ),
                RenderRequest(
                    str(source),
                    str(root / "cached"),
                    "200x200",
                    mode="v3",
                    plan=plan,
                    source_type="model",
                    include_logo=False,
                ),
            )
            renderer = Renderer()
            with patch.object(renderer, "_compose", wraps=renderer._compose) as compose:
                cached_results, _timings = renderer.render_source_batch(cached_requests)
            self.assertEqual(compose.call_count, 1)
            self.assertTrue(all(result.success for result in cached_results), [result.errors for result in cached_results])

            uncached_root = root / "uncached"
            uncached_results = tuple(Renderer().render(request) for request in (
                replace(cached_requests[0], output_root=str(uncached_root)),
                replace(cached_requests[1], output_root=str(uncached_root)),
            ))
            self.assertTrue(all(result.success for result in uncached_results), [result.errors for result in uncached_results])
            for cached, uncached in zip(cached_results, uncached_results):
                self.assertEqual(Path(cached.outputs[0]).read_bytes(), Path(uncached.outputs[0]).read_bytes())

    def test_source_batch_compose_cache_key_keeps_scale_variants_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "product.jpg"
            image = Image.new("RGB", (96, 96), "white")
            image.paste((220, 40, 40), (28, 28, 68, 68))
            image.save(source)
            requests = (
                RenderRequest(
                    str(source),
                    str(root / "out"),
                    "200x200",
                    mode="v3",
                    include_logo=False,
                    output_path=str(root / "out" / "first.jpg"),
                    metadata={"source_scale_adjustment": 0},
                ),
                RenderRequest(
                    str(source),
                    str(root / "out"),
                    "200x200",
                    mode="v3",
                    include_logo=False,
                    output_path=str(root / "out" / "second.jpg"),
                    metadata={"source_scale_adjustment": 50},
                ),
            )
            renderer = Renderer()
            with patch.object(renderer, "_compose", wraps=renderer._compose) as compose:
                results, _timings = renderer.render_source_batch(requests)
            self.assertEqual(compose.call_count, 2)
            self.assertTrue(all(result.success for result in results), [result.errors for result in results])

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


def _reference_background_to_alpha(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """Per-pixel implementation the vectorised version must match byte for byte."""
    rgba = image.convert("RGBA")
    pixels = list(rgba.convert("RGB").get_flattened_data())
    alpha = []
    for pixel in pixels:
        distance = max(abs(channel - base) for channel, base in zip(pixel, background))
        alpha.append(0 if distance <= 8 else 255 if distance >= 48 else round((distance - 8) * 255 / 40))
    rgba.putalpha(Image.frombytes("L", rgba.size, bytes(alpha)))
    return rgba


def _reference_encode_png(image: Image.Image, max_size_mb: float | None) -> bytes:
    """Eager level-9 implementation the probed version must match byte for byte."""
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    limit = None if max_size_mb is None else max_size_mb * 1024 * 1024
    candidates = [rgba]
    if limit is not None:
        for colors in (256, 128, 64, 32):
            try:
                candidates.append(rgba.quantize(colors=colors, method=Image.Quantize.FASTOCTREE).convert("RGBA"))
            except (ValueError, OSError):
                break
    best = b""
    for candidate in candidates:
        stream = BytesIO()
        candidate.save(stream, format="PNG", optimize=True, compress_level=9)
        payload = stream.getvalue()
        if not best or len(payload) < len(best):
            best = payload
        if limit is None or len(payload) <= limit:
            return payload
    return best


class EncodingParityTests(unittest.TestCase):
    def test_background_to_alpha_matches_per_pixel_reference(self) -> None:
        background = (200, 120, 0)
        # Distances at both thresholds, just inside them and at .5 rounding points
        # (distance 12 -> 25.5, 20 -> 76.5) plus the extremes.
        distances = [0, 1, 7, 8, 9, 10, 12, 20, 27, 28, 47, 48, 49, 55, 255]
        rng = np.random.default_rng(20261005)
        rows = []
        for distance in distances:
            row = []
            for channel in range(3):
                # Each channel moves up or down by exactly `distance` where possible,
                # so the max-channel distance is the one under test.
                up = background[channel] + distance
                down = background[channel] - distance
                value = up if up <= 255 else down
                if value < 0:
                    value = background[channel]
                row.append(value)
            rows.append(row)
        exact = np.zeros((len(distances), 4, 3), dtype=np.uint8)
        for index, row in enumerate(rows):
            exact[index, :, :] = row
            # Vary which channel carries the max distance.
            exact[index, 1, 1] = background[1]
            exact[index, 2, 0] = background[0]
            exact[index, 3, :] = background
            exact[index, 3, index % 3] = row[index % 3]
        noise = rng.integers(0, 256, size=(37, 53, 3), dtype=np.uint8)
        near = np.clip(
            np.asarray(background, dtype=np.int16) + rng.integers(-60, 61, size=(41, 29, 3)), 0, 255
        ).astype(np.uint8)
        images = [Image.fromarray(exact, "RGB"), Image.fromarray(noise, "RGB"), Image.fromarray(near, "RGB")]
        rgba_noise = rng.integers(0, 256, size=(19, 23, 4), dtype=np.uint8)
        images.append(Image.fromarray(rgba_noise, "RGBA"))
        for image in images:
            with self.subTest(size=image.size, mode=image.mode):
                expected = _reference_background_to_alpha(image, background)
                actual = _background_to_alpha(image, background)
                self.assertEqual(actual.mode, expected.mode)
                self.assertEqual(actual.size, expected.size)
                self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_encode_png_matches_eager_level9_reference(self) -> None:
        rng = np.random.default_rng(1005)
        noise = Image.fromarray(rng.integers(0, 256, size=(160, 200, 4), dtype=np.uint8), "RGBA")
        smooth_rgb = np.zeros((300, 240, 3), dtype=np.uint8)
        smooth_rgb[..., 0] = np.linspace(0, 255, 240, dtype=np.uint8)[None, :]
        smooth_rgb[..., 1] = np.linspace(0, 255, 300, dtype=np.uint8)[:, None]
        smooth = Image.fromarray(smooth_rgb, "RGB")
        # Smooth gradient with mild noise: raw misses a tight limit but quantized fits.
        textured = np.clip(
            np.dstack([smooth_rgb, np.full((300, 240), 255, dtype=np.uint8)]).astype(np.int16)
            + rng.integers(-12, 13, size=(300, 240, 4)),
            0,
            255,
        ).astype(np.uint8)
        textured_image = Image.fromarray(textured, "RGBA")

        def l9_size(image: Image.Image, colors: int | None = None) -> int:
            rgba = image if image.mode == "RGBA" else image.convert("RGBA")
            if colors is not None:
                rgba = rgba.quantize(colors=colors, method=Image.Quantize.FASTOCTREE).convert("RGBA")
            stream = BytesIO()
            rgba.save(stream, format="PNG", optimize=True, compress_level=9)
            return len(stream.getvalue())

        mb = 1024 * 1024
        textured_raw = l9_size(textured_image)
        textured_q64 = l9_size(textured_image, 64)
        textured_q32 = l9_size(textured_image, 32)
        self.assertLess(textured_q64, textured_raw)
        cases = [
            ("noise-unlimited", noise, None),
            ("noise-loose", noise, 10.0),
            ("noise-tiny", noise, 1 / mb),
            ("smooth-unlimited", smooth, None),
            ("smooth-loose", smooth, 10.0),
            ("smooth-tiny", smooth, 1 / mb),
            # Raw misses, a quantized candidate fits.
            ("textured-quantized-fits", textured_image, textured_q64 / mb),
            ("textured-raw-just-misses", textured_image, (textured_raw - 1) / mb),
            # Every candidate misses; the last one is probed at level 9, earlier ones are deferred.
            ("textured-all-miss-mixed", textured_image, (textured_q32 - 1) / mb),
            ("textured-tiny", textured_image, 1 / mb),
        ]
        for label, image, limit in cases:
            with self.subTest(label):
                expected = _reference_encode_png(image, limit)
                self.assertEqual(_encode_png(image, limit), expected)
                if limit is not None and label == "textured-quantized-fits":
                    self.assertLessEqual(len(expected), limit * mb)
                    self.assertLess(len(expected), textured_raw)


if __name__ == "__main__":
    unittest.main()
