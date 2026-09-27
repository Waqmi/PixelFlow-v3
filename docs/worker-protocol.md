# PixelFlow Worker 协议 v1

协议版本：`1`（已冻结；不兼容变化必须升级版本）

前端/Tauri 不直接调用 Python 内部函数。双方通过 stdin/stdout 交换一行
一个 JSON 对象（JSON Lines）。日志写 stderr，避免污染协议流。

## 请求

```json
{"protocol":1,"request_id":"req-001","type":"ping","payload":{}}
```

支持的请求类型：

```text
ping
probe_models
classify_materials
start_generation
cancel_generation
retry_failed
clear_cache
read_preview
shutdown
```

请求 payload 约定：

- `classify_materials`：`product_root` 必填；可选 `source_filter`、`overrides`、`workers`；
- `start_generation` / `retry_failed`：使用 `GenerationRequest` 对应字段，至少包含
  `product_root`、`output_root`、`selected_sizes`；可选 `max_size_mb` 控制单文件大小、
  `logo_overrides` 替换 Logo 资源、`folder_category_overrides` 覆盖文件夹品类、
  `include_selling_point_images` 与 `selling_points` 生成卖点图；`mode` 可选为
  `legacy`、`v3` 或 `compare`，省略时使用 `PIXELFLOW_ENGINE_MODE`，默认 `v3`；
- `cancel_generation`：payload 可为空；取消当前唯一运行中的任务；
- `probe_models`：可选 `model_root`；
- `read_preview`：`path`、`allowed_roots` 必填；可选 `max_width`、`max_height`，Worker 使用
  PIL 生成缩略图，返回 `done`，payload 为 `{mime_type, data}`，其中 `data` 是缩略图的
  base64，不是原图。Host 和 Worker 都会解析真实路径并拒绝越出当前任务或用户选择目录的
  请求；缩略图按路径、文件 mtime/大小和目标尺寸在进程内做有界缓存。
- `clear_cache`、`ping`、`shutdown`：payload 为空对象。

## 事件

```json
{"protocol":1,"request_id":"req-001","type":"ready","payload":{"worker_version":"3.0.0-beta.1","rules_version":"v3-composition-1","build_hash":"<sha256>"}}
{"protocol":1,"request_id":"req-001","type":"progress","payload":{"completed":2,"total":10,"phase":"分类"}}
{"protocol":1,"request_id":"req-001","type":"done","payload":{"outputs":[]}}
```

`start_generation` 会立即返回 `status: started`，任务在 Worker 线程中执行；
后续 `progress`、`done`、`cancelled` 或 `error` 事件继续使用同一个
`request_id`。`shutdown` 会先请求正在运行的任务取消，等待 Worker 线程退出，
再返回 `done`，避免留下孤儿任务。

事件类型：

```text
ready
status
progress
preview_ready
done
cancelled
error
```

## 约束

- `request_id` 必须原样回传；
- 未知协议版本必须返回 `error`；
- 未知请求类型必须返回 `error`；
- 取消是幂等操作；
- Worker 不能把模型数组或完整原图通过 JSON 返回；`read_preview` 只允许返回受尺寸限制的缩略图 base64；
- 生成任务中的图片通过文件路径或缓存键引用；预览请求例外，只返回缩略图 base64；
- `shutdown` 完成后 Worker 退出；
- 协议变化必须递增 `protocol`，不能静默改变字段含义。
- `packages/protocol/schema.json` 是消息 envelope 的机器可读约束；具体 payload
  字段按本文件约定解释。

## 当前已实现

- `start_generation`：默认走 v3 Worker 内置 Renderer，`legacy` 保留兼容路径，`compare`
  只记录差异并以 legacy 正式输出；
- `cancel_generation`：通过线程安全取消事件停止任务；
- `progress` / `done` / `cancelled` / `error`：已实现；
- `shutdown`：等待任务安全结束后退出；
- `probe_models`：无模型目录时不启动推理服务，有模型时返回 Pose/Seg 状态、模型版本和
  fallback 原因；
- `retry_failed`：复用生成请求和 `source_filter`；
- `clear_cache`：清理 manifest 与 v3 模型结果缓存，不触碰用户输出目录；
- `read_preview`：跨平台读取并缩放图片预览，默认上限 198×138；要求 `allowed_roots` 作用域，
  解析后的路径越界会被拒绝；按路径元数据和目标尺寸做有界缓存，不按文件大小拦截，5000 万像素以上由预检阶段提示风险，保留 PNG 透明通道；
- `classify_materials`：已接入 v2.6 规则分类及可选 Pose/Seg 增强；进度事件按
  `规则分类`、`Pose`、`Seg` 阶段返回，最终返回 `decisions`。
- `start_generation` / `retry_failed`：已透传 Logo 覆盖、文件夹品类覆盖和卖点图参数。
