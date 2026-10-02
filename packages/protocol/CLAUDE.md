[根目录](../../CLAUDE.md) > packages > **protocol**

# packages/protocol

## 变更记录

- 2026-10-02：初始化文档。

## 职责

定义宿主（Vue / Tauri Host）与 Python Worker 之间 JSONL 消息的信封 JSON Schema。每行一个 JSON 对象。协议版本固定为 `1`。

## 文件

| 文件 | 说明 |
|---|---|
| `schema.json` | 唯一文件。JSON Schema 2020-12，标题 `PixelFlow Worker Message` |

## 信封结构

必填 `protocol`（integer，const 1）、`request_id`（非空 string）、`type`（enum）、`payload`（object）；`additionalProperties: false`。

`type` 枚举（共 16 个）：
- 请求：`ping`、`probe_models`、`classify_materials`、`start_generation`、`cancel_generation`、`retry_failed`、`clear_cache`、`read_preview`、`shutdown`
- 事件：`ready`、`status`、`progress`、`preview_ready`、`done`、`cancelled`、`error`

`payload` 的具体字段不在 schema 里约束，由两端各自定义：
- TS 端：`apps/desktop/src/workerProtocol.ts`（`WORKER_REQUEST_TYPES`、`WORKER_EVENT_TYPES`、`GenerationPayload`、`ClassificationPayload`、`parseWorkerMessage`、`createRequest`）
- Python 端：`worker/pixelflow_core/contracts.py`（`Message`、`ProtocolError`）、`worker/pixelflow_core/worker.py`（`SUPPORTED_REQUESTS`）
- 文档：`docs/worker-protocol.md`

## 一致性检查与注意事项

- `python3 tools/check_protocol_schema.py`（即 `npm run protocol:check`，CI 的 desktop job 会跑）校验 schema 与实现一致。
- 新增/删除消息类型必须同时改：`schema.json`、`workerProtocol.ts` 两个数组与类型联合、`worker.py` 的 `SUPPORTED_REQUESTS` 与 `main()` 分派、`docs/worker-protocol.md`。
- Rust Host（`apps/desktop/src-tauri/src/main.rs`）转发 JSONL，并对 `read_preview` 做路径范围校验，改协议时一并确认。
