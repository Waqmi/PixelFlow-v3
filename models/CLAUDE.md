[根目录](../CLAUDE.md) > **models**

# models

## 变更记录

- 2026-10-02：初始化文档。

## 职责

外部 ONNX 模型的约定目录。模型**不入 Git、不打包进 App、不由 App 下载**；用户在 PixelFlow 设置里选择本目录作为“外部模型总目录”，Worker 运行时读取。仓库只保留 `README.md`、分类 sidecar 与导出/自检脚本。

## 目录约定

```text
models/
├── pose/yolo11n-pose.onnx                      # Pose（LocalModelAssistant）
├── segmentation/yolo11n-seg.onnx               # 分割（LocalSegmentationAssistant）
└── detection/
    ├── yolov8m-worldv2-pixelflow.onnx          # 检测（LocalDetectionAssistant，可选）
    └── yolov8m-worldv2-pixelflow.classes.json  # 必须与同名 onnx 放一起
```

- `detection/` 可选。当前检测框路由覆盖 `shoes`、`long_pants` / `pants` / `shorts`、`socks`、`knee_pads`、`hat`、`gloves`；`bag` 刻意复用 shirt 锚点规则且不使用检测框。ONNX 与 `.classes.json` 缺任一时，对应品类回退到 Pose、Seg 或规则构图。`classes.json` 含 `classes`（如 shoe、sneaker、sock、glove、hat、shirt、jacket、shorts、pants 等）和 `category_map`（类别 -> 品类，如 shoe/sneaker -> shoes）。
- 当前 SHA-256（以 `README.md` 为准）：pose `86e1275f...`、seg `52269a89...`、detection `467b89a9...`。校验：`shasum -a 256 models/pose/yolo11n-pose.onnx models/segmentation/yolo11n-seg.onnx models/detection/yolov8m-worldv2-pixelflow.onnx`。
- 运行还需要 Worker Python 环境有 `numpy` 与 `onnxruntime`；缺失时 Worker 仍可启动并返回带原因的 fallback。

## 关键文件

| 文件 | 说明 |
|---|---|
| `README.md` | 目录约定与 SHA-256 |
| `detection/export_yolo_world.py` | YOLO-World 固定类别导出脚本 |
| `detection/EXPORT_REPORT.md` | 导出过程与结果记录 |
| `detection/selfcheck/run_selfcheck.py`、`selfcheck_results.txt` | 检测模型自检脚本与结果 |
| `detection/unused/` | 未采用的 yolov8s 类别文件，仅留档 |

## 使用方（Worker 侧）

- `worker/pixelflow_core/model_service.py`：`configured_model_candidates` / `model_candidates` 按 purpose 找模型，`ModelProcessService` 在独立进程里加载 assistant。
- `worker/pixelflow_core/model_assistant.py`：`LocalModelAssistant`（Pose）、`LocalSegmentationAssistant`、`LocalDetectionAssistant`（`shoe_boxes`、`boxes_for(source, category)`）。
- `worker/pixelflow_core/model_cache.py`：缓存 identity 包含模型文件版本（含 pose+seg 两个模型，防止新旧混用）和 `COMPOSITION_RULES_VERSION`。

## 注意事项

- 更换模型文件会改变缓存 identity，旧缓存自然失效；更换后同步更新 `README.md` 里的 SHA-256。
- 设置页的 Worker `probe_models` 请求读取本目录并报告就绪状态。
