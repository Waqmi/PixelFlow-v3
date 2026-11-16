"""Optional YOLO-World object detector (needs the .classes.json sidecar)."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from .onnx_session import _create_onnx_session, _decoded_source, _static_dimension


_GLOBAL_OBJECT_CACHE: dict[tuple[str, int, int, str], list[tuple[str, float, tuple[float, float, float, float]]]] = {}
# Detector shoe boxes larger than this share of the image are not shoes.
DETECTION_MAX_BOX_AREA = 0.40


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _nms(items: list[tuple[str, float, tuple[float, float, float, float]]], iou: float) -> list:
    kept: list = []
    for item in sorted(items, key=lambda entry: entry[1], reverse=True):
        if all(_iou(item[2], other[2]) < iou for other in kept):
            kept.append(item)
    return kept


class LocalDetectionAssistant:
    """Optional YOLO-World object detector (needs the ``.classes.json`` sidecar)."""

    def __init__(self, model_path: str | Path, score_threshold: float = 0.25, nms_iou: float = 0.5) -> None:
        try:
            import numpy as np
            import onnxruntime as ort  # noqa: F401
        except ImportError as error:  # pragma: no cover - optional runtime
            raise RuntimeError("未安装 ONNX Runtime 或 NumPy") from error
        self._np = np
        self.model_path = Path(model_path)
        sidecar = json.loads(self.model_path.with_suffix(".classes.json").read_text(encoding="utf-8"))
        self.classes: list[str] = list(sidecar["classes"])
        self.category_map: dict[str, str] = dict(sidecar.get("category_map", {}))
        self.session = _create_onnx_session(self.model_path)
        meta = self.session.get_inputs()[0]
        shape = meta.shape
        self.input_name = meta.name
        self.input_height = _static_dimension(shape[2] if len(shape) > 2 else None, 640)
        self.input_width = _static_dimension(shape[3] if len(shape) > 3 else None, 640)
        self.score_threshold = score_threshold
        self.nms_iou = nms_iou

    def _geometry(self, image_size: tuple[int, int]) -> tuple[float, int, int]:
        scale = min(self.input_width / image_size[0], self.input_height / image_size[1])
        pad_x = round((self.input_width - round(image_size[0] * scale)) / 2 - 0.1)
        pad_y = round((self.input_height - round(image_size[1] * scale)) / 2 - 0.1)
        return scale, pad_x, pad_y

    def _letterbox(self, image: Image.Image):
        rgb = image.convert("RGB")
        scale, pad_x, pad_y = self._geometry(rgb.size)
        resized = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", (self.input_width, self.input_height), (114, 114, 114))
        canvas.paste(resized, (pad_x, pad_y))
        array = self._np.asarray(canvas, dtype=self._np.float32) / 255.0
        return self._np.transpose(array, (2, 0, 1))[None, ...]

    def _decode(self, output, image_size: tuple[int, int]) -> list[tuple[str, float, tuple[float, float, float, float]]]:
        """Decode [1, 4 + classes, N] (cx, cy, w, h in letterbox px + scores) to per-class NMS boxes."""
        np = self._np
        data = np.asarray(output)[0].T
        scores = data[:, 4:]
        class_ids = scores.argmax(axis=1)
        best = scores[np.arange(len(class_ids)), class_ids]
        keep = best >= self.score_threshold
        scale, pad_x, pad_y = self._geometry(image_size)
        width, height = image_size
        found: list[tuple[str, float, tuple[float, float, float, float]]] = []
        for (cx, cy, w, h), class_id, score in zip(data[keep, :4], class_ids[keep], best[keep]):
            box = (
                max(0.0, min(width, (cx - w / 2 - pad_x) / scale)),
                max(0.0, min(height, (cy - h / 2 - pad_y) / scale)),
                max(0.0, min(width, (cx + w / 2 - pad_x) / scale)),
                max(0.0, min(height, (cy + h / 2 - pad_y) / scale)),
            )
            found.append((self.classes[int(class_id)], float(score), box))
        result: list[tuple[str, float, tuple[float, float, float, float]]] = []
        for label in {item[0] for item in found}:
            result.extend(_nms([item for item in found if item[0] == label], self.nms_iou))
        return sorted(result, key=lambda entry: entry[1], reverse=True)

    def detect(self, source: Path) -> list[tuple[str, float, tuple[float, float, float, float]]]:
        stat = source.stat()
        key = (str(source.resolve()), stat.st_mtime_ns, stat.st_size, str(self.model_path.resolve()))
        if key in _GLOBAL_OBJECT_CACHE:
            return _GLOBAL_OBJECT_CACHE[key]
        decoded = _decoded_source(source)
        image_size = decoded.size
        input_array = self._letterbox(decoded)
        output = self.session.run(None, {self.input_name: input_array})[0]
        _GLOBAL_OBJECT_CACHE[key] = result = self._decode(output, image_size)
        return result

    def shoe_boxes(self, source: Path) -> list[list[float]]:
        """Shoe boxes in source px: shoe labels merged across labels, oversized boxes dropped."""
        return self.boxes_for(source, "shoes")

    def boxes_for(self, source: Path, category: str) -> list[list[float]]:
        """Return detector boxes mapped to a product category in source px."""
        with Image.open(source) as opened:
            area = opened.width * opened.height
        items = [item for item in self.detect(source) if self.category_map.get(item[0]) == category]
        return [
            list(box) for _label, _score, box in _nms(items, self.nms_iou)
            if (box[2] - box[0]) * (box[3] - box[1]) <= area * DETECTION_MAX_BOX_AREA
        ]
