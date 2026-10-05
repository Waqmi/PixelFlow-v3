# PixelFlow v3 性能优化：后续任务（交给 Codex）

日期 2026-10-05。背景与实测数据见同目录 `review-and-recommendations.md`，基准脚本在 `bench/`。

## 0. 当前状态（先读）

- 已提交到分支 `perf/render-alpha-png`（commit `0612774`，基于 main 的 `a6a6131`）：`worker/pixelflow_core/renderer.py` 的 `_background_to_alpha`（numpy 向量化）与 `_encode_png`（惰性量化 + `PNG_PROBE_MARGIN = 0.20` 的 L6 探测），以及 `worker/tests/test_renderer.py` 的 `EncodingParityTests`。输出与 a6a6131 逐字节一致，单测 195 通过 / 1 skip；护膝批次（27 输出）墙钟 32.0s → 7.5s。
- 在该分支上继续工作（`git switch perf/render-alpha-png`），不要回退这些改动。
- 工作区里 `.planning/` 下的未跟踪/已修改文件和 `:memory:.ses` 与本任务无关，不要提交它们。

## 1. 约束（必须遵守）

- 先读根目录 `CLAUDE.md` 与 `worker/CLAUDE.md`。
- 不读、不改构建产物目录：`dist*`、`build*`、`target`、`worker/.pyinstaller-config*`。
- **输出文件必须与改动前逐字节一致**。因此规则版本号（`RULES_VERSION` / `COMPOSITION_RULES_VERSION` / `expectedRulesVersion`）不改。
- 不得改变 shirt / shoes / pants 及其他品类的构图行为。
- 不 commit、不 push，除非用户明确要求。
- 测试只做必要的（见每项的“验证”），不要跑大规模语料基准。

## 2. 任务 A：同来源同尺寸的 compose 结果复用（优先）

**为什么**：`engine.py` 对每个尺寸按 `include_logo` 生成含Logo / 无Logo 两个请求（`worker/pixelflow_core/engine.py:423` 附近），二者共享同一个 `plan` 对象，而 `Renderer._compose` 不读 `include_logo`。实测两次 compose 逐字节相同。产品图单次 compose 只有 0.03–0.07s，但模特 side fill 单次 0.45–0.88s；模特批量（3 来源 × 3 尺寸 + Logo + VIP = 21 输出）里 compose 占墙钟 82%。预计每个模特来源的 compose 次数从 7 次降到 3–4 次。

**改法**（只改 `renderer.py`）：

1. 在 `Renderer.render_source_batch` 内（单来源分支，`for index, request, ... in work:` 循环前）建一个局部 dict 缓存，生命周期仅限这一次调用，结束即丢弃。不要做成实例级或全局缓存。
2. 新增 `_compose_key(request, target, transparent)`。键必须覆盖 `_compose` 读取的**全部**输入，逐行读 `_compose`（`renderer.py` 约 670–790 行）确认，目前包括：
   - `target`、`transparent`（以及 `request.transparent`）
   - `request.plan`：用对象身份 `id(request.plan)`（`plan.evidence` 是 dict，不可哈希；同一来源的含/无 Logo 请求在 engine 里是同一个 plan 对象）。`plan is None` 时用 `None`。
   - `request.source_type`、`request.background`
   - `request.layout` 中的 `category`、`enable_category_template`、`scale`、`allow_mirror_extension`
   - `request.metadata` 中的 `source_type`、`material_type`、`background`、`source_scale_adjustment`、`category`、`enable_category_template`、`scale`、`position_x`、`position_y`、`allow_mirror_extension`（取值后转成可哈希形式，如 list→tuple）
   - 若 `_compose` 还读其他字段，一并加入。宁可多加导致不命中，也不能漏加导致错误复用。
3. 在 `_render_prepared` 里，把 `self._compose(...)` 换成“先查缓存，未命中再算并存入”。命中时 `compose_seconds` 记实际耗时（接近 0）即可。
4. 确认 `_overlay` 不会就地修改传入的底图（当前 `image.convert("RGBA")` 返回新对象）。若有任何就地修改路径，对缓存命中的底图先 `.copy()`。
5. VIP 请求由键自动决定是否复用，不要为 VIP 写特殊分支。
6. 内存：每张 1440×2160 RGB 底图约 9MB，单来源最多缓存约 4–7 张，且随调用结束释放，可接受。

