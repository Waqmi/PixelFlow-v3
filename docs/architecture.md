# PixelFlow v3 beta 架构决策

## 运行边界

| 层 | 职责 | 不负责 |
|---|---|---|
| Vue 前端 | 页面、状态展示、主题、动画、用户输入 | 图像处理、模型加载 |
| Tauri/Rust Host | 窗口、文件选择、Worker 生命周期、系统能力 | 品类规则、图片算法 |
| Python Worker | 分类、渲染、模型推理、缓存、进度和取消 | UI 和窗口管理 |

## 模型策略

- v3 beta 不强制内置大型 Pose/Seg 模型；
- 用户可以选择外部模型目录；
- 启动时探测 Pose/Seg 能力并显示状态；
- 模型不可用时回退规则分类和人工确认；
- Worker 内只加载一次模型，后续任务复用；
- 模型加载失败不能阻止无模型模式启动。

## 缓存策略

- 用户偏好：Tauri Store 或 JSON；
- 任务、分类、输出索引和缓存版本：SQLite；
- 图片、缩略图和模型：文件系统；
- 缓存键包含素材哈希、处理参数、模型版本和协议版本；
- 清理缓存不删除用户输出和模型文件；
- 所有数据库变更必须有迁移版本。

## 平台目标

第一阶段目标平台：

- macOS Apple Silicon；
- Windows x64。

macOS Intel/Catalina 不进入 v3 beta 第一轮，待主流程稳定后单独评估。

## 通信

Tauri 与 Python Worker 使用版本化 JSON Lines。暂不引入 FastAPI，避免本地
端口、服务发现和孤儿进程问题。

Host/Vue 的最小转发契约见 [`tauri-host-contract.md`](tauri-host-contract.md)。
