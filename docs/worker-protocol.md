# PixelFlow Worker 协议草案

协议版本：`1`

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

## 事件

```json
{"protocol":1,"request_id":"req-001","type":"ready","payload":{}}
{"protocol":1,"request_id":"req-001","type":"progress","payload":{"completed":2,"total":10,"phase":"分类"}}
{"protocol":1,"request_id":"req-001","type":"done","payload":{"outputs":[]}}
```

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

