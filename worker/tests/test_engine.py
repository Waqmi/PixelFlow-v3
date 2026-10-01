from __future__ import annotations

import tempfile
import threading
import time
import unittest
import shutil
import os
import sys
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from pixelflow_core.contracts import CompositionPlan, RenderResult
from pixelflow_core.engine import GenerationRequest, ImageEngine, V26EngineAdapter, V3EngineAdapter
from pixelflow_core.worker import WorkerRuntime, _generation_request

_manifest_dir = tempfile.TemporaryDirectory()
_manifest_patch = patch(
    "pixelflow_core.engine._v3_manifest_path",
    lambda output_root: Path(_manifest_dir.name)
    / f"{abs(hash(str(output_root.resolve(strict=False))))}.json",
)


def setUpModule() -> None:
    # Keep V3 stale-cleanup manifests out of the real user cache.
    _manifest_patch.start()


def tearDownModule() -> None:
    _manifest_patch.stop()
    _manifest_dir.cleanup()


class EngineAdapterTests(unittest.TestCase):
    def test_v3_ignores_external_v2_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"PIXELFLOW_V2_ROOT": str(Path(temp_dir) / "missing-v2")}, clear=False
        ):
            source_root = Path(temp_dir) / "input"
            source_root.mkdir()
            Image.new("RGB", (32, 32), "white").save(source_root / "sample.jpg")
            before = list(sys.path)
            result = V3EngineAdapter().run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1440",),
                    include_logo=False,
                    include_vip=True,
                    include_model_images=True,
                    mode="v3",
                )
            )
            self.assertEqual(result["mode"], "v3")
            self.assertEqual(result["renderer"], "typed-v3")
            self.assertFalse(result["fallback"])
            self.assertEqual(sys.path, before)
            self.assertEqual(result["vip_images"], 1)
            self.assertEqual(result["typed_renderer"]["validated"], 2)

    def test_frozen_worker_spec_has_no_external_v2_checkout(self) -> None:
        spec = Path(__file__).resolve().parents[1] / "pixelflow-worker.spec"
        text = spec.read_text(encoding="utf-8")
        self.assertNotIn("PixelFlow-App-v2.0", text)
        self.assertNotIn('"generate_main_images"', text)
        self.assertIn("pixelflow_core.legacy_engine.generate_main_images", text)

    def test_v3_renderer_uses_owned_resource_directory(self) -> None:
        from pixelflow_core.renderer import Renderer

        resource_root = Renderer().resource_root
        self.assertEqual(resource_root.name, "resources")
        self.assertNotIn("legacy_engine", resource_root.parts)
        self.assertTrue((resource_root / "新logo 1440.png").is_file())

    def test_v3_submits_one_batch_task_per_source(self) -> None:
        class BatchRenderer:
            def __init__(self) -> None:
                self.batches = []

            def render_source_batch(self, requests):
                requests = tuple(requests)
                self.batches.append(requests)
                results = []
                for request in requests:
                    output = Path(request.output_path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"rendered")
                    results.append(
                        RenderResult(
                            source=request.source,
                            outputs=(str(output),),
                            mode="v3",
                            metadata={"size": request.size},
                        )
                    )
                return tuple(results), {"total_seconds": 0.01}

            def validate_existing(self, request):
                return RenderResult(source=request.source, outputs=(request.output_path,), mode="v3")

        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "input"
            source_root.mkdir()
            source = source_root / "sample.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            renderer = BatchRenderer()
            result = V3EngineAdapter(renderer=renderer).run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1440", "1440x1920"),
                    include_logo=True,
                    include_vip=True,
                    include_model_images=True,
                    source_type_overrides={str(source): "main_product"},
                    mode="v3",
                )
            )
        self.assertEqual(len(renderer.batches), 1)
        self.assertEqual(len(renderer.batches[0]), 5)  # 2 sizes × 2 logos + VIP
        self.assertEqual(result["failures"], [])

    def test_v3_progress_counts_actual_output_files(self) -> None:
        class BatchRenderer:
            def render_source_batch(self, requests):
                results = []
                for request in tuple(requests):
                    output = Path(request.output_path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"rendered")
                    results.append(RenderResult(source=request.source, outputs=(str(output),), mode="v3", metadata={"size": request.size}))
                return tuple(results), {}

            def validate_existing(self, request):
                return RenderResult(source=request.source, outputs=(request.output_path,), mode="v3")

        progress = []
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "input"
            source_root.mkdir()
            source = source_root / "sample.png"
            Image.new("RGBA", (32, 32), (255, 0, 0, 255)).save(source)
            result = V3EngineAdapter(renderer=BatchRenderer()).run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1440",),
                    include_logo=True,
                    include_vip=True,
                    include_model_images=False,
                    source_type_overrides={str(source): "main_product"},
                    mode="v3",
                ),
                progress=lambda completed, total, phase: progress.append((completed, total, phase)),
            )
        self.assertEqual(result["validation"]["passed"], 7)  # 2 main + VIP + 4 transparent
        self.assertEqual(progress[0], (0, 7, "准备生成"))
        self.assertEqual(progress[-1][:2], (7, 7))

    def test_v3_vip_uses_square_model_plan(self) -> None:
        class BatchRenderer:
            def __init__(self) -> None:
                self.requests = []

            def render_source_batch(self, requests):
                requests = tuple(requests)
                self.requests.extend(requests)
                results = []
                for request in requests:
                    output = Path(request.output_path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"rendered")
                    results.append(RenderResult(source=request.source, outputs=(str(output),), mode="v3", metadata={"size": request.size}))
                return tuple(results), {}

            def validate_existing(self, request):
                return RenderResult(source=request.source, outputs=(request.output_path,), mode="v3")

        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "input"
            source_root.mkdir()
            source = source_root / "model.jpg"
            Image.new("RGB", (32, 64), "white").save(source)
            renderer = BatchRenderer()
            result = V3EngineAdapter(renderer=renderer).run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1440", "1440x1920"),
                    include_logo=False,
                    include_vip=True,
                    include_model_images=True,
                    source_type_overrides={str(source): "model"},
                    composition_plans=(
                        CompositionPlan(source=str(source), size="1440x1440", crop_ratio=0.6),
                        CompositionPlan(source=str(source), size="1440x1920", crop_ratio=0.8),
                    ),
                    mode="v3",
                )
            )
        self.assertEqual(result["failures"], [])
        vip = next(request for request in renderer.requests if request.output_kind == "vip")
        self.assertIsNotNone(vip.plan)
        self.assertEqual(vip.plan.size, "1440x1440")
        self.assertAlmostEqual(vip.plan.crop_ratio, 0.6)

    def test_v3_skipped_plan_drops_size_and_vip(self) -> None:
        class BatchRenderer:
            def __init__(self) -> None:
                self.requests = []

            def render_source_batch(self, requests):
                requests = tuple(requests)
                self.requests.extend(requests)
                results = []
                for request in requests:
                    output = Path(request.output_path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"rendered")
                    results.append(RenderResult(source=request.source, outputs=(str(output),), mode="v3", metadata={"size": request.size}))
                return tuple(results), {}

            def validate_existing(self, request):
                return RenderResult(source=request.source, outputs=(request.output_path,), mode="v3")

        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "input"
            source_root.mkdir()
            source = source_root / "model.jpg"
            Image.new("RGB", (32, 64), "white").save(source)
            output_root = Path(temp_dir) / "output"
            stale = output_root / "模特图" / "1440x1440" / "含Logo" / "model_jpg.jpg"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"old")
            renderer = BatchRenderer()
            result = V3EngineAdapter(renderer=renderer).run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=output_root,
                    selected_sizes=("1440x1440", "1440x1920"),
                    include_logo=False,
                    include_vip=True,
                    include_model_images=True,
                    source_type_overrides={str(source): "model"},
                    composition_plans=(
                        CompositionPlan(
                            source=str(source), size="1440x1440", composition_mode="skipped",
                            evidence={"skip_reason": "背景不适合补边"},
                        ),
                        CompositionPlan(source=str(source), size="1440x1920", crop_ratio=0.8),
                    ),
                    mode="v3",
                )
            )
        self.assertEqual(result["failures"], [])
        self.assertEqual([request.size for request in renderer.requests], ["1440x1920"])
        self.assertEqual(
            result["skipped_outputs"],
            [{"source": str(source), "sizes": ["1440x1440", "唯品专享1440"], "reason": "背景不适合补边"}],
        )
        self.assertFalse(stale.exists())

    def test_v3_does_not_emit_vip_without_square_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "input"
            source_root.mkdir()
            source = source_root / "model.jpg"
            Image.new("RGB", (32, 64), "white").save(source)
            result = V3EngineAdapter().run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1920",),
                    include_logo=False,
                    include_vip=True,
                    include_model_images=True,
                    source_type_overrides={str(source): "model"},
                    mode="v3",
                )
            )
        self.assertEqual(result["vip_images"], 0)

    def test_v3_adapter_routes_emitted_files_through_typed_renderer(self) -> None:
        calls = []

        class StubRenderer:
            def validate_existing(self, typed_request):
                calls.append(typed_request)
                return RenderResult(
                    source=typed_request.source,
                    outputs=(typed_request.source,),
                    mode="v3",
                )

        def runner(request, progress=None, cancel_event=None):
            output = request.output_root / "产品主图" / "1440x1440" / "无Logo" / "sample.jpg"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"typed-renderer-bridge")
            return {"output_root": str(request.output_root), "main_images": 1}

        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            request = GenerationRequest(
                product_root=product_root,
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                include_logo=False,
                mode="v3",
            )
            result = V3EngineAdapter(runner=runner, renderer=StubRenderer()).run(request)
            self.assertEqual(result["mode"], "v3")
            self.assertEqual(result["typed_renderer"]["validated"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].mode, "v3")
            self.assertEqual(calls[0].output_path, str(Path(temp_dir) / "output" / "产品主图" / "1440x1440" / "无Logo" / "sample.jpg"))
            self.assertEqual(calls[0].source_type, "main_product")
            self.assertFalse(calls[0].transparent)

    def test_v3_source_type_override_wins_over_native_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "运动鞋"
            source_root.mkdir()
            source = source_root / "sample.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            result = V3EngineAdapter().run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1440",),
                    include_logo=False,
                    include_vip=False,
                    include_model_images=True,
                    source_type_overrides={str(source): "model"},
                    mode="v3",
                )
            )
            self.assertEqual(result["model_images"], 1)
            self.assertTrue(any("模特图" in path for path in result["outputs"]))

    def test_v3_reuses_complete_confirmation_overrides_without_reclassification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "input"
            source_root.mkdir()
            source = source_root / "model.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            request = GenerationRequest(
                product_root=source_root,
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                include_logo=False,
                include_vip=False,
                include_model_images=True,
                source_type_overrides={str(source): "model"},
                source_metadata_overrides={str(source): {"category": "shirt"}},
                mode="v3",
            )
            with patch(
                "pixelflow_core.v3_classification.V3ClassifierAdapter.classify",
                side_effect=AssertionError("confirmed sources must not be classified again"),
            ):
                result = V3EngineAdapter().run(request)
            self.assertEqual(result["model_images"], 1)

    def test_v3_skips_excluded_or_disabled_model_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "input"
            source_root.mkdir()
            source = source_root / "model.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            base = dict(
                product_root=source_root,
                selected_sizes=("1440x1440",),
                include_logo=False,
                include_vip=True,
                source_type_overrides={str(source): "model"},
                mode="v3",
            )
            excluded = V3EngineAdapter().run(
                GenerationRequest(
                    output_root=Path(temp_dir) / "excluded",
                    include_model_images=True,
                    excluded_model_sources={str(source)},
                    **base,
                )
            )
            disabled = V3EngineAdapter().run(
                GenerationRequest(
                    output_root=Path(temp_dir) / "disabled",
                    include_model_images=False,
                    **base,
                )
            )
            for result in (excluded, disabled):
                self.assertEqual(result["main_images"], 0)
                self.assertEqual(result["model_images"], 0)
                self.assertEqual(result["vip_images"], 0)
                self.assertEqual(result["failures"], [])

    def test_v3_vip_includes_model_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "运动鞋"
            source_root.mkdir()
            source = source_root / "model.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            output_root = Path(temp_dir) / "output"
            result = V3EngineAdapter().run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=output_root,
                    selected_sizes=("1440x1440",),
                    include_logo=False,
                    include_vip=True,
                    include_model_images=True,
                    source_type_overrides={str(source): "model"},
                    mode="v3",
                )
            )
            self.assertEqual(result["model_images"], 1)
            self.assertEqual(result["vip_images"], 1)
            self.assertTrue((output_root / "模特图" / "1440x1440" / "无Logo" / "素材_01.jpg").is_file())
            self.assertTrue((output_root / "唯品专享1440" / "无Logo" / "素材_01.jpg").is_file())

    def test_v3_model_folders_get_distinct_short_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            first = product_root / "模特图"
            second = product_root / "模特图2"
            first.mkdir(parents=True)
            second.mkdir()
            first_source = first / "one.jpg"
            second_source = second / "two.jpg"
            Image.new("RGB", (32, 32), "white").save(first_source)
            Image.new("RGB", (32, 32), "white").save(second_source)
            output_root = Path(temp_dir) / "output"
            result = V3EngineAdapter().run(
                GenerationRequest(
                    product_root=product_root,
                    output_root=output_root,
                    selected_sizes=("1440x1440",),
                    include_logo=False,
                    include_vip=True,
                    include_model_images=True,
                    source_type_overrides={str(first_source): "model", str(second_source): "model"},
                    mode="v3",
                )
            )
            self.assertEqual(result["model_images"], 2)
            self.assertEqual(result["vip_images"], 2)
            self.assertEqual(len(result["outputs"]), 4)
            self.assertTrue((output_root / "模特图" / "1440x1440" / "无Logo" / "素材_01.jpg").is_file())
            self.assertTrue((output_root / "模特图" / "1440x1440" / "无Logo" / "素材_2_01.jpg").is_file())

    def test_v3_reuses_existing_outputs_without_scope_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            Image.new("RGB", (32, 32), "white").save(product_root / "sample.jpg")
            request = GenerationRequest(
                product_root=product_root,
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                include_logo=False,
                include_vip=False,
                mode="v3",
            )
            adapter = V3EngineAdapter()
            first = adapter.run(request)
            second = adapter.run(request)
            self.assertEqual(first["incremental_reused"], 0)
            self.assertGreater(second["incremental_reused"], 0)
            self.assertTrue(second["incremental_skipped"])
            self.assertEqual(second["skipped_by_scope"], 0)
            self.assertEqual(second["failures"], [])
            forced = replace(request, force_rerender=True)
            third = adapter.run(forced)
            self.assertEqual(third["incremental_reused"], 0)
            self.assertFalse(third["incremental_skipped"])
            self.assertEqual(third["failures"], [])

    def test_v3_same_name_reuses_by_default_and_force_rerender_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            Image.new("RGB", (32, 32), "red").save(product_root / "sample.jpg")
            request = GenerationRequest(
                product_root=product_root,
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                include_logo=False,
                include_vip=False,
                include_model_images=False,
                mode="v3",
            )
            adapter = V3EngineAdapter()
            first = adapter.run(request)
            output = Path(first["outputs"][0])
            Image.new("RGB", (1440, 1440), "blue").save(output)
            second = adapter.run(request)
            with Image.open(output) as reused:
                reused_pixel = reused.getpixel((720, 720))
            self.assertGreater(reused_pixel[2], 200)
            self.assertLess(reused_pixel[0], 20)
            self.assertLess(reused_pixel[1], 20)
            self.assertGreater(second["incremental_reused"], 0)
            forced = adapter.run(replace(request, force_rerender=True))
            with Image.open(output) as rerendered:
                rerendered_pixel = rerendered.getpixel((720, 720))
            self.assertFalse(rerendered_pixel[2] > 200 and rerendered_pixel[0] < 20 and rerendered_pixel[1] < 20)
            self.assertEqual(forced["incremental_reused"], 0)

    def test_v3_removes_only_previously_generated_stale_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            Image.new("RGB", (32, 32), "white").save(product_root / "sample.jpg")
            output_root = Path(temp_dir) / "output"
            request = GenerationRequest(
                product_root=product_root,
                output_root=output_root,
                selected_sizes=("1440x1440", "1440x1920"),
                include_logo=False,
                include_vip=False,
                include_model_images=False,
                mode="v3",
            )
            adapter = V3EngineAdapter()
            first = adapter.run(request)
            self.assertEqual(len(first["outputs"]), 2)
            dropped = [path for path in first["outputs"] if "1440x1920" in path]
            self.assertEqual(len(dropped), 1)
            # A file PixelFlow never generated, placed inside a generated category.
            user_file = output_root / "模特图" / "1440x1440" / "无Logo" / "用户自己的图.jpg"
            user_file.parent.mkdir(parents=True)
            Image.new("RGB", (1440, 1440), "black").save(user_file)

            second = adapter.run(replace(request, selected_sizes=("1440x1440",)))
            self.assertEqual(second["stale_outputs_removed"], 1)
            self.assertFalse(Path(dropped[0]).exists())
            self.assertTrue(user_file.exists())
            self.assertEqual(len(list(output_root.rglob("*.jpg"))), 2)

    def test_v3_retry_does_not_delete_and_keeps_manifest_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            first_source = product_root / "a.jpg"
            second_source = product_root / "b.jpg"
            Image.new("RGB", (32, 32), "white").save(first_source)
            Image.new("RGB", (32, 32), "white").save(second_source)
            output_root = Path(temp_dir) / "output"
            request = GenerationRequest(
                product_root=product_root,
                output_root=output_root,
                selected_sizes=("1440x1440",),
                include_logo=False,
                include_vip=False,
                include_model_images=False,
                mode="v3",
            )
            adapter = V3EngineAdapter()
            first = adapter.run(request)
            self.assertEqual(len(first["outputs"]), 2)

            retry = adapter.run(replace(request, source_filter={str(first_source)}))
            self.assertEqual(retry["stale_outputs_removed"], 0)
            self.assertTrue(all(Path(path).is_file() for path in first["outputs"]))

            # The retry must not shrink the record: removing a source later
            # still cleans up its earlier output.
            second_source.unlink()
            final = adapter.run(request)
            self.assertEqual(final["stale_outputs_removed"], 1)
            self.assertEqual(len(list(output_root.rglob("*.jpg"))), 1)

    def test_v3_reuse_keeps_png_transparent_counts_after_vip_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            Image.new("RGBA", (32, 32), (255, 255, 255, 0)).save(product_root / "sample.png")
            request = GenerationRequest(
                product_root=product_root,
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                include_logo=False,
                include_vip=True,
                include_model_images=False,
                mode="v3",
            )
            adapter = V3EngineAdapter()
            first = adapter.run(request)
            second = adapter.run(request)
            self.assertEqual(first["transparent_images"], 4)
            self.assertEqual(second["transparent_images"], 4)
            self.assertEqual(second["vip_images"], 1)
            self.assertEqual(second["incremental_reused"], 6)
            self.assertTrue(second["incremental_skipped"])
            self.assertEqual(len(second["outputs"]), 6)
            self.assertEqual(second["failures"], [])

    def test_v3_partial_reuse_is_not_reported_as_full_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            Image.new("RGB", (32, 32), "white").save(product_root / "sample.jpg")
            output_root = Path(temp_dir) / "output"
            request = GenerationRequest(
                product_root=product_root,
                output_root=output_root,
                selected_sizes=("1440x1440", "1440x1920"),
                include_logo=False,
                include_vip=False,
                mode="v3",
            )
            adapter = V3EngineAdapter()
            adapter.run(request)
            next(output_root.rglob("*.jpg")).unlink()
            second = adapter.run(request)
            self.assertEqual(second["incremental_reused"], 1)
            self.assertFalse(second["incremental_skipped"])
            self.assertEqual(second["failures"], [])

    def test_request_requires_existing_product_root(self) -> None:
        request = GenerationRequest(
            product_root=Path("/path/that/does/not/exist"),
            output_root=Path("/tmp/output"),
            selected_sizes=("1440x1440",),
        )
        with self.assertRaises(ValueError):
            request.validate()

    def test_request_rejects_file_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output.txt"
            output.write_text("not a directory", encoding="utf-8")
            request = GenerationRequest(
                product_root=Path(temp_dir),
                output_root=output,
                selected_sizes=("1440x1440",),
            )
            with self.assertRaises(ValueError):
                request.validate()

    def test_request_rejects_input_directory_or_child_as_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            for output_root in (product_root, product_root / "generated"):
                request = GenerationRequest(
                    product_root=product_root,
                    output_root=output_root,
                    selected_sizes=("1440x1440",),
                    material_confirmation_completed=True,
                )
                with self.subTest(output_root=output_root):
                    with self.assertRaisesRegex(ValueError, "cannot be the input directory"):
                        request.validate()

    def test_request_requires_material_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = GenerationRequest(
                product_root=Path(temp_dir),
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                material_confirmation_completed=False,
            )
            with self.assertRaises(ValueError):
                request.validate()

    def test_request_rejects_non_positive_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = GenerationRequest(
                product_root=Path(temp_dir),
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                max_size_mb=0,
            )
            with self.assertRaises(ValueError):
                request.validate()

    def test_generation_payload_keeps_v26_optional_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = _generation_request(
                {
                    "product_root": temp_dir,
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                    "logo_overrides": {"logo": "/tmp/logo.png"},
                    "folder_category_overrides": {"黑色": "long_pants"},
                    "include_selling_point_images": True,
                    "selling_points": ["防风", "显瘦"],
                }
            )
            self.assertEqual(request.logo_overrides["logo"], "/tmp/logo.png")
            self.assertEqual(request.folder_category_overrides["黑色"], "long_pants")
            self.assertTrue(request.include_selling_point_images)
            self.assertEqual(request.selling_points, ("防风", "显瘦"))

    def test_generation_payload_rejects_string_selling_points(self) -> None:
        with self.assertRaises(ValueError):
            _generation_request({"selected_sizes": ["1440x1440"], "selling_points": "防风"})

    def test_generation_payload_accepts_explicit_engine_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for mode in ("legacy", "v3", "compare"):
                request = _generation_request(
                    {
                        "product_root": temp_dir,
                        "output_root": str(Path(temp_dir) / "output"),
                        "selected_sizes": ["1440x1440"],
                        "mode": mode,
                    }
                )
                self.assertEqual(request.mode, mode)
            with self.assertRaises(ValueError):
                _generation_request({"product_root": temp_dir, "selected_sizes": ["1440x1440"], "mode": "nope"})

    def test_generation_payload_replays_typed_composition_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            request = _generation_request(
                {
                    "product_root": temp_dir,
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                    "composition_plans": [
                        {
                            "source": str(Path(temp_dir) / "shoe.jpg"),
                            "size": "1440x1440",
                            "scale": 0.8,
                            "requires_review": True,
                            "review_hint": "人工确认",
                        }
                    ],
                }
            )
            self.assertEqual(request.composition_plans[0].scale, 0.8)
            self.assertTrue(request.composition_plans[0].requires_review)

    def test_generation_payload_defaults_to_v3_and_allows_legacy_rollout_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                _generation_request(
                    {
                        "product_root": temp_dir,
                        "output_root": str(Path(temp_dir) / "output"),
                        "selected_sizes": ["1440x1440"],
                    }
                ).mode,
                "v3",
            )
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ", {"PIXELFLOW_ENGINE_MODE": "legacy"}, clear=True
        ):
            self.assertEqual(
                _generation_request(
                    {
                        "product_root": temp_dir,
                        "output_root": str(Path(temp_dir) / "output"),
                        "selected_sizes": ["1440x1440"],
                    }
                ).mode,
                "legacy",
            )

    def test_compare_keeps_legacy_result_and_records_plan_difference(self) -> None:
        class FakeAdapter:
            def __init__(self, plan):
                self.plan = plan

            def run(self, request, progress=None, cancel_event=None):
                return {"output_root": str(request.output_root), "plan": self.plan}

        with tempfile.TemporaryDirectory() as temp_dir:
            request = GenerationRequest(
                product_root=Path(temp_dir),
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                mode="compare",
            )
            engine = ImageEngine(FakeAdapter({"scale": 1}), FakeAdapter({"scale": 2}))
            result = engine.run(request)
            self.assertEqual(result["output_root"], str(Path(temp_dir) / "output"))
            self.assertEqual(result["mode"], "compare")
            self.assertEqual(result["comparison"]["formal_output_mode"], "legacy")
            self.assertEqual(result["comparison"]["differences"], ["scale"])

    def test_plan_dict_becomes_typed_composition_plan(self) -> None:
        source = Path("/tmp/shoe.jpg")
        plan = V3EngineAdapter._plan(
            {
                "composition_mode": "safe_fit",
                "scale": 0.8,
                "protected_regions": [{"kind": "shoe", "bbox": [1, 2, 30, 40]}],
                "risk_flags": ["edge"],
                "requires_review": True,
            },
            source,
            "1440x1440",
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.composition_mode, "safe_fit")
        self.assertEqual(plan.protected_regions[0].kind, "shoe")
        self.assertTrue(plan.requires_review)

    def test_v3_replays_relative_typed_plan_for_matching_source(self) -> None:
        captured = []

        class CaptureRenderer:
            def render(self, typed_request):
                captured.append(typed_request)
                output = Path(typed_request.output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1440, 1440), "white").save(output)
                return RenderResult(
                    source=typed_request.source,
                    outputs=(str(output),),
                    mode="v3",
                )

            def validate_existing(self, typed_request):
                return RenderResult(
                    source=typed_request.source,
                    outputs=(str(typed_request.output_path),),
                    mode="v3",
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "product"
            product_root.mkdir()
            source = product_root / "sample.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            plan = V3EngineAdapter._plan(
                {
                    "source": "sample.jpg",
                    "size": "1440x1440",
                    "composition_mode": "safe_fit",
                    "protected_regions": [{"kind": "product", "bbox": [1, 2, 20, 24]}],
                },
                source,
                "1440x1440",
            )
            assert plan is not None
            V3EngineAdapter(renderer=CaptureRenderer()).run(
                GenerationRequest(
                    product_root=product_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1440",),
                    include_logo=False,
                    include_vip=False,
                    include_model_images=False,
                    composition_plans=(plan,),
                    mode="v3",
                )
            )

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].plan.source, "sample.jpg")

    def test_v3_plan_parser_accepts_model_composition_shape(self) -> None:
        source = Path("/tmp/model.jpg")
        plan = V3EngineAdapter._plan(
            {
                "composition_mode": "zoom_focus",
                "scale_adjustment_percent": 25,
                "position_x": 0.4,
                "position_y": 0.6,
                "target_crop_height_ratio": 0.7,
                "protected_crop_bbox": [10, 20, 90, 160],
            },
            source,
            "1440x1440",
        )
        assert plan is not None
        self.assertAlmostEqual(plan.scale, 1.25)
        self.assertEqual(plan.crop_ratio, 0.7)
        self.assertEqual(plan.protected_regions[0].bbox, (10.0, 20.0, 90.0, 160.0))

    def test_v3_replays_model_plan_from_source_metadata_override(self) -> None:
        captured = []

        class CaptureRenderer:
            def render(self, typed_request):
                captured.append(typed_request)
                output = Path(typed_request.output_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (1440, 1440), "white").save(output)
                return RenderResult(source=typed_request.source, outputs=(str(output),), mode="v3")

            def validate_existing(self, typed_request):
                return RenderResult(source=typed_request.source, outputs=(str(typed_request.output_path),), mode="v3")

        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "product"
            product_root.mkdir()
            source = product_root / "model.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            V3EngineAdapter(renderer=CaptureRenderer()).run(
                GenerationRequest(
                    product_root=product_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1440",),
                    include_logo=False,
                    include_vip=False,
                    include_model_images=True,
                    source_type_overrides={str(source): "model"},
                    source_metadata_overrides={
                        str(source): {
                            "by_size": {
                                "1440x1440": {
                                    "composition_mode": "zoom_focus",
                                    "scale_adjustment_percent": 50,
                                    "target_crop_height_ratio": 0.7,
                                    "protected_crop_bbox": [10, 20, 90, 160],
                                }
                            }
                        }
                    },
                    mode="v3",
                )
            )

        self.assertEqual(len(captured), 1)
        self.assertAlmostEqual(captured[0].plan.scale, 1.5)
        self.assertEqual(captured[0].plan.crop_ratio, 0.7)
        self.assertEqual(captured[0].plan.protected_regions[0].bbox, (10.0, 20.0, 90.0, 160.0))

    def test_v3_forwards_product_scale_adjustment_to_render_request(self) -> None:
        captured = []

        class CaptureRenderer:
            def render_source_batch(self, requests):
                requests = tuple(requests)
                captured.extend(requests)
                results = []
                for request in requests:
                    output = Path(request.output_path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"rendered")
                    results.append(RenderResult(source=request.source, outputs=(str(output),), mode="v3", metadata={"size": request.size}))
                return tuple(results), {}

            def validate_existing(self, request):
                return RenderResult(source=request.source, outputs=(request.output_path,), mode="v3")

        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "product"
            product_root.mkdir()
            source = product_root / "product.jpg"
            Image.new("RGB", (32, 32), "white").save(source)
            V3EngineAdapter(renderer=CaptureRenderer()).run(
                GenerationRequest(
                    product_root=product_root,
                    output_root=Path(temp_dir) / "output",
                    selected_sizes=("1440x1440",),
                    include_logo=False,
                    include_vip=False,
                    source_type_overrides={str(source): "main_product"},
                    source_scale_adjustments={str(source): 50},
                    mode="v3",
                )
            )
        self.assertEqual(len(captured), 1)
        self.assertAlmostEqual(float(captured[0].metadata["source_scale_adjustment"]), 50.0)

    def test_v3_failure_falls_back_to_legacy_output(self) -> None:
        class LegacyAdapter:
            def run(self, request, progress=None, cancel_event=None):
                request.output_root.mkdir(parents=True, exist_ok=True)
                (request.output_root / "legacy-ok.txt").write_text("ok", encoding="utf-8")
                return {"outputs": ["legacy-ok.txt"]}

        class FailingV3:
            def run(self, request, progress=None, cancel_event=None):
                raise RuntimeError("renderer failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            request = GenerationRequest(
                product_root=Path(temp_dir),
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                mode="v3",
            )
            result = ImageEngine(LegacyAdapter(), FailingV3()).run(request)
            self.assertTrue(result["fallback"])
            self.assertEqual(result["fallback_reason"], "renderer failed")
            self.assertTrue((request.output_root / "legacy-ok.txt").is_file())

    def test_v3_reported_file_failure_falls_back_to_legacy_output(self) -> None:
        class LegacyAdapter:
            def run(self, request, progress=None, cancel_event=None):
                request.output_root.mkdir(parents=True, exist_ok=True)
                (request.output_root / "legacy-ok.txt").write_text("ok", encoding="utf-8")
                return {"outputs": ["legacy-ok.txt"]}

        class ReportingV3:
            def run(self, request, progress=None, cancel_event=None):
                return {"failures": [{"source": "sample.jpg", "error": "write failed"}]}

        with tempfile.TemporaryDirectory() as temp_dir:
            request = GenerationRequest(
                product_root=Path(temp_dir),
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                mode="v3",
            )
            result = ImageEngine(LegacyAdapter(), ReportingV3()).run(request)
            self.assertTrue(result["fallback"])
            self.assertIn("write failed", result["fallback_reason"])
            self.assertTrue((request.output_root / "legacy-ok.txt").is_file())

    def test_compare_isolates_v3_writes_from_formal_output(self) -> None:
        class WritingAdapter:
            def __init__(self, marker: str):
                self.marker = marker

            def run(self, request, progress=None, cancel_event=None):
                request.output_root.mkdir(parents=True, exist_ok=True)
                (request.output_root / f"{self.marker}.txt").write_text("ok", encoding="utf-8")
                return {"output_root": str(request.output_root), "plan": self.marker}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "output"
            result = ImageEngine(
                WritingAdapter("legacy"),
                WritingAdapter("v3"),
            ).run(
                GenerationRequest(
                    product_root=Path(temp_dir),
                    output_root=output_root,
                    selected_sizes=("1440x1440",),
                    mode="compare",
                )
            )
            self.assertTrue((output_root / "legacy.txt").is_file())
            self.assertFalse((output_root / "v3.txt").exists())
            self.assertEqual(result["comparison"]["formal_output_mode"], "legacy")

    def test_compare_reports_file_level_hash_and_image_differences(self) -> None:
        class WritingAdapter:
            def __init__(self, color: str):
                self.color = color

            def run(self, request, progress=None, cancel_event=None):
                outputs = (
                    request.output_root / "产品主图" / "1440x1440" / "sample.jpg",
                    request.output_root / "透明产品图" / "1440x1440" / "sample.png",
                    request.output_root / "唯品专享1440" / "无Logo" / "sample.jpg",
                )
                for output in outputs:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    image = Image.new("RGBA" if output.suffix == ".png" else "RGB", (8, 8), self.color)
                    image.save(output)
                return {"output_root": str(request.output_root)}

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "output"
            result = ImageEngine(
                WritingAdapter("red"),
                WritingAdapter("blue"),
            ).run(
                GenerationRequest(
                    product_root=Path(temp_dir),
                    output_root=output_root,
                    selected_sizes=("1440x1440",),
                    mode="compare",
                )
            )
            differences = result["comparison"]["file_differences"]
            self.assertEqual(len(differences), 3)
            item = next(item for item in differences if item["path"] == "产品主图/1440x1440/sample.jpg")
            self.assertEqual(item["path"], "产品主图/1440x1440/sample.jpg")
            self.assertIn("sha256", item["differences"])
            self.assertIn("pixel_sha256", item["differences"])
            self.assertEqual(item["legacy"]["size"], [8, 8])
            self.assertEqual(item["legacy"]["mode"], "RGB")
            self.assertEqual(len(item["legacy"]["sha256"]), 64)
            self.assertEqual(len(item["legacy"]["pixel_sha256"]), 64)
            self.assertEqual(result["comparison"]["output_kind_summary"]["product_main"]["different_files"], 1)
            self.assertEqual(result["comparison"]["output_kind_summary"]["transparent"]["different_files"], 1)
            self.assertEqual(result["comparison"]["output_kind_summary"]["vip"]["different_files"], 1)
            self.assertEqual(len(list(output_root.rglob("*.jpg"))), 2)
            self.assertEqual(len(list(output_root.rglob("*.png"))), 1)

    def test_adapter_reuses_unchanged_output_manifest(self) -> None:
        source_root = Path(__file__).resolve().parents[3] / "05_测试素材与输出/冲锋裤主图"
        source = source_root / "FRPN015-1 男 暗夜黑/5B3A7456.jpg"
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"HOME": temp_dir}):
            product_root = Path(temp_dir) / "product"
            product_root.joinpath("样本").mkdir(parents=True)
            shutil.copy2(source, product_root / "样本" / "sample.jpg")
            request = GenerationRequest(
                product_root=product_root,
                output_root=Path(temp_dir) / "output",
                selected_sizes=("1440x1440",),
                include_logo=False,
                include_vip=False,
                include_model_images=False,
            )
            adapter = V26EngineAdapter()
            first = adapter.run(request)
            second = adapter.run(request)
            self.assertEqual(first["incremental_reused"], 0)
            self.assertTrue(second["incremental_skipped"])
            self.assertEqual(second["failures"], [])

    def test_adapter_skips_excluded_model_source(self) -> None:
        source_root = Path(__file__).resolve().parents[3] / "05_测试素材与输出/冲锋裤主图"
        source = source_root / "FRPN015-1 男 暗夜黑/5B3A7456.jpg"
        with tempfile.TemporaryDirectory() as temp_dir:
            result = V26EngineAdapter().run(
                GenerationRequest(
                    product_root=source_root,
                    output_root=Path(temp_dir),
                    selected_sizes=("1440x1440",),
                    include_logo=False,
                    include_vip=False,
                    source_type_overrides={str(source): "model"},
                    excluded_model_sources={str(source)},
                    source_filter={str(source)},
                )
            )
            self.assertEqual(result["main_images"], 0)
            self.assertEqual(result["model_images"], 0)
            self.assertEqual(result["skipped_model_sources"], 1)
            self.assertEqual(result["failures"], [])

    def test_runtime_clear_cache_only_removes_private_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"HOME": temp_dir}):
            manifest_dir = Path(temp_dir) / "Library/Caches/PixelFlow/manifests"
            model_dir = Path(temp_dir) / "Library/Caches/PixelFlow/model-results"
            manifest_dir.mkdir(parents=True)
            model_dir.mkdir(parents=True)
            (manifest_dir / "manifest.json").write_text("{}", encoding="utf-8")
            (model_dir / "model.json").write_text("{}", encoding="utf-8")
            output_file = Path(temp_dir) / "user-output.jpg"
            output_file.write_bytes(b"keep")

            cleared = WorkerRuntime(adapter=V26EngineAdapter()).clear_cache()

            self.assertEqual(cleared, {"manifest": 1, "model_results": 1})
            self.assertTrue(output_file.exists())

    def test_adapter_imports_v26_engine_without_qt(self) -> None:
        adapter = V26EngineAdapter()
        generate_images, resource_root = adapter._load_engine()
        self.assertTrue(callable(generate_images))
        self.assertTrue(resource_root.is_dir())

    def test_runtime_emits_progress_and_done(self) -> None:
        events: list[tuple[str, dict]] = []

        class FakeAdapter:
            def run(self, request, progress, cancel_event):
                progress(1, 2, "分类")
                progress(2, 2, "生成")
                return {"cancelled": False, "outputs": 2}

        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            runtime = WorkerRuntime(
                adapter=FakeAdapter(),
                emit=lambda _request_id, event_type, payload: events.append((event_type, payload)),
            )
            runtime.start(
                "job-1",
                {
                    "product_root": str(product_root),
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                },
            )
            deadline = time.monotonic() + 2
            while runtime.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(runtime.busy)
            self.assertEqual([event[0] for event in events], ["progress", "progress", "done"])

    def test_runtime_cancel_sets_event_and_emits_cancelled(self) -> None:
        events: list[tuple[str, dict]] = []
        started = threading.Event()

        class FakeAdapter:
            def run(self, request, progress, cancel_event):
                started.set()
                while not cancel_event.is_set():
                    time.sleep(0.005)
                return {"cancelled": True}

        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            runtime = WorkerRuntime(
                adapter=FakeAdapter(),
                emit=lambda _request_id, event_type, payload: events.append((event_type, payload)),
            )
            runtime.start(
                "job-2",
                {
                    "product_root": str(product_root),
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                },
            )
            self.assertTrue(started.wait(1))
            self.assertTrue(runtime.cancel("job-2"))
            deadline = time.monotonic() + 2
            while runtime.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn("cancelled", [event[0] for event in events])

    def test_runtime_reports_engine_errors(self) -> None:
        events: list[tuple[str, dict]] = []

        class FakeAdapter:
            def run(self, request, progress, cancel_event):
                raise RuntimeError("synthetic failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            product_root = Path(temp_dir) / "input"
            product_root.mkdir()
            runtime = WorkerRuntime(
                adapter=FakeAdapter(),
                emit=lambda _request_id, event_type, payload: events.append((event_type, payload)),
            )
            runtime.start(
                "job-3",
                {
                    "product_root": str(product_root),
                    "output_root": str(Path(temp_dir) / "output"),
                    "selected_sizes": ["1440x1440"],
                },
            )
            deadline = time.monotonic() + 2
            while runtime.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(events[-1][0], "error")
            self.assertEqual(events[-1][1]["code"], "generation_failed")

    def test_probe_without_models_does_not_start_inference(self) -> None:
        runtime = WorkerRuntime(adapter=V26EngineAdapter())
        result = runtime.probe_models("/path/that/does/not/exist")
        self.assertEqual(result["pose"]["state"], "未配置")
        self.assertEqual(result["segmentation"]["state"], "未配置")


if __name__ == "__main__":
    unittest.main()
