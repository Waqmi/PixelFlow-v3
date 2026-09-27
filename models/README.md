# 外部模型目录

在 PixelFlow 设置中选择本目录作为外部模型总目录：

```text
models/
├── pose/yolo11n-pose.onnx
└── segmentation/yolo11n-seg.onnx
```

模型不会打包进 App，也不会由 App 下载。模型文件需拥有合法使用权，并兼容当前 ONNX Runtime；不兼容时对应能力会自动关闭并回退规则判断。

现场推理还需要 Worker Python 环境提供 `numpy` 和 `onnxruntime`；缺少任一依赖时，Worker 仍可启动并返回带原因的 fallback。

## 当前模型校验值

提交仓库时只保留本说明，不提交模型二进制。当前本地模型文件的 SHA-256 为：

| 文件 | SHA-256 |
| --- | --- |
| `pose/yolo11n-pose.onnx` | `86e1275fed40d3fce62acb80eae74c9e190e62c7e75da982de81787c4b3ef6f8` |
| `segmentation/yolo11n-seg.onnx` | `52269a89f1e8d258d3e170f60a30b7bcb417f44b892d23f105f37759e582db55` |

校验命令：

```sh
shasum -a 256 models/pose/yolo11n-pose.onnx models/segmentation/yolo11n-seg.onnx
```
