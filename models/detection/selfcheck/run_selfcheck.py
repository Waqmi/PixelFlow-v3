from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "selfcheck"
IMAGES = [
    Path("/Users/weiheng/Desktop/PixelFlow/05_测试素材与输出/鞋/2026-8-3Bmai3563.jpg"),
    Path("/Users/weiheng/Desktop/PixelFlow/05_测试素材与输出/鞋/2026-9-4BMAI1132.jpg"),
]
MODELS = sorted(ROOT.glob("yolov8*-worldv2-*.onnx"))


def letterbox(image: np.ndarray, size: int = 640) -> tuple[np.ndarray, float, tuple[float, float]]:
    h, w = image.shape[:2]
    scale = min(size / h, size / w)
    nw, nh = round(w * scale), round(h * scale)
    resized = np.asarray(Image.fromarray(image).resize((nw, nh), Image.Resampling.BILINEAR))
    pad_x, pad_y = (size - nw) / 2, (size - nh) / 2
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    left, top = round(pad_x - 0.1), round(pad_y - 0.1)
    canvas[top : top + nh, left : left + nw] = resized
    return canvas, scale, (left, top)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float = 0.5) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (
            boxes[order[1:], 3] - boxes[order[1:], 1]
        )
        iou = inter / np.maximum(area_i + area - inter, 1e-9)
        order = order[1:][iou < iou_threshold]
    return keep


def detect(session: ort.InferenceSession, image: np.ndarray, classes: list[str]) -> list[tuple[int, float, list[float]]]:
    padded, scale, (pad_x, pad_y) = letterbox(image)
    tensor = padded[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    output = session.run(None, {session.get_inputs()[0].name: tensor})[0]
    pred = output[0].T  # [8400, 4 + classes]
    boxes_xywh = pred[:, :4]
    class_scores = pred[:, 4:]
    class_ids = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(len(class_ids)), class_ids]
    mask = scores >= 0.25
    boxes_xywh, scores, class_ids = boxes_xywh[mask], scores[mask], class_ids[mask]
    if not len(scores):
        return []
    boxes = np.empty_like(boxes_xywh)
    boxes[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2
    boxes[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2
    boxes[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2
    boxes[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / scale
    h, w = image.shape[:2]
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
    selected: list[int] = []
    for class_id in np.unique(class_ids):
        indices = np.where(class_ids == class_id)[0]
        selected.extend(indices[nms(boxes[indices], scores[indices])].tolist())
    selected.sort(key=lambda i: float(scores[i]), reverse=True)
    return [(int(class_ids[i]), float(scores[i]), boxes[i].round(1).tolist()) for i in selected]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    lines: list[str] = []
    for model_path in MODELS:
        classes = json.loads(model_path.with_suffix(".classes.json").read_text(encoding="utf-8"))["classes"]
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        inp, out = session.get_inputs()[0], session.get_outputs()[0]
        lines.append(f"MODEL {model_path.name}")
        lines.append(f"INPUT name={inp.name} shape={inp.shape} type={inp.type}")
        lines.append(f"OUTPUT name={out.name} shape={out.shape} type={out.type}")
        for image_path in IMAGES:
            image = np.asarray(Image.open(image_path).convert("RGB"))
            detections_a = detect(session, image, classes)
            detections_b = detect(session, image, classes)
            deterministic = detections_a == detections_b
            annotated = Image.fromarray(image.copy())
            draw = ImageDraw.Draw(annotated)
            for class_id, score, box in detections_a:
                x1, y1, x2, y2 = map(int, box)
                draw.rectangle((x1, y1, x2, y2), outline=(0, 220, 0), width=3)
                draw.text((x1, max(0, y1 - 18)), f"{classes[class_id]} {score:.2f}", fill=(0, 220, 0))
            out_path = OUT / f"{model_path.stem}__{image_path.stem}.jpg"
            annotated.save(out_path, quality=92)
            lines.append(f"IMAGE {image_path.name} deterministic={deterministic} detections={len(detections_a)}")
            for class_id, score, box in detections_a:
                lines.append(f"  - {classes[class_id]} {score:.6f} box={box}")
        lines.append("")
    result = "\n".join(lines)
    (OUT / "selfcheck_results.txt").write_text(result + "\n", encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
