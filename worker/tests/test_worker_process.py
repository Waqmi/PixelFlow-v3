from __future__ import annotations

import json
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
