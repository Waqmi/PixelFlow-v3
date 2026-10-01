# YOLO-World v2 导出报告

日期：2026-10-01

## 环境

- Python：3.14.5
- ultralytics：8.4.170
- torch：2.14.1
- onnx：1.23.1
- onnxruntime：1.30.0
- onnxslim：0.1.97
- 平台：Apple Silicon arm64，CPU 导出
- 独立环境：`/Users/weiheng/Desktop/PixelFlow/06_开发环境与缓存/yolo-world-export/venv`

## 权重来源与 SHA-256

权重由 Ultralytics 自动下载，来源链接：

- https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8s-worldv2.pt
- https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8m-worldv2.pt

| 文件 | SHA-256 |
|---|---|
| `yolov8s-worldv2.pt` | `9b2c17ab6124a913e9b3a5c170617920d91b0f01111a8479da69f00e2cf27792` |
| `yolov8m-worldv2.pt` | `b614d33aa35b8e61d988041ff6939dfb3ed627af88ccaf643e4cdb822eb41d71` |

CLIP 文本编码器来自 https://github.com/ultralytics/CLIP.git（commit `a13192f8cb767260d7dfd98c843b0716593169e7`），由 Ultralytics 首次 `set_classes` 自动安装并下载。

## 导出物

导出参数：`format=onnx, imgsz=640, dynamic=False, simplify=True, opset=17, nms=False`。输入固定为 `[1, 3, 640, 640]`，输出为 `[1, 4+类别数, 8400]`。

| 文件 | 类别数 | 大小（字节） | SHA-256 | 输入 | 输出 |
|---|---:|---:|---|---|---|
| `yolov8s-worldv2-pixelflow.onnx` | 15 | 50,189,626 | `3d000af39b76192385d8e23109945eb22f46e0d21d703424f9264f3280874b9c` | `images [1,3,640,640]` | `output0 [1,19,8400]` |
| `yolov8m-worldv2-pixelflow.onnx` | 15 | 112,266,933 | `467b89a95b8f9f99d81483e4d768fde008690600649e817964655ee590cff942` | `images [1,3,640,640]` | `output0 [1,19,8400]` |
| `yolov8s-worldv2-shoe.onnx` | 2 | 50,079,604 | `0fa9352229d50090d4fdaca587d510f9102397b1728d57e889b3b32a0f098593` | `images [1,3,640,640]` | `output0 [1,6,8400]` |

类别顺序和映射记录在各自的 `.classes.json` 中。完整导出脚本为同目录的 `export_yolo_world.py`。

## Worker 环境自检

自检实际使用 `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/worker/.venv/bin/python`，仅使用 Worker 已有的 `onnxruntime`、`numpy` 和 `Pillow`，未安装任何依赖。完整逐框输出保存在 `selfcheck/selfcheck_results.txt`，标注图保存在 `selfcheck/`。

结果摘要：

| 模型 | 2026-8-3Bmai3563.jpg | 2026-9-4BMAI1132.jpg | 两次推理一致 |
|---|---:|---:|---|
| `yolov8m-worldv2-pixelflow` | 4 框（均 sneaker） | 6 框（含 hat/glove/shoe/sneaker） | 是 |
| `yolov8s-worldv2-pixelflow` | 22 框 | 7 框 | 是 |
| `yolov8s-worldv2-shoe` | 4 框（均 sneaker） | 1 框（sneaker） | 是 |

自检按置信度 `≥0.25`、类别内 NMS IoU `0.5` 解码。第一张图的 m/s/鞋专用模型均检出 4 个鞋相关框；第二张图鞋专用模型只检出 1 个框，m 模型检出 1 个 sneaker 加 1 个 shoe，且多类别 s 模型有额外类别框。按任务要求如实记录，未调参或换模型；准确率判断留给后续 Claude 验证。

## 完整导出命令

```bash
/Users/weiheng/Desktop/PixelFlow/06_开发环境与缓存/yolo-world-export/venv/bin/python \
  /Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/models/detection/export_yolo_world.py
```

