from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class WorkerProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source_root = (Path(__file__).resolve().parents[2] / "../05_测试素材与输出/冲锋裤主图").resolve()
        if not cls.source_root.is_dir():
            raise unittest.SkipTest("external 16-image fixture is not present in this checkout")

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
        self.assertEqual(events[-2]["payload"]["result"]["summary"]["count"], 16)

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
                    "mode": "v3",
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
            self.assertEqual(result["mode"], "v3")
            self.assertEqual(result["renderer"], "typed-v3")
            self.assertFalse(result["fallback"])
            self.assertEqual(result["sources"], 16)
            self.assertEqual(result["main_images"], 16)
            self.assertEqual(result["transparent_images"], 8)
            self.assertEqual(result["validation"]["passed"], 24)
            self.assertEqual(result["audit"]["checked_outputs"], 24)
            self.assertFalse(result["failures"])
            self.assertEqual(len(output_files), 24)

    def test_retry_failed_only_reruns_requested_source(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
        manifest = json.loads((root / "fixtures/manifest.json").read_text(encoding="utf-8"))
        source = str(source_root / manifest["fixtures"][0]["relative_path"])
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
                "request_id": "retry-process",
                "type": "retry_failed",
                "payload": {
                    "product_root": str(source_root),
                    "output_root": output_dir,
                    "selected_sizes": ["1440x1440"],
                    "include_logo": False,
                    "include_vip": False,
                    "include_model_images": False,
                    "source_filter": [source],
                },
            }
            shutdown = {"protocol": 1, "request_id": "shutdown", "type": "shutdown", "payload": {}}
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            while True:
                event = json.loads(process.stdout.readline())
                if event.get("request_id") == "retry-process" and event.get("type") == "done":
                    result = event["payload"]["result"]
                    break
            process.stdin.write(json.dumps(shutdown) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["type"], "done")
            process.stdin.close()
            process.stdout.close()
            process.wait(timeout=10)

            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["sources"], 1)
            self.assertTrue(result["retried_only"])
            self.assertFalse(result["failures"])

    def test_generation_process_cancel_keeps_worker_alive(self) -> None:
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
            start = {
                "protocol": 1,
                "request_id": "cancel-process",
                "type": "start_generation",
                "payload": {
                    "product_root": str(source_root),
                    "output_root": output_dir,
                    "selected_sizes": ["1440x1440", "1440x1920", "1125x1500"],
                    "include_logo": False,
                    "include_vip": False,
                    "include_model_images": False,
                    "source_filter": sources,
                },
            }
            cancel = {
                "protocol": 1,
                "request_id": "cancel-process",
                "type": "cancel_generation",
                "payload": {},
            }
            shutdown = {"protocol": 1, "request_id": "shutdown", "type": "shutdown", "payload": {}}
            process.stdin.write(json.dumps(start, ensure_ascii=False) + "\n")
            process.stdin.write(json.dumps(cancel) + "\n")
            process.stdin.flush()
            terminal = None
            while True:
                event = json.loads(process.stdout.readline())
                if event.get("request_id") == "cancel-process" and event.get("type") in {"cancelled", "error"}:
                    terminal = event
                    break
            process.stdin.write(json.dumps(shutdown) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["type"], "done")
            process.stdin.close()
            process.stdout.close()
            process.wait(timeout=10)

            self.assertEqual(process.returncode, 0)
            self.assertEqual(terminal["type"], "cancelled")

    def test_generation_process_routes_legacy_and_compare_modes(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source_root = (root / "../05_测试素材与输出/冲锋裤主图").resolve()
        manifest = json.loads((root / "fixtures/manifest.json").read_text(encoding="utf-8"))
        source = str(source_root / manifest["fixtures"][0]["relative_path"])
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
            for request_id, mode in (
                ("legacy-process", "legacy"),
                ("v3-process", "v3"),
                ("compare-process", "compare"),
            ):
                process.stdin.write(json.dumps({
                    "protocol": 1,
                    "request_id": request_id,
                    "type": "start_generation",
                    "payload": {
                        "product_root": str(source_root),
                        "output_root": output_dir,
                        "selected_sizes": ["1440x1440"],
                        "include_logo": False,
                        "include_vip": False,
                        "include_model_images": False,
                        "source_filter": [source],
                        "mode": mode,
                    },
                }, ensure_ascii=False) + "\n")
                process.stdin.flush()
                while True:
                    event = json.loads(process.stdout.readline())
                    if event.get("request_id") == request_id and event.get("type") == "done":
                        result = event["payload"]["result"]
                        break
                self.assertEqual(result["mode"], mode)
                if mode == "v3":
                    self.assertEqual(result["renderer"], "typed-v3")
                    self.assertFalse(result["fallback"])
                if mode == "compare":
                    self.assertEqual(result["comparison"]["formal_output_mode"], "legacy")
                    self.assertGreaterEqual(result["comparison"]["compared_files"], 1)
            process.stdin.write(json.dumps({"protocol": 1, "request_id": "shutdown", "type": "shutdown", "payload": {}}) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["type"], "done")
            process.stdin.close()
            process.stdout.close()
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
