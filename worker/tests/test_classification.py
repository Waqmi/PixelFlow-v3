from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from pixelflow_core.classification import (
    ClassificationDecision,
    ClassificationRequest,
    V26ClassifierAdapter,
)
from pixelflow_core.worker import WorkerRuntime


class ClassificationAdapterTests(unittest.TestCase):
    def test_fixed_fixture_classifies_without_qt(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
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
        decisions = V26ClassifierAdapter().classify(ClassificationRequest(source_root))

        self.assertEqual(len(decisions), 36)
        self.assertTrue(all("/成品主图/" not in decision.source for decision in decisions))

    def test_invalid_product_root_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            V26ClassifierAdapter().classify(
                ClassificationRequest(Path("/path/that/does/not/exist"))
            )


class ClassificationRuntimeTests(unittest.TestCase):
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

    def test_runtime_reports_classification_errors(self) -> None:
        events: list[tuple[str, dict]] = []

        class FailingClassifier:
            def classify(self, request, progress):
                raise RuntimeError("synthetic classification failure")

        with tempfile.TemporaryDirectory() as temp_dir:
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


if __name__ == "__main__":
    unittest.main()
