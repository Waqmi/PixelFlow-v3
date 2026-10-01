# 任务：YOLO-World 检测模型下载与 ONNX 导出

执行方：Codex　　交付后由 Claude 做准确率验证与接入
日期：2026-10-01

## 1. 目标

导出固定类别的 YOLO-World 检测模型（ONNX），供 PixelFlow Worker 离线推理，用于定位鞋（本期接入）以及袜、手套、帽子、上衣、短裤、长裤（本期只导出，不接入）。

本任务**只做下载、导出和基本可用性自检**，不做准确率评估，不改项目代码。

## 2. 硬性约束

1. **不得修改项目任何代码、配置、依赖**：`worker/.venv`、`requirements.lock`、`apps/`、`worker/pixelflow_core/` 一律不动。
2. 导出环境必须独立：在 `/Users/weiheng/Desktop/PixelFlow/06_开发环境与缓存/yolo-world-export/` 下新建虚拟环境，所有下载缓存也放在这里（设置 `PIP_CACHE_DIR`、`TORCH_HOME`、`ULTRALYTICS` 相关缓存目录指向该目录），不要装进系统 Python。
   - 项目 Worker 用的是 Python 3.14；如 torch/ultralytics 不支持 3.14，导出环境可用 3.11 或 3.12（例如通过 `uv python install 3.12`）。导出环境的 Python 版本与项目无关。
3. 机器为 Apple Silicon（arm64），导出在 CPU 上进行即可。
4. 不提交 git，不把模型文件加入仓库（`models/README.md` 约定只记录校验值）。

## 3. 安装

- `ultralytics`（最新稳定版）及其依赖（torch、onnx、onnxslim 等）。
- `set_classes` 需要 CLIP 文本编码器：ultralytics 首次调用时会自动安装 `git+https://github.com/ultralytics/CLIP.git` 并下载权重；如自动安装失败，请手动安装后重试。
- 记录最终版本号：Python、ultralytics、torch、onnx、onnxruntime（导出环境内）。

## 4. 权重

使用支持导出的 **v2** 系列 YOLO-World 权重（官方 `yolov8s-worldv2.pt`、`yolov8m-worldv2.pt`，由 ultralytics 自动下载即可）。

## 5. 类别清单（英文提示词，顺序固定）

每个提示词是独立类别，类别索引 = 列表下标；右侧是映射回 PixelFlow 品类的分组。

| 索引 | 提示词 | PixelFlow 品类 | 说明 |
|---|---|---|---|
| 0 | `shoe` | shoes | |
| 1 | `sneaker` | shoes | |
| 2 | `sock` | socks | 通用 |
| 3 | `ankle sock` | socks | 短袜 |
| 4 | `crew sock` | socks | 中筒袜 |
| 5 | `knee-high sock` | socks | 长筒袜 |
| 6 | `glove` | gloves | |
| 7 | `hat` | hat | 通用 |
| 8 | `sun visor` | hat | 空顶帽 |
| 9 | `baseball cap` | hat | 圆顶帽（待确认，见第 9 节） |
| 10 | `beanie` | hat | 毛线帽 |
| 11 | `shirt` | shirt | 上衣 |
| 12 | `jacket` | shirt | 上衣（外套类） |
| 13 | `shorts` | shorts | |
| 14 | `pants` | long_pants | 长裤 |

## 6. 需要导出的模型（共 3 个）

| 文件名 | 权重 | 类别 |
|---|---|---|
| `yolov8s-worldv2-pixelflow.onnx` | yolov8s-worldv2 | 第 5 节全部 15 类 |
| `yolov8m-worldv2-pixelflow.onnx` | yolov8m-worldv2 | 第 5 节全部 15 类 |
| `yolov8s-worldv2-shoe.onnx` | yolov8s-worldv2 | 仅 `shoe`、`sneaker` 2 类 |

第三个只认鞋的模型用于对照：验证类别变多后，鞋的检测会不会变差。

导出参数：

```python
from ultralytics import YOLO
model = YOLO("yolov8s-worldv2.pt")
model.set_classes(CLASSES)          # 第 5 节列表，顺序不可变
model.export(format="onnx", imgsz=640, dynamic=False, simplify=True, opset=17)
```

- 输入固定为 `[1, 3, 640, 640]`，和现有 `models/pose/yolo11n-pose.onnx` 一致。
- 如 opset 17 无法导出，可依次尝试 16、12，并记录最终使用的 opset。
- 不要开启 NMS 内置导出（`nms=False`，保持默认），后处理由 Worker 自己做。

## 7. 交付物

放到 `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/models/detection/`：

1. 第 6 节的 3 个 `.onnx` 文件。
2. 每个模型一份同名 `.classes.json`，内容为 `{"classes": [...], "category_map": {"shoe": "shoes", ...}}`，顺序与导出时一致。
3. `EXPORT_REPORT.md`，包含：
   - 各版本号（第 3 节）、权重文件名及其 SHA-256；
   - 每个 ONNX 的文件大小、SHA-256、opset、输入/输出名称与形状（预期输出类似 `[1, 4+类别数, 8400]`）；
   - 完整的导出命令或脚本（脚本另存为 `export_yolo_world.py` 放同目录）；
   - 第 8 节自检的输出。

## 8. 完成标准（自检）

**必须用项目 Worker 的环境加载**，而不是导出环境：

```
/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/worker/.venv/bin/python
```

该环境为 onnxruntime 1.30.0、numpy 2.5.3、Python 3.14.5，且未安装 `onnx` 包，自检脚本不要依赖 `onnx`。

对 3 个模型逐一自检：

1. `onnxruntime.InferenceSession` 能加载，并打印输入/输出形状。
2. 用以下 2 张图做推理，按常规 YOLOv8 方式解码（letterbox 到 640、置信度 ≥ 0.25、类别内 NMS IoU 0.5）。对每张图列出检测框的类别、置信度、框坐标（原图像素），并把画好框的图存到 `models/detection/selfcheck/`：
   - `/Users/weiheng/Desktop/PixelFlow/05_测试素材与输出/鞋/2026-8-3Bmai3563.jpg`（双人，预期能检出约 4 只鞋）
   - `/Users/weiheng/Desktop/PixelFlow/05_测试素材与输出/鞋/2026-9-4BMAI1132.jpg`（单人，左侧鞋尖翘起，预期能检出 2 只鞋）
3. 同一张图跑两次，输出完全一致。

如果自检时鞋明显检不出来，**不要自行调参或换模型**，如实写进报告即可，由 Claude 判断。

## 9. 需要用户确认的项（不阻塞导出）

- “圆顶帽”暂按 `baseball cap`（棒球帽）处理。如果指的是盆帽/渔夫帽（`bucket hat`）或其他款式，后续可以重新导出，成本很低。
- 上衣暂用 `shirt` + `jacket` 两个提示词。本期上衣仍用人体关键点构图，这两类只是为以后备用。

## 10. 许可证提示

ultralytics 及 YOLO-World 权重为 AGPL-3.0，与项目现有的 yolo11n-pose / yolo11n-seg 相同，不新增许可证风险。请在报告中记录权重来源链接。
