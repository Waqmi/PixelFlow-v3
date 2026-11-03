# Windows 与 ARM v3 同步计划

## 目标
以 ARM v3 当前修复为基线，核对 Windows 分支的源码与打包流程；同步可共享修复，保留 Windows 专项修复，并验证可执行的检查。

## 阶段
1. **基线识别**：确认 ARM 工作区改动、Windows 分支位置和提交关系。完成。
2. **差异审计**：区分共享业务改动、Windows 专项修复和构建配置，确定最小同步集。完成。
3. **同步与验证**：在独立 Windows 同步分支应用 ARM 修复，执行可用回归。完成。
4. **交付**：说明已同步内容、未能在 macOS 验证的 Windows 项目及生成新包的步骤。完成；远端 Windows 构建仍待触发。

## 约束
- 不覆盖 ARM 主工作区已有改动。
- 保留 Windows 分支上的平台专项修复。
- 当前主机为 macOS，不能把静态检查表述为 Windows 实机验证。

## 错误
| 问题 | 处理 |
|---|---|
| Windows 打包 worktree 路径已失效 | 直接通过 Git 引用审计现有分支，不重建或覆盖该 worktree。 |
| patch 在 main.rs、App.vue、test_engine.py 有冲突 | 在隔离同步 worktree 手工合并，保留两侧逻辑后完成回归。 |
| 同步 worktree 缺 Python/Node/Tauri Worker 依赖 | 复用主工作区依赖路径或创建资源占位目录；测试通过后移除 Node 临时链接。 |
| Windows V3 目录名回归预期不一致 | 在同步分支将输入根目录直接素材的组名固定为“素材”，回归通过。 |