**验证**：

- 在 `worker/tests/test_renderer.py` 加一个小测试：构造同一 plan 的含/无 Logo 两个请求，调用 `render_source_batch`，用计数器（mock 或包装 `_compose`）断言 compose 只调用一次，且两份输出文件逐字节等于“禁用缓存时”的结果。再加一个键不同的用例（例如不同 `source_scale_adjustment`）断言不复用。图片 ≤ 300px。
- 跑全量单测：`PYTHONPATH=worker worker/.venv/bin/python -m unittest discover -s worker/tests -p 'test_*.py'`。
- 端到端一次：用 `bench/bench_batch.py` 里的 `model_request`（热能长裤 3 张模特图，21 输出）分别在改动前后各跑一次（可在脚本里 monkeypatch 掉缓存来得到“改动前”），对两份输出目录做 sha256 逐文件比对，要求 21/21 相同，并记录两次墙钟。临时输出目录用完删除。

## 3. 任务 B：报告里加真实墙钟

**为什么**：`engine.py:690` 的 `performance.total_seconds` 是各阶段累计值，多线程时会重复计算等待时间（原版 3 线程 compose 累计 42s，实际墙钟只有 32s），容易误读。

**改法**（只改 `engine.py`）：

- 在 `V3EngineAdapter.run` 中记录整次运行的墙钟，写入 `performance["wall_seconds"]`；渲染线程池那段单独记 `performance["render_wall_seconds"]`。
- **不要修改或删除 `total_seconds`**，前端和已保存的报告依赖它（`apps/desktop/src/App.vue:424`、`:469` 原样透传 `performance`）。
- 如果 `packages/protocol/schema.json` 对 `performance` 字段有约束，同步更新，并跑 `cd apps/desktop && npm run protocol:check`。

**验证**：在 `test_engine.py` 里已有的生成用例中断言 `wall_seconds` 存在且 > 0、`render_wall_seconds <= wall_seconds`。跑全量单测即可。

## 4. 任务 C（低优先级）：避免重复的 `probe_models`

**为什么**：Worker 的 `ready` 事件会广播到所有 WebView。`App.vue:1448` 在收到 `ready` 时调用 `probeModels()`，现在排除了报告窗和确认窗，但主窗口和设置窗口仍可能各发一次。每次 probe 都会对所有模型文件做完整的 SHA-256，并占用模型锁。

**改法**（只改 `apps/desktop/src/App.vue`）：

- 先确认实际会同时存在哪些窗口（看 `isReportWindow` / `isConfirmWindow` 的定义，以及设置窗口如何打开）。
- 只让主窗口在 `ready` 时自动 probe；手动“测试模型”按钮（`App.vue:2129`）和模型路径变化触发的 probe（`App.vue:826`）保持不变。
- 在 `probeModels()` 里加在途保护：若已有未完成的 `modelProbeRequestId`，则不再重复发送。

**验证**：`cd apps/desktop && npm run build`。不需要启动 App；如果顺手启动了，就在 Worker 日志里确认冷启动时只出现一次 `probe_models`。

## 5. 不要做

- 不要把渲染并发 `render_workers = min(3, len(sources))`（`engine.py:322`）改成核数，等 A 完成后再单独测量 3/4 线程与峰值内存。
- 不要放开模型锁或并行跑 ONNX。
- 不要降低 JPEG 质量，也不要去掉 VIP 的中间 JPEG。JPEG 实测不是瓶颈，所有尺寸在 q95 下一次就达标。
- 不要缓存透明抠图结果。numpy 版单次只要 0.125s，缓存省不了多少。

## 6. 交付

- `git diff --stat` 与关键 diff。
- 单测结果（数量 / 通过 / skip）。
- 任务 A 的 21/21 sha256 比对结果与前后墙钟。
- 每项注明是否完成；未完成的写原因。
