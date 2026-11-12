from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from pixelflow_core import renderer as renderer_module
from pixelflow_core.contracts import CompositionPlan, ProtectedRegion, RenderLayout, RenderRequest
from pixelflow_core.renderer import (
    RenderConfig,
    Renderer,
    _background_to_alpha,
    _encode_png,
    _light_foreground_touches_edge,
    _orientation_factor,
    _resize_cover,
    _subject_alpha_box,
    _template_config,
    _template_normalization,
)

REPO_RESOURCES = Path(__file__).resolve().parents[1] / "resources"
TEMPLATE_FILE = REPO_RESOURCES / "templates" / "category_templates.json"


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
            self.assertIn("唯品会专享", Path(result.outputs[0]).parts)

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


def _template_root(directory: Path, mutate) -> Path:
    """Copy the bundled templates into a temp resource root and patch them."""
    root = directory / "resources"
    (root / "templates").mkdir(parents=True, exist_ok=True)
    payload = json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))
    mutate(payload)
    (root / "templates" / "category_templates.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def _product_source(
    path: Path,
    box: tuple[int, int, int, int],
    *,
    mode: str = "RGB",
    size: tuple[int, int] = (500, 500),
) -> None:
    """White/transparent canvas with a dark subject, so the bbox probe is exact."""
    fill = (0, 0, 0, 0) if mode == "RGBA" else "white"
    subject = (30, 30, 30, 255) if mode == "RGBA" else (30, 30, 30)
    image = Image.new(mode, size, fill)
    image.paste(subject, box)
    image.save(path)


