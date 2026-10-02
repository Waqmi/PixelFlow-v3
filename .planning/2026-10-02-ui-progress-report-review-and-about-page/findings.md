# Findings & Decisions

## Requirements
- 统一素材识别与生成阶段的平滑进度动画。
- 将放弃出图说明集中到复核与回退二级页。
- 复核页字号按报告页标题与正文风格统一。
- 精简并更新设置页关于内容，保留诊断与发布信息。

## Research Findings
-

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 复用 `useTaskProgress` 的 RAF 插值通道处理分类进度 | 保持两阶段动画参数一致，避免新增动画逻辑 |
| 复核页继续使用现有 skipped output 数据 | 只移动展示位置，不改变结果数据或操作语义 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
-
