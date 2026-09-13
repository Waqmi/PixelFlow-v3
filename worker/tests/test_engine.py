from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pixelflow_core.engine import GenerationRequest, V26EngineAdapter


class EngineAdapterTests(unittest.TestCase):
    def test_request_requires_existing_product_root(self) -> None:
        request = GenerationRequest(
            product_root=Path("/path/that/does/not/exist"),
            output_root=Path("/tmp/output"),
            selected_sizes=("1440x1440",),
        )
        with self.assertRaises(ValueError):
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

    def test_adapter_imports_v26_engine_without_qt(self) -> None:
        adapter = V26EngineAdapter()
        generate_images, resource_root = adapter._load_engine()
        self.assertTrue(callable(generate_images))
        self.assertTrue(resource_root.is_dir())


if __name__ == "__main__":
    unittest.main()

