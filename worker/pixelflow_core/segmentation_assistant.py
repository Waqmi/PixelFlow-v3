"""Optional YOLO segmentation evidence for ambiguous material photos."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from .onnx_session import _create_onnx_session, _decoded_source, _static_dimension


_GLOBAL_SEGMENTATION_CACHE: dict[tuple[str, int, int, str], dict[str, object]] = {}


class LocalSegmentationAssistant:
    """Optional YOLO segmentation evidence for ambiguous material photos."""

    def __init__(self, model_path: str | Path, confidence_threshold: float = 0.28) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as error:  # pragma: no cover - optional runtime
            raise RuntimeError("未安装 ONNX Runtime 或 NumPy") from error
        self._np = np
        self.model_path = Path(model_path)
        self.session = _create_onnx_session(self.model_path)
        meta = self.session.get_inputs()[0]
        shape = meta.shape
        self.input_name = meta.name
        self.input_height = _static_dimension(shape[2] if len(shape) > 2 else None, 640)
        self.input_width = _static_dimension(shape[3] if len(shape) > 3 else None, 640)
        self.confidence_threshold = confidence_threshold

    def _letterbox(self, image: Image.Image):
        rgb = image.convert("RGB")
        scale = min(self.input_width / rgb.width, self.input_height / rgb.height)
        resized = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", (self.input_width, self.input_height), (114, 114, 114))
        pad_x = (self.input_width - resized.width) / 2
        pad_y = (self.input_height - resized.height) / 2
        canvas.paste(resized, (round(pad_x), round(pad_y)))
        array = self._np.asarray(canvas, dtype=self._np.float32) / 255.0
        return self._np.transpose(array, (2, 0, 1))[None, ...]

    def _mask_rows(self, mask, box, image_size: tuple[int, int]) -> list[list[float]]:
        """Return the person mask as per-row [top, bottom, left, right] image ratios.

        The mask is limited to its detection box (standard YOLO-seg decoding)
        and mapped back through the letterbox, so no row can fall outside the
        source image.
        """
        np = self._np
        image_width, image_height = image_size
        rows, cols = mask.shape
        step_y = self.input_height / rows
        step_x = self.input_width / cols
        scale = min(self.input_width / image_width, self.input_height / image_height)
        pad_x = round((self.input_width - max(1, round(image_width * scale))) / 2)
        pad_y = round((self.input_height - max(1, round(image_height * scale))) / 2)
        cx, cy, width, height = (float(value) for value in box)
        x0 = max(0, int((cx - width / 2) / step_x))
        x1 = min(cols, int(math.ceil((cx + width / 2) / step_x)))
        y0 = max(0, int((cy - height / 2) / step_y))
        y1 = min(rows, int(math.ceil((cy + height / 2) / step_y)))
        to_x = lambda value: max(0.0, min(1.0, (value - pad_x) / scale / image_width))
        to_y = lambda value: max(0.0, min(1.0, (value - pad_y) / scale / image_height))
        result: list[list[float]] = []
        for row in range(y0, y1):
            filled = np.flatnonzero(mask[row, x0:x1])
            if not filled.size:
                continue
            top, bottom = to_y(row * step_y), to_y((row + 1) * step_y)
            if bottom <= top:
                continue
            result.append([
                round(top, 4),
                round(bottom, 4),
                round(to_x((x0 + filled[0]) * step_x), 4),
                round(to_x((x0 + filled[-1] + 1) * step_x), 4),
            ])
        return result

    def segment(self, source: Path) -> dict[str, object]:
        stat = source.stat()
        key = (str(source.resolve()), stat.st_mtime_ns, stat.st_size, str(self.model_path.resolve()))
        if key in _GLOBAL_SEGMENTATION_CACHE:
            return _GLOBAL_SEGMENTATION_CACHE[key]
        decoded = _decoded_source(source)
        image_size = decoded.size
        input_array = self._letterbox(decoded)
        outputs = self.session.run(None, {self.input_name: input_array})
        arrays = [self._np.asarray(item) for item in outputs]
        proto = next((item for item in arrays if item.ndim == 4), None)
        prediction = next((item for item in arrays if item.ndim == 3), None)
        if proto is None or prediction is None:
            raise RuntimeError("不支持的 YOLO Seg 输出格式")
        proto = proto[0]
        data = prediction[0]
        if data.shape[0] < data.shape[1] and data.shape[0] <= 256:
            data = data.T
        mask_count = proto.shape[0]
        if data.shape[1] < 5 + mask_count:
            raise RuntimeError("Seg 模型通道数无效")
        best = max(data, key=lambda row: float(row[4]))
        confidence = float(best[4])
        if confidence > 1.0:
            confidence = 1.0 / (1.0 + pow(2.718281828, -confidence))
        if confidence < self.confidence_threshold:
            result = {"available": True, "model": self.model_path.name, "confidence": round(confidence, 4), "mask_area_ratio": 0.0, "mask_edge_ratio": 0.0}
            _GLOBAL_SEGMENTATION_CACHE[key] = result
            return result
        coeff = best[-mask_count:]
        mask = 1.0 / (1.0 + self._np.exp(-self._np.clip(self._np.tensordot(coeff, proto, axes=(0, 0)), -40, 40))) >= 0.5
        ys, xs = self._np.where(mask)
        edge = self._np.concatenate((mask[0, :], mask[-1, :], mask[:, 0], mask[:, -1]))
        result = {
            "available": True,
            "model": self.model_path.name,
            "confidence": round(confidence, 4),
            "mask_area_ratio": round(float(mask.mean()), 4),
            "mask_edge_ratio": round(float(edge.mean()), 4),
            "mask_bbox_ratio": [round(float(xs.min() / mask.shape[1]), 4), round(float(ys.min() / mask.shape[0]), 4), round(float((xs.max() + 1) / mask.shape[1]), 4), round(float((ys.max() + 1) / mask.shape[0]), 4)] if len(xs) else None,
            "mask_rows": self._mask_rows(mask, best[:4], image_size),
        }
        _GLOBAL_SEGMENTATION_CACHE[key] = result
        return result
