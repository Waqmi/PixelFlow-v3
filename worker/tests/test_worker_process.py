from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class WorkerProcessTests(unittest.TestCase):
    def test_classification_jsonl_lifecycle(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
        manifest = json.loads((root / "fixtures/manifest.json").read_text(encoding="utf-8"))
        sources = [str(source_root / item["relative_path"]) for item in manifest["fixtures"]]
        process = subprocess.Popen(
            [sys.executable, "-m", "pixelflow_core.worker"],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        request = {
            "protocol": 1,
            "request_id": "classify-process",
            "type": "classify_materials",
            "payload": {"product_root": str(source_root), "source_filter": sources},
        }
        shutdown = {"protocol": 1, "request_id": "shutdown", "type": "shutdown", "payload": {}}
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        events: list[dict] = []
        while True:
            event = json.loads(process.stdout.readline())
            events.append(event)
            if event.get("request_id") == "classify-process" and event.get("type") == "done":
                break
        process.stdin.write(json.dumps(shutdown) + "\n")
        process.stdin.flush()
        events.append(json.loads(process.stdout.readline()))
        process.stdin.close()
        process.stdout.close()
        process.wait(timeout=5)

        self.assertEqual(process.returncode, 0)
        self.assertEqual(events[0]["type"], "ready")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(len(events[-2]["payload"]["result"]["decisions"]), 16)
        self.assertIsInstance(events[-2]["payload"]["result"]["decisions"][0], dict)

    def test_generation_jsonl_lifecycle_and_output_count(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
        manifest = json.loads((root / "fixtures/manifest.json").read_text(encoding="utf-8"))
        sources = [str(source_root / item["relative_path"]) for item in manifest["fixtures"]]
        with tempfile.TemporaryDirectory() as output_dir:
            process = subprocess.Popen(
                [sys.executable, "-m", "pixelflow_core.worker"],
                cwd=Path(__file__).resolve().parents[1],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            request = {
                "protocol": 1,
                "request_id": "generation-process",
                "type": "start_generation",
                "payload": {
                    "product_root": str(source_root),
                    "output_root": output_dir,
                    "selected_sizes": ["1440x1440"],
                    "include_logo": False,
                    "include_vip": False,
                    "include_model_images": False,
                    "category_override": "long_pants",
                    "source_filter": sources,
                },
            }
            shutdown = {"protocol": 1, "request_id": "shutdown", "type": "shutdown", "payload": {}}
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            while True:
                event = json.loads(process.stdout.readline())
                if event.get("request_id") == "generation-process" and event.get("type") == "done":
                    result = event["payload"]["result"]
                    break
            process.stdin.write(json.dumps(shutdown) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["type"], "done")
            process.stdin.close()
            process.stdout.close()
            process.wait(timeout=10)

            output_files = [path for path in Path(output_dir).rglob("*") if path.is_file()]
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["sources"], 16)
            self.assertEqual(result["main_images"], 16)
            self.assertEqual(result["transparent_images"], 8)
            self.assertEqual(result["validation"]["passed"], 24)
            self.assertEqual(result["audit"]["checked_outputs"], 24)
            self.assertFalse(result["failures"])
            self.assertEqual(len(output_files), 24)


if __name__ == "__main__":
    unittest.main()