def _shadowed_product_source(
    path: Path,
    box: tuple[int, int, int, int],
    *,
    shadow: tuple[int, int, int, int],
    alpha: int = 30,
    size: tuple[int, int] = (1000, 1000),
) -> None:
    """Opaque product on top of a much larger, nearly transparent drop shadow."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    image.paste((30, 30, 30, alpha), shadow)
    image.paste((30, 30, 30, 255), box)
    image.save(path)


def _bottom_cut_source(path: Path) -> None:
    """White canvas whose dark block runs off the bottom edge only."""
    image = Image.new("RGB", (800, 600), "white")
    ImageDraw.Draw(image).rectangle((100, 300, 700, 600), fill=(30, 30, 30))
    image.save(path, quality=95)


def _render_detail(source: Path, output_root: Path, adjustment: float):
    return Renderer().render(
        RenderRequest(
            source=str(source),
            output_root=str(output_root),
            size="1440x1440",
            mode="v3",
            output_kind="main",
            include_logo=False,
            source_type="detail",
            metadata={"source_scale_adjustment": adjustment},
            layout=RenderLayout(category="产品主图", output_category="产品主图", filename="detail.jpg"),
        )
    )


def _render_category(
    source: Path,
    output_root: Path,
    resource_root: Path,
    category: str,
    *,
    size: str = "1125x1500",
    transparent: bool = False,
):
    return Renderer(resource_root=resource_root).render(
        RenderRequest(
            source=str(source),
            output_root=str(output_root),
            size=size,
            mode="v3",
            output_kind="transparent" if transparent else "main",
            transparent=transparent,
            include_logo=False,
            layout=RenderLayout(category=category, enable_category_template=True),
        )
    )


def _subject_height(path: Path) -> int:
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGB"))
    rows = np.where((array[:, :, 0] < 128).any(axis=1))[0]
    return int(rows[-1] - rows[0] + 1)


class OrientationNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        _template_config.cache_clear()
        _template_normalization.cache_clear()

    def tearDown(self) -> None:
        _template_config.cache_clear()
        _template_normalization.cache_clear()

    def test_orientation_factor_matches_reference_table(self) -> None:
        cases = {
            (1125, 1500): ((2.09, 1.0), (0.79, 0.6148), (0.58, 0.6812), (3.0, 1.0)),
            (1440, 1920): ((2.09, 1.0), (0.79, 0.6148), (0.58, 0.6812), (3.0, 1.0)),
            (1440, 1440): ((2.09, 1.0), (0.79, 0.7782), (0.58, 0.9083), (3.0, 1.0)),
        }
        for target, rows in cases.items():
            for aspect, expected in rows:
                with self.subTest(target=target, aspect=aspect):
                    factor = _orientation_factor(round(aspect * 1000), 1000, target, 2.09, 1.0)
                    self.assertAlmostEqual(factor, expected, places=4)

    def test_orientation_factor_never_upscales_and_zero_strength_disables(self) -> None:
        for target in ((1125, 1500), (1440, 1920), (1440, 1440)):
            for aspect in (0.05, 0.33, 0.58, 0.79, 1.0, 2.09, 3.0, 9.0):
                with self.subTest(target=target, aspect=aspect):
                    box = (round(aspect * 1000), 1000)
                    factor = _orientation_factor(*box, target, 2.09, 1.0)
                    self.assertLessEqual(factor, 1.0)
                    self.assertGreaterEqual(factor, 0.0)
                    self.assertEqual(_orientation_factor(*box, target, 2.09, 0.0), 1.0)

    def test_reference_aspect_output_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "shoe.png"
            _product_source(source, (40, 100, 458, 300))  # 418x200 -> 2.09
            enabled = _render_category(source, root / "enabled", REPO_RESOURCES, "shoes")
            disabled_root = _template_root(root, lambda payload: payload["shoes"]["normalize"].update(enabled=False))
            disabled = _render_category(source, root / "disabled", disabled_root, "shoes")
            self.assertTrue(enabled.success, enabled.errors)
            self.assertTrue(disabled.success, disabled.errors)
            self.assertEqual(Path(enabled.outputs[0]).read_bytes(), Path(disabled.outputs[0]).read_bytes())

    def test_vertical_subject_is_scaled_down_by_the_expected_factor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "vertical.png"
            _product_source(source, (92, 50, 408, 450))  # 316x400 -> 0.79
            enabled = _render_category(source, root / "enabled", REPO_RESOURCES, "shoes")
            disabled_root = _template_root(root, lambda payload: payload["shoes"]["normalize"].update(enabled=False))
            disabled = _render_category(source, root / "disabled", disabled_root, "shoes")
            self.assertTrue(enabled.success, enabled.errors)
            self.assertTrue(disabled.success, disabled.errors)
            tall = _subject_height(Path(disabled.outputs[0]))
            short = _subject_height(Path(enabled.outputs[0]))
            self.assertAlmostEqual(short / tall, 0.6148, delta=2 / tall)

    def test_soft_shadow_is_not_part_of_the_subject_box(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shadowed = root / "shadowed.png"
            plain = root / "plain.png"
            subject = (100, 60, 500, 540)
            _shadowed_product_source(shadowed, subject, shadow=(40, 20, 940, 600))
            _product_source(plain, subject, mode="RGBA", size=(1000, 1000))
            self.assertEqual(_subject_alpha_box(Image.open(shadowed).convert("RGBA")), subject)

            shadowed_output = _render_category(shadowed, root / "a", REPO_RESOURCES, "shoes")
            plain_output = _render_category(plain, root / "b", REPO_RESOURCES, "shoes")
            self.assertTrue(shadowed_output.success, shadowed_output.errors)
            self.assertTrue(plain_output.success, plain_output.errors)
            self.assertAlmostEqual(
                _subject_height(Path(shadowed_output.outputs[0])),
                _subject_height(Path(plain_output.outputs[0])),
                delta=2 / 1500,
            )

            # Without normalisation the plain bounds stay in force, so the
            # output must match a render that never trimmed the shadow.
            disabled_root = _template_root(root, lambda payload: payload["shoes"]["normalize"].update(enabled=False))
            disabled_output = _render_category(shadowed, root / "c", disabled_root, "shoes")
            self.assertTrue(disabled_output.success, disabled_output.errors)
            with patch.object(renderer_module, "_subject_alpha_box", lambda image: image.getchannel("A").getbbox()):
                baseline = _render_category(shadowed, root / "d", disabled_root, "shoes")
            self.assertEqual(
                Path(disabled_output.outputs[0]).read_bytes(),
                Path(baseline.outputs[0]).read_bytes(),
            )

    def test_rgba_main_output_is_normalized_while_transparent_output_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "product.png"
            # Large enough that the template thumbnail actually shrinks it.
            _product_source(source, (168, 0, 1432, 1600), mode="RGBA", size=(1600, 1600))  # 0.79
            enabled = _render_category(source, root / "enabled", REPO_RESOURCES, "shoes")
            disabled_root = _template_root(root, lambda payload: payload["shoes"]["normalize"].update(enabled=False))
            disabled = _render_category(source, root / "disabled", disabled_root, "shoes")
            self.assertTrue(enabled.success, enabled.errors)
            self.assertTrue(disabled.success, disabled.errors)
            self.assertLess(
                _subject_height(Path(enabled.outputs[0])),
                _subject_height(Path(disabled.outputs[0])) * 0.9,
            )

            transparent_enabled = _render_category(
                source, root / "t-enabled", REPO_RESOURCES, "shoes", size="800x800", transparent=True
            )
            transparent_disabled = _render_category(
                source, root / "t-disabled", disabled_root, "shoes", size="800x800", transparent=True
            )
            self.assertTrue(transparent_enabled.success, transparent_enabled.errors)
            self.assertTrue(transparent_disabled.success, transparent_disabled.errors)
            self.assertEqual(
                Path(transparent_enabled.outputs[0]).read_bytes(),
                Path(transparent_disabled.outputs[0]).read_bytes(),
            )

    def test_disabled_category_output_matches_config_without_normalize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "shirt.png"
            _product_source(source, (92, 50, 408, 450))  # 0.79, would shrink if enabled
            with_normalize = _render_category(source, root / "with", REPO_RESOURCES, "shirt")
            without_root = _template_root(root, lambda payload: payload["shirt"].pop("normalize"))
            without_normalize = _render_category(source, root / "without", without_root, "shirt")
            self.assertTrue(with_normalize.success, with_normalize.errors)
            self.assertTrue(without_normalize.success, without_normalize.errors)
            self.assertEqual(
                Path(with_normalize.outputs[0]).read_bytes(),
                Path(without_normalize.outputs[0]).read_bytes(),
            )

    def test_invalid_normalize_config_falls_back_to_current_output(self) -> None:
        self.assertEqual(_template_normalization(REPO_RESOURCES, "shoes"), (2.09, 1.0))
        self.assertIsNone(_template_normalization(REPO_RESOURCES, "hat"))
        self.assertIsNone(_template_normalization(REPO_RESOURCES, "other"))

        variants = {
            "missing-aspect": lambda payload: payload["shoes"]["normalize"].pop("reference_aspect"),
            "strength-out-of-range": lambda payload: payload["shoes"]["normalize"].update(strength=1.5),
            "negative-aspect": lambda payload: payload["shoes"]["normalize"].update(reference_aspect=-1.0),
            "enabled-string": lambda payload: payload["shoes"]["normalize"].update(enabled="true"),
            "normalize-string": lambda payload: payload["shoes"].__setitem__("normalize", "on"),
            "missing-normalize": lambda payload: payload["shoes"].pop("normalize"),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "shoe.png"
            _product_source(source, (92, 50, 408, 450))  # 0.79
            baseline_root = _template_root(root, lambda payload: payload["shoes"].pop("normalize"))
            baseline = _render_category(source, root / "baseline", baseline_root, "shoes")
            self.assertTrue(baseline.success, baseline.errors)
            expected = Path(baseline.outputs[0]).read_bytes()
            for label, mutate in variants.items():
                with self.subTest(label):
                    _template_normalization.cache_clear()
                    variant_root = _template_root(root / label, mutate)
                    result = _render_category(source, root / f"out-{label}", variant_root, "shoes")
                    self.assertTrue(result.success, result.errors)
                    self.assertEqual(Path(result.outputs[0]).read_bytes(), expected)

    def test_legacy_template_manager_loads_bundled_templates(self) -> None:
        from pixelflow_core.legacy_engine.category_engine.category_template_manager import CategoryTemplateManager

        manager = CategoryTemplateManager(TEMPLATE_FILE)
        self.assertAlmostEqual(manager.get("shoes", "1125x1500").scale, 0.840, places=3)
        self.assertAlmostEqual(manager.get("gloves").scale, 0.620, places=3)


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


class DetailShrinkTests(unittest.TestCase):
    def test_zero_adjustment_matches_cover_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "detail.jpg"
            _bottom_cut_source(source)
            rendered = _render_detail(source, root / "a", 0.0)
            self.assertTrue(rendered.success, rendered.errors)
            with patch.object(
                renderer_module,
                "_pinned_detail_shrink",
                lambda image, target, scale: renderer_module._resize_cover(image, target),
            ):
                legacy = _render_detail(source, root / "b", 0.0)
            self.assertTrue(legacy.success, legacy.errors)
            self.assertEqual(
                Path(rendered.outputs[0]).read_bytes(),
                Path(legacy.outputs[0]).read_bytes(),
            )

    def test_bottom_cut_detail_keeps_its_bottom_edge_and_gains_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "detail.jpg"
            _bottom_cut_source(source)
            rendered = _render_detail(source, root / "a", -20.0)
            self.assertTrue(rendered.success, rendered.errors)
            image = Image.open(rendered.outputs[0]).convert("RGB")
            bottom = image.getpixel((720, 1439))
            top = image.getpixel((720, 0))
            self.assertLess(sum(bottom) / 3, 100)  # product still runs off the bottom
            self.assertGreater(sum(top) / 3, 200)  # the freed band is background

    def test_fully_bleeding_detail_ignores_the_slider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "texture.jpg"
            Image.new("RGB", (800, 600), (30, 30, 30)).save(source)
            base = _render_detail(source, root / "a", 0.0)
            shrunk = _render_detail(source, root / "b", -40.0)
            self.assertTrue(base.success, base.errors)
            self.assertTrue(shrunk.success, shrunk.errors)
            self.assertEqual(Path(base.outputs[0]).read_bytes(), Path(shrunk.outputs[0]).read_bytes())

    def test_positive_adjustment_never_enlarges_a_detail_shot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "detail.jpg"
            _bottom_cut_source(source)
            base = _render_detail(source, root / "a", 0.0)
            larger = _render_detail(source, root / "b", 30.0)
            self.assertEqual(Path(base.outputs[0]).read_bytes(), Path(larger.outputs[0]).read_bytes())


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
