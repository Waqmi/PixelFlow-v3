# Task Plan: PixelFlow v3 T2–T11 sequential maintenance

## Goal
按既定计划依次完成 T2 到 T11；每阶段只在有真实实现和可复核证据时标记完成，受环境或真实批次约束的阶段保留明确延期记录。

## Next Step
T11 周期跟踪：等待至少 14 个自然日或 2 个真实批次；完成条件前不删除 `legacy_engine`。

## Current Phase
T11（周期 active，T2–T10 已完成当前环境可执行部分）

## Phases

### T2 `.app` 完整回归
- [ ] 分类、手动修改、生成、打开输出目录、增量复用
- [ ] 安全取消和进程残留检查
- [ ] 20 张鞋类素材 Pose/Seg、复核提示、模型缺失回退
- **Status:** partial — live UI evidence complete; JOB-06 count discrepancy and missing shoe fixture remain

### T3 legacy/v3 基线
- [x] 双报告、逐像素指标、差异分类和 contact sheet
- **Status:** complete（已有 52 项 needs_fix）

### T4 预览与安全
- [x] 预览缓存、canonical scope、CSP、300 张性能证据
- **Status:** partial（Worker/性能完成，桌面首屏 UI 待补）

### T5 Tauri Host
- [x] 重启上限、日志滚动、任务 ID、ready 版本、平台行为
- [ ] 故障注入不无限重启/不留孤儿
- **Status:** partial — supervisor verified; platform fault injection and dialog plugin pending

### T6 功能对齐
- [x] 输入输出校验、主体比例、取消/失败报告、设置项和状态的可自动验证项
- [x] parity 清单已更新，未完成 UI 项保留明确延期原因
- **Status:** partial（完整桌面人工审计、主题/动态/Logo 联调仍待宿主操作）

### T7 onedir 与独立 Python
- [x] onedir 资源、哈希清单、Worker 测试和启动/双跑/退出证据
- [ ] Python 3.12 重建
- **Status:** partial（当前构建使用 Python 3.9.13；本机无 Python 3.12 和 `worker/.venv`）

### T8 复核与回退可见性
- [ ] review_hint、保护框、接受/保守重跑、统计
- **Status:** complete（已有 force_rerender 修复）

### T9 前端与 CI
- [x] 进度状态抽出 composable、schema 校验和 CI 配置
- [ ] 完整 views 拆分、Vitest
- **Status:** partial（Vitest 依赖无法在当前无网络环境安装）

### T10 发布
- [ ] macOS DMG/签名/公证/首次启动
- [ ] Windows x64 安装和中文/长路径验证
- **Status:** blocked by platform credentials/runners; 未签名 arm64 `.app` 仅完成启动/Worker 证据

### T11 legacy 周期
- [ ] beta.2 至少 14 天或 2 个真实批次
- [ ] fallback=0、T3 needs_fix 清零、T8 可追溯
- **Status:** active（真实批次 0，natural fallback 未知，T3 needs_fix=52）

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 继续保留 legacy_engine | T3 仍有 52 项 needs_fix，T11 周期尚未满足删除条件 |
| 不伪造 T2/T10/T11 证据 | UI、签名、公证、Windows 和真实批次需要真实环境或用户运行 |
| 当前正式 onefile hash 以 027a624b… 为准 | 用户计划明确要求；6c5fad… 仅保留为历史，当前 onedir app 为 627ebb… |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| planning directory not found | Initialized selected plan with init-session.sh |


## Verification note
- T9 progress composable extraction is implemented and build-verified; Vitest remains explicitly deferred because the current environment has no installable npm dependency path.
