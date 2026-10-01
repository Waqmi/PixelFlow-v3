from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

CLASSES = [
    "shoe",
    "sneaker",
    "sock",
    "ankle sock",
    "crew sock",
    "knee-high sock",
    "glove",
    "hat",
    "sun visor",
    "baseball cap",
    "beanie",
    "shirt",
    "jacket",
    "shorts",
    "pants",
]
SHOE_CLASSES = ["shoe", "sneaker"]
CATEGORY_MAP = {
    "shoe": "shoes",
    "sneaker": "shoes",
    "sock": "socks",
    "ankle sock": "socks",
    "crew sock": "socks",
    "knee-high sock": "socks",
    "glove": "gloves",
    "hat": "hat",
    "sun visor": "hat",
    "baseball cap": "hat",
    "beanie": "hat",
    "shirt": "shirt",
    "jacket": "shirt",
    "shorts": "shorts",
    "pants": "long_pants",
}

OUT_DIR = Path(__file__).resolve().parent
EXPORT_ROOT = OUT_DIR.parents[2] / "06_开发环境与缓存" / "yolo-world-export"
os.environ.setdefault("YOLO_CONFIG_DIR", str(EXPORT_ROOT / "ultralytics-config"))
os.environ.setdefault("TORCH_HOME", str(EXPORT_ROOT / "torch-cache"))
os.environ.setdefault("ULTRALYTICS_HOME", str(EXPORT_ROOT / "ultralytics-cache"))

from ultralytics import YOLO


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_one(weight: str, classes: list[str], output_name: str) -> Path:
    model = YOLO(weight)
    model.set_classes(classes)
    exported = model.export(
        format="onnx",
        imgsz=640,
        dynamic=False,
        simplify=True,
        opset=17,
        nms=False,
    )
    source = Path(exported)
    destination = OUT_DIR / output_name
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    metadata = {
        "classes": classes,
        "category_map": {name: CATEGORY_MAP[name] for name in classes},
    }
    (OUT_DIR / f"{destination.stem}.classes.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"EXPORTED {destination} size={destination.stat().st_size} sha256={sha256(destination)}")
    return destination


def main() -> None:
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    os.chdir(EXPORT_ROOT)
    export_one("yolov8s-worldv2.pt", CLASSES, "yolov8s-worldv2-pixelflow.onnx")
    export_one("yolov8m-worldv2.pt", CLASSES, "yolov8m-worldv2-pixelflow.onnx")
    export_one("yolov8s-worldv2.pt", SHOE_CLASSES, "yolov8s-worldv2-shoe.onnx")


if __name__ == "__main__":
    main()
