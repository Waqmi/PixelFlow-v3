# Host / Worker 故障契约检查

当前 Host 已实现以下契约：60 秒内最多自动重启 3 次并按 250 ms、1 s、2 s 退避；
任务 ID 在异常退出事件中保留；退出时发送 `shutdown` 并等待 10 秒；非零退出和无输出
会触发带 `retryable` 的退出事件；Windows 子进程设置 `CREATE_NO_WINDOW`，避免弹出控制台。

本机最小检查：

```bash
python3 tools/check_host_fault_contract.py
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
```

`cargo test` 覆盖重启预算、shutdown 状态下不重启、任务 ID 保留和诊断归档。静态检查
覆盖超时、非零退出路径和 Windows 无控制台标志。当前开发环境为 macOS；真实 Windows
进程故障注入未执行，需在 `windows-2022` CI/机器上终止 Worker、制造非零退出并核对
`worker-exit` 的 `task_id`、`retryable` 和重启预算。

故障注入和 smoke 不计入 `docs/legacy-cycle-beta.2.json` 的真实批次；真实批次必须用
`tools/record_legacy_batch.py --real-batch` 单独登记。
