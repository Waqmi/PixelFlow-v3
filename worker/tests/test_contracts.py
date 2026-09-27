from __future__ import annotations

import unittest
import base64
import json
from io import BytesIO
import tempfile
from pathlib import Path

from PIL import Image

from pixelflow_core.contracts import (
    ClassificationResult,
    CompositionPlan,
    Message,
    ProtectedRegion,
    ProtocolError,
    RenderRequest,
    RenderResult,
    RenderAssets,
    RenderLayout,
    build_main_request,
    build_model_request,
    build_transparent_request,
    build_vip_request,
)
from pixelflow_core.worker import _read_preview, handle_message


class ProtocolTests(unittest.TestCase):
    def test_round_trip_preserves_unicode_payload(self) -> None:
        message = Message(1, "req-1", "ping", {"主题": "夜间"})
        self.assertEqual(Message.from_json(message.to_json()), message)

    def test_invalid_version_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            Message.from_json('{"protocol":2,"request_id":"r","type":"ping","payload":{}}')

    def test_unknown_request_returns_error(self) -> None:
        response, should_exit = handle_message(Message(1, "req-2", "nope", {}))
        self.assertIn('"error"', response)
        self.assertFalse(should_exit)

    def test_shutdown_is_terminal(self) -> None:
        response, should_exit = handle_message(Message(1, "req-3", "shutdown", {}))
        self.assertIn('"done"', response)
        self.assertTrue(should_exit)

    def test_preview_is_scaled_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.jpg"
            Image.new("RGB", (1600, 1200), "red").save(path)
            result = _read_preview({"path": str(path), "max_width": 198, "max_height": 138})
            self.assertEqual(result["mime_type"], "image/jpeg")
            image_bytes = base64.b64decode(result["data"])
            decoded = Image.open(BytesIO(image_bytes))
            self.assertLessEqual(decoded.width, 198)
            self.assertLessEqual(decoded.height, 138)

    def test_typed_image_contracts_validate_and_serialize(self) -> None:
        region = ProtectedRegion("shoe", (0, 1, 10, 20), confidence=0.9)
        plan = CompositionPlan(
            "shoe.jpg",
            "1440x1440",
            protected_regions=(region,),
            risk_flags=("shoe_risk",),
            requires_review=True,
        )
        classification = ClassificationResult("shoe.jpg", "model", 0.9)
        request = RenderRequest("shoe.jpg", "/tmp/output", "1440x1440", mode="compare", plan=plan)
        result = RenderResult("shoe.jpg", ("/tmp/output/main.jpg",), mode="legacy")

        request.validate()
        self.assertEqual(region.to_dict()["bbox"], [0.0, 1.0, 10.0, 20.0])
        self.assertEqual(plan.to_dict()["protected_regions"][0]["kind"], "shoe")
        self.assertEqual(classification.material_type, "model")
        self.assertEqual(result.to_dict()["outputs"], ["/tmp/output/main.jpg"])

    def test_four_request_builders_are_replayable(self) -> None:
        assets = RenderAssets(logo="logo.png", white_logo="white.png", border="border.png")
        layout = RenderLayout(category="产品主图", group="男_黑", filename="x.jpg")
        builders = (
            build_main_request,
            build_model_request,
            build_transparent_request,
            build_vip_request,
        )
        requests = [builder("x.jpg", "/tmp/output", "1440x1440", assets=assets, layout=layout) for builder in builders]
        for request in requests:
            request.validate()
            json.dumps(request.to_dict(), ensure_ascii=False)
        self.assertEqual([request.output_kind for request in requests], ["main", "model", "transparent", "vip"])


if __name__ == "__main__":
    unittest.main()
