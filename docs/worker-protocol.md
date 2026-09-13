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
shutdown
```

请求 payload 约定：

- `classify_materials`：`product_root` 必填；可选 `source_filter`、`overrides`、`workers`；
- `start_generation` / `retry_failed`：使用 `GenerationRequest` 对应字段，至少包含
  `product_root`、`output_root`、`selected_sizes`；可选 `max_size_mb` 控制单文件大小、
  `logo_overrides` 替换 Logo 资源、`folder_category_overrides` 覆盖文件夹品类、
  `include_selling_point_images` 与 `selling_points` 生成卖点图；
- `cancel_generation`：payload 可为空；取消当前唯一运行中的任务；
- `probe_models`：可选 `model_root`；
- `clear_cache`、`ping`、`shutdown`：payload 为空对象。

## 事件

```json
{"protocol":1,"request_id":"req-001","type":"ready","payload":{}}
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
- Worker 不能把模型数组或完整原图通过 JSON 返回；
- 图片通过文件路径或缓存键引用；
- `shutdown` 完成后 Worker 退出；
- 协议变化必须递增 `protocol`，不能静默改变字段含义。
- `packages/protocol/schema.json` 是消息 envelope 的机器可读约束；具体 payload
  字段按本文件约定解释。

## 当前已实现

- `start_generation`：已接入 v2.6 Engine Adapter；
- `cancel_generation`：通过线程安全取消事件停止任务；
- `progress` / `done` / `cancelled` / `error`：已实现；
- `shutdown`：等待任务安全结束后退出；
- `probe_models`：无模型目录时不启动推理服务，有模型时复用 v2.6 探测服务；
- `retry_failed`：复用生成请求和 `source_filter`；
- `clear_cache`：清理 manifest 与模型结果缓存；
- `classify_materials`：已接入 v2.6 规则分类；请求体使用 `product_root`、可选
  `source_filter`、`overrides` 和 `workers`，异步返回分类进度及最终 `decisions`。
  Pose/Seg 增强和人工确认页仍待迁移。
- `start_generation` / `retry_failed`：已透传 Logo 覆盖、文件夹品类覆盖和卖点图参数。
