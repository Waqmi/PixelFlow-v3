# Findings & Decisions

## Requirements
- Repair duplicate VIP outputs and generation progress rollback in the ARM v3 main repository.
- Keep generation progress monotonic through render/legacy fallback; only mark 100% on the worker's final `done` event.
- Deduplicate exact output pixels even when filenames differ, while preserving same-name images with distinct pixels.

## Research Findings
- The diagnostic archive has no input/output roots or result details (`last-result.json` is `{}` and `worker.log` is empty), so it does not identify the generating code path by itself.
- The supplied output tree contains byte-identical files in `唯品专享1440/无Logo`, including suffixed names. This is consistent with the previous VIP routine recursively scanning the output tree and flattening category/aggregate copies into one directory.
- VIP generation receives current-batch rendered source paths. Its outputs are included in the manifest, and a changed VIP version invalidates reuse; a successful full run then removes obsolete manifest-owned outputs through stale cleanup.
- Ordinary generation progress previously mapped each event directly to `completed / total * 1000`; varying phase totals could lower the displayed target. The front-end interpolation helper also accepted lower targets.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Restrict VIP inputs to current-batch outputs and deduplicate by decoded pixel hash | Prevent recursive collection of stale and aggregate outputs, and catch exact duplicates even when source filenames differ. |
| Keep progress monotonic and reserve 100% for `done` | Prevent stage/fallback changes from rolling back or reporting completion before final validation. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Luna worker could not start because the local agent proxy does not support `gpt-6-luna` | Implementation was delegated to the available general worker; root retained review responsibility. |

## Resources
-
# 发现

- ARM 主仓为 `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta`，基线 `main @ ee265aa`，只存在未跟踪 `:memory:.ses`；修改应在此仓库进行。
- 诊断包 `diagnostics.json` 显示 app/worker `3.0.0`，规则 `v3-composition-11`，worker 哈希 `e0b9c87f31c983bbc4642eb7896b74a05f565922e8ad521e4eb8750c50c3fbc2`，Pose/Seg/Detection 均 ready；但 input/output root 为 null、`last-result.json` `{}`、worker.log 空，不能定位重复输出。
- 进度 UI 在 `apps/desktop/src/App.vue` 的 generation progress 分支直接调用 `setProgress(completed / total * 1000)`；渲染工作进度由 `worker/pixelflow_core/engine.py` V3 adapter 回调，按 reused 和成功渲染输出累计。若同一任务不同阶段/适配器重置 completed 或变化 total，UI 目标值可下降；前端插值器目前也接受较低目标。
- 用户验收期望是整个任务进度单调递增，包括阶段变化/回退到 legacy 时不回滚。
- 用户补充了实际测试目录：源 `/Users/weiheng/Desktop/pure2 极寒`，输出 `/Users/weiheng/Desktop/pure2 极寒主图`。只读 SHA-256 检查确认 `唯品专享1440/无Logo` 存在大量 `_2.jpg` 完全重复对，例如 `素材_2_04.jpg` / `素材_2_04_2.jpg`（同 SHA-256 前缀 `05a6a0b6889a7465`），以及 `女_白绿_18.jpg` / `女_白绿_18_2.jpg`。强线索是唯品图从不同分类子目录收集同名基础图，再因平面目标名已存在而自动 `_2` 保存。
- 输出的 `产品主图/素材/<尺寸>/<Logo状态>/` 还存在与按颜色分类目录完全相同 SHA-256 的图像副本；需 worker 核实这是预期汇总目录还是重复产物，不应误删合法分类输出。
