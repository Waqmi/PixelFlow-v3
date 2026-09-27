# Tauri Host 与 Vue Worker Bridge 契约

前端通过 `apps/desktop/src/tauriBridge.ts` 使用两条 Tauri 通道连接 Python
Worker。Host 不解释业务 payload，只负责进程生命周期和 JSONL 转发。

## Command

`worker_send(line: string) -> Result<(), string>`

- 将一整行 JSON 原样写入 Python Worker stdin，并补一个换行（前端传入的
  字符串不应重复追加换行）；
- Worker 未启动、stdin 已关闭或写入失败时返回错误；
- 不在 Rust 层缓存、改写或合并请求。
- 预览请求必须携带 `payload.allowed_roots`；Host 只接受当前任务或用户目录选择时登记的
  canonical roots，并在转发前校验路径边界，Worker 再次解析并限制在这些目录内。Host 不提供读取任意文件的命令。

## Event

`worker-line: string`

- Python Worker stdout 每读到一行就发送一次事件；
- 保留 UTF-8 内容，不加日志前缀；
- stderr 只写 Host/Worker 诊断日志，不转发到该事件。

`worker-exit: { message, task_id?, retryable }`

- Worker 意外退出时携带当前任务 ID（若有），用于页面标记中断任务；
- Host 在 60 秒内最多自动重启 3 次，重启间隔为 250 ms、1 s、2 s；超过上限时
  `retryable` 为 `false`，页面提示查看日志。
- Worker stderr 写入平台日志目录的 `worker.log`，超过 5 MiB 后滚动为
  `worker.log.1`。

## 生命周期

- Host 启动时创建一个 Python Worker 进程；
- Worker 退出或 stdout 关闭后，Host 发出 `worker-exit`，按上限和退避策略重新启动 Worker；中断的任务不会静默重试，页面会提示用户重新开始；
- Tauri 窗口退出前发送 `shutdown`，等待 Worker 完成后再释放进程；
- Worker 可执行文件、工作目录和 Python 运行时路径由 Host 配置决定，Vue 不感知。

## 版本握手

Worker 启动和 `ping` 都发送 `ready`，payload 保留原有字段并增加：

```json
{
  "worker_version": "3.0.0-beta.1",
  "rules_version": "v3-composition-1",
  "build_hash": "<sha256>"
}
```

前端对 `worker_version` 和 `rules_version` 做兼容性检查；`build_hash` 用于诊断，
不参与协议版本递增。
