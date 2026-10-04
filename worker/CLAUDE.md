[根目录](../CLAUDE.md) > **worker**

# worker

## 变更记录

- 2026-10-02：初始化文档。

## 职责

Python Worker：从 stdin 读 JSONL 请求，向 stdout 写 JSONL 事件。负责素材分类（主图/细节/模特/模特细节 + 品类）、构图规划（锚点构图）、渲染输出（Renderer）、ONNX 模型推理（独立进程服务）、模型结果缓存。用 PyInstaller 打包为 onedir 二进制随 App 分发。

## 入口

- 开发：`cd worker && python -m pixelflow_core.worker`
- 打包入口：`worker_entry.py`（frozen 时设置 `PIXELFLOW_RESOURCE_ROOT`，调用 `pixelflow_core.worker.main`，结束用 `os._exit` 避免 onnxruntime 退出时崩溃）
- 兼容 shim：`local_model_assistant.py`、`model_process_service.py`、`model_result_cache.py` 只是转发到 `legacy_engine` 的同名模块

## 目录

```text
worker/
├── pixelflow_core/        # 主包
│   ├── legacy_engine/     # 从 v2.6 复制的兼容图像核心（离线运行用；内部不展开）
│   └── *.py               # 见下表
├── tests/                 # unittest，10 个 test_*.py
├── resources/             # 模板 JSON、图标、svg（打包为 resources/）
│   └── templates/         # category_templates.json、render_rules.json
├── build_worker.py        # PyInstaller 构建脚本
├── pixelflow-worker-ondir.spec / pixelflow-worker.spec
├── requirements.lock      # CI 使用的依赖锁定
└── worker_entry.py
```

`worker/build*`、`worker/dist*`、`worker/.pyinstaller-config*` 是构建产物/缓存，不要改。

## 关键文件（pixelflow_core）

| 文件 | 职责 |
|---|---|
| `worker.py` | 协议主循环 `main()`；`WorkerRuntime`（启动/取消/关闭生成与分类、`probe_models`、`clear_cache`）；`SUPPORTED_REQUESTS`；`RULES_VERSION`；`_read_preview`（有界缓存，限 128 项，路径范围校验）；`_ready_payload`（含 `worker_version`、`rules_version`、`build_hash`） |
| `contracts.py` | 数据契约：`Message`、`ProtocolError`、`CompositionPlan`、`ProtectedRegion`、`RenderRequest`、`RenderResult`、`build_main_request` / `build_model_request` / `build_transparent_request` / `build_vip_request` |
| `classification.py` | `ClassificationRequest`、`ClassificationDecision`、`V26ClassifierAdapter`（封装 v2.6 规则分类，默认根目录为 `legacy_engine`，可由 `PIXELFLOW_V2_ROOT` 覆盖） |
| `v3_classification.py` | `V3SourceTypeClassifier`、`V3CategoryClassifier`、`V3ClassifierAdapter`（`classify(request, progress)`；`_apply_optional_models` 叠加 Pose/Seg 判断）；`_foreground_metrics` 使用 `get_flattened_data()` |
| `engine.py` | `GenerationRequest`、`V3EngineAdapter`（生成主流程，维护 v3 输出 manifest 用于复用/清理）、`ImageEngine`、`V26EngineAdapter`（对照用） |
| `renderer.py` | `Renderer`（`render`、`render_source_batch`、`render_batch`、`validate_existing`）、`RenderConfig`；`_side_filled_window` 调用 `side_fill.extend_side`；输出 JPEG/PNG 并按 `max_size_mb` 压缩；`_light_foreground_bbox`、`_background_to_alpha` 使用 `get_flattened_data()` |
| `side_fill.py` | `extend_side(region, strip_w, ext, side)`、`strip_structure`、`strip_partial`：仅当两侧条带是纯色影棚背景时合成补边；`STRUCTURE_MAX_SHARE`、`PARTIAL_MAX_SHARE` 为判定阈值 |
| `model_assistant.py`（约 3100 行） | `anchor_crop_box`、`LocalModelAssistant`（Pose，`compose_for_sizes`）、`LocalSegmentationAssistant`、`LocalDetectionAssistant`；各品类 `_*_crop_box` 与 `*_protected_region`；`resolve_model_source_type` |
| `shoe_composition.py` | 鞋类构图（Pose + 检测框），evidence 里写入 `composition_rules_version` |
| `model_service.py` | `ModelProcessService`：独立进程加载 assistant，`get_model_service()`；模型缺失/出错返回 fallback（`_fallback_composition`、`_fallback_segmentation`） |
| `model_cache.py` | `COMPOSITION_RULES_VERSION`、`build_cache_identity`、`lookup_model_result` / `store_model_result` / `clear_model_result_cache`；缓存目录由 `model_cache_dir()` 决定 |
| `compare.py` | 输出树对比（`compare_output_trees`、`tree_manifest`、`aggregate_tree_sha256`），供基线工具使用 |

