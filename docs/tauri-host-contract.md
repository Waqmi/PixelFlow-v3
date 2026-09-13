# Tauri Host 与 Vue Worker Bridge 契约

前端通过 `apps/desktop/src/tauriBridge.ts` 使用两条 Tauri 通道连接 Python
Worker。Host 不解释业务 payload，只负责进程生命周期和 JSONL 转发。

## Command

`worker_send(line: string) -> Result<(), string>`

- 将一整行 JSON 原样写入 Python Worker stdin，并补一个换行（前端传入的
  字符串不应重复追加换行）；
- Worker 未启动、stdin 已关闭或写入失败时返回错误；
- 不在 Rust 层缓存、改写或合并请求。

## Event

`worker-line: string`

- Python Worker stdout 每读到一行就发送一次事件；
- 保留 UTF-8 内容，不加日志前缀；
- stderr 只写 Host/Worker 诊断日志，不转发到该事件。

## 生命周期

- Host 启动时创建一个 Python Worker 进程；
- Worker 退出或 stdout 关闭后，Host 继续运行但 bridge 标记为断开；
- Tauri 窗口退出前发送 `shutdown`，等待 Worker 完成后再释放进程；
- Worker 可执行文件、工作目录和 Python 运行时路径由 Host 配置决定，Vue 不感知。