## 锚点构图

`anchor_crop_box(detections, image_size, target_size, category, segmentation, shoe_boxes, detector_boxes, allow_side_fill)` 返回裁剪窗口 dict，或 `None`（缺少脚踝/品类不支持时保持原构图）。`ANCHOR_CATEGORIES` = shoes、shirt、long_pants、pants、shorts、socks、hat、gloves、knee_pads、bag；`NEW_ANCHOR_CATEGORIES` = socks、hat、gloves、knee_pads、bag。bag 刻意复用 shirt 的锚点规则，且不使用检测框。检测模型（`LocalDetectionAssistant.boxes_for`）仅作为补充框。左右超出 0/1 的部分是 side fill，条带宽度存于 `plan.evidence["side_fill"]`，由 Renderer 合成。

## 协议消息

请求（`SUPPORTED_REQUESTS`）：`ping`、`probe_models`、`classify_materials`、`start_generation`、`cancel_generation`、`retry_failed`、`clear_cache`、`read_preview`、`shutdown`。事件：`ready`（启动即发）、`status`、`progress`、`preview_ready`、`done`、`cancelled`、`error`。`probe_models`、`read_preview` 在后台线程/线程池处理；`start_generation` / `classify_materials` 由 `WorkerRuntime` 单任务执行。详见 [packages/protocol/CLAUDE.md](../packages/protocol/CLAUDE.md) 与 `docs/worker-protocol.md`。

## 测试

```bash
# 与 CI 一致（CI 使用 Python 3.12；本机版本必须由 PIXELFLOW_PYTHON 或 worker/.venv 明确选择）
PYTHONPATH=worker python -m unittest discover -s worker/tests -p 'test_*.py'
# 单文件
cd worker && python3 -m unittest tests/test_contracts.py
```

测试文件：`test_contracts`、`test_classification`、`test_v3_classification`、`test_engine`、`test_renderer`、`test_anchor_composition`、`test_shoe_composition`、`test_model_cache`、`test_compare`、`test_worker_process`。
`test_classification.py`、`test_engine.py`、`test_shoe_composition.py`、`test_worker_process.py` 在外部素材目录/文件缺失时 `skipTest`；没有素材的机器上“通过”不代表这些路径被验证。

## 构建

```bash
./tools/build_worker.sh
```

- 正式打包入口 `tools/build_worker.sh` 的解释器选择：`PIXELFLOW_PYTHON` > `worker/.venv/bin/python`；两者都不存在时明确失败。直接调用 `build_worker.py` 仍保留当前解释器回退，便于 CI/开发自定义入口。
- 模式：`PIXELFLOW_WORKER_MODE`，默认 `ondir` -> `worker/dist-ondir/`（spec：`pixelflow-worker-ondir.spec`）；`onefile` -> `worker/dist/`（spec：`pixelflow-worker.spec`）。`PYINSTALLER_CONFIG_DIR` 可覆盖配置目录。
- onedir 构建后 `materialize_symlinks` 把 PyInstaller 的符号链接实体化，否则 Tauri 无法复制资源。
- Tauri 把 `worker/dist-ondir/pixelflow-worker` 放到 `Contents/Resources/pixelflow-worker`。
- 打包后用 `ditto` 复制 `.app` 必须重新 ad-hoc 签名并校验。

## 注意事项

- **规则版本**：`worker.py` 的 `RULES_VERSION` 与 `model_cache.py` 的 `COMPOSITION_RULES_VERSION` 当前都是 `v3-composition-11`，另需同步 `apps/desktop/src/App.vue` 的 `expectedRulesVersion` 与 `tests/test_shoe_composition.py`。
- **缓存 identity**：`build_cache_identity` 含模型文件版本（pose 与 seg 同时进入，避免新旧混用）、输出尺寸、用途与 `composition_rules_version`；改构图规则不升版本会命中旧缓存。
- 不得改变 shirt / shoes / pants 既有行为而不跑回归；side fill 改动后必须 100% 缩放目视检查渲染图。
- 缺少 `numpy` / `onnxruntime` 或模型文件时，Worker 仍启动并返回带原因的 fallback，不应抛错中断。
- 输出复用：同名输出默认复用，`force_rerender` 才覆盖。
- 外部模型目录约定见 [models/CLAUDE.md](../models/CLAUDE.md)。
