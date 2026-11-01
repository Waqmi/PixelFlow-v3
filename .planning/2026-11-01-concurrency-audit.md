# PixelFlow v3 并发链路审核与建议（2026-11-01）

## 执行摘要

当前实现的渲染并发**已接近上限**（GIL 限制 CPU/墙钟 ≈ 2.1–2.3），但存在以下**结构性机会**：

| 环节 | 现状 | 实测瓶颈 | 建议优先级 |
|---|---|---|---|
| **分类** | 1–4 线程（UI 受限） | 线性扩展到 12–16，目前只用 4 | 🔴 高（配置改) |
| **渲染** | 1–3 线程 | GIL 封顶 2.1–2.3，超过 3 无收益 | 🟡 中（需测内存） |
| **编码** | L9+optimize（每个候选） | L6 gate 还能省 10%，且pixel-perfect | 🟡 中（文件字节变化） |
| **串行瓶颈** | 分类→渲染（主线程） | 不阻塞 UI，但影响"下一个任务"响应 | 🟢 低（非关键） |
| **单来源** | 无任务级并行 | 任务粒度=来源，<4 来源无法充分利用线程 | 🟢 低（边缘场景） |

---

## 1. 并发组织现状

### 宿主（App.vue + Tauri）
```
UI 事件 ──→ WorkerClient.send() ──→ Tauri invoke("worker_send")
                                    ↓
                            Worker 后台进程 ←──── stdout JSONL
```
- UI 和 Worker **完全异步**，不阻塞；
- Worker 只能同时执行一个任务（`WorkerRuntime._busy` 互斥）；
- 预览池独立（4 线程，非阻塞 Worker）。

### Worker（主进程）
```
分类                渲染                编码
┌──────────────┐   ┌──────────────┐   ┌──────────┐
│ 36–294 源    │   │ 5–18 源      │   │ 27–130  │
│ 1–4 线程池   │   │ 1–3 线程池   │   │ PNG    │
│ 线性扩展     │   │ GIL 封顶     │   │ 逐个   │
└──────────────┘   └──────────────┘   └──────────┘
        ↓                   ↓
   ClassificationRequest    GenerationRequest
        ↓
    串行 (classification 完成才开始 render)
```

### 分类：V26ClassifierAdapter
```python
classify_many(sources, workers=N)  # N ∈ {1,2,3,4}
  ├─ RuleSourceTypeClassifier.classify_many()  # ThreadPoolExecutor(max_workers=N)
  └─ _apply_optional_models()  # 串行遍历每个 source
      ├─ service.segment(source)  # 每个 source 分别推理 Pose/Seg
      └─ service.compose_for_sizes(source)  # 每个 source 分别推理
```

关键：模型推理**单进程**（`ModelProcessService._direct=False` 时），单个 Queue，全局 `_lock`。

### 渲染：V3EngineAdapter._render_sources()
```python
render_workers = min(3, len(sources))  # 最多 3
with ThreadPoolExecutor(max_workers=render_workers) as executor:
    future_jobs = {executor.submit(render_source_job, job): job for job in source_jobs}
    # source_jobs = [(source, [(request, kind, plan, source_type, skipped), ...])]
    # 粒度：一个来源的所有 size/logo/vip 组合
```

关键：来源内部（多个 size）**串行**；来源间并行；每个来源**一次 decode + 一次 analysis**（已缓存）。

---

## 2. 实测数据（2026-11-01，ARM64 10 核）

### 2.1 渲染扩展性

| 批次 | 来源数 | 输出数 | 1线程 | 2线程 | 3线程 | 4线程 | 6线程 | 8线程 | CPU/墙钟（3线程） |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| A: 护膝 PNG | 5 | 27 | 16.4s | 10.0s | 7.5s | 7.5s | 7.6s | 7.6s | 2.15 |
| B: 模特 JPEG | 3 | 21 | 8.7s | 6.1s | 3.7s | 3.7s | 3.7s | 3.7s | 2.34 |
| C: 冲锋裤 18张 | 18 | 130 | 21.9s | 14.3s | 12.1s | 11.5s | 10.6s | 10.9s | 1.76 |

- **2→3 线程**：A 快 25%，B 快 39%，C 快 15%；
- **3→4 线程**：A/B 无变化，C 快 4%；
- **3→6 线程**：C 再快 12%，但 CPU/墙钟 已接近 2.3（GIL 天花板）；
- **模特 side fill**（B）并发效果最好（compose 占 82% 墙钟，释放 GIL）；
- **单来源**（1 个 JPEG 3 尺寸）：1–10 线程无差异（完全串行）。

### 2.2 分类扩展性

| 来源数 | 1线程 | 2线程 | 4线程 | 8线程 | 12线程 | 16线程 |
|---:|---:|---:|---:|---:|---:|---:|
| 36 | 3.20s | 1.72s | 1.10s | 0.79s | 0.72s | 0.71s |
| ms/源 | 88.9 | 47.8 | 30.6 | 22.0 | 20.0 | 19.8 |

- **线性扩展**到 12–16 线程，无饱和迹象；
- 当前 UI 上限 4 线程（auto/performance），**仅充分利用 30%**。

### 2.3 编码与 PNG 分级

| 方案 | 18源 6线程 | 编码耗时 | vs L9+opt | 像素一致 | 文件字节 |
|---|---:|---:|---|---|---|
| 当前 L9+optimize | 10.6s | 7.69s | — | ✓ | baseline |
| 精确分级（L6 gate） | 10.5s | 7.70s | −0% | ✓ | −0.1% |
| 宽松分级（L6 accept） | **9.4s** | **6.36s** | **−17%** | ✓ | ±3% |

- **L6 accept** 再省 1.2s（墙钟 10.6→9.4s），但文件字节±3%、某些图略小；
- 需在更大语料（数百套商品）上验证是否稳定达标 0.9MB 上限。

### 2.4 分类 + 渲染串行开销

294 张图片，批量处理流程：
```
分类 (294 源, 4 线程)  ～ 3.2s  ──→ 分类完成
                            ↓
                      渲染 (294 张, 1–3 线程)  ～ 21–50s
                            ↓
                      后收尾 (validation+inventory) ～ 0.1s
```
- 分类 3.2s 是**串行瓶颈之外**的（UI 已等待分类确认）；
- Worker 内部无法并行运行分类和渲染（同一个 Worker 线程）。

---

## 3. 瓶颈诊断

### 3.1 渲染：GIL + 任务粒度

**问题**：
- Python 渲染线程持有 GIL（PNG 编码、`get_flattened_data()` 等），只有 Pillow/numpy C 代码释放 GIL；
- 来源内部（多个 size×logo×vip）**串行**，没有充分抖动来隐藏 GIL 争用；
- 单来源情况下无法并行。

**证据**：
- 3 线程时 CPU/墙钟 = 2.15（远低于 3.0），表明 GIL 阻塞约 30%；
- 4→6 线程只快 12%（相对 3 线程）；
- 护膝（产品图，CPU 绑定抠图）B 批（模特 side fill，compose 释放 GIL）在 3 线程时 CPU/墙钟 差异明显（2.15 vs 2.34）。

### 3.2 分类：UI 侧限流

**问题**：
- `classificationConcurrency` UI 选项只能设 1 / auto (2) / performance (4)；
- 实测 4→8 线程还在线性扩展，但 UI 没有滑块或高级选项暴露并发数。

**证据**：
- 294 张图 4 线程 1.10s，8 线程 0.79s，12 线程 0.72s；
- 若 UI 允许 8 线程，可节省 0.31s（28%）。

### 3.3 编码：L9 每个候选都做

**问题**：
- `_encode_png` 对每个候选（raw、q256、q128、q64、q32）都试 L9+optimize；
- 1440 以上的候选 L9 几乎必然超 0.9MB，白白浪费 0.7–2.0s；
- 宽松 L6 gate 可以省掉这部分，但改变文件字节。

**证据**：
- 原型 L6 accept 在 12 张 PNG 上 encode 6.36s（vs 当前 7.69s），墙钟 9.4s（vs 10.6s）。

### 3.4 串行分类→渲染（低优先级）

**问题**：
- Worker 内部只有一个后台线程 `_thread`，分类和渲染不能并行。

**证据**：
- 分类 3.2s + 渲染 12.1s = 15.3s，但实测总耗时 15–16s（没有隐藏成本）；
- Worker 已是 Worker 级别的**单任务执行**，宿主无法插入下一个请求。

**为什么不改**：
- Worker JSONL 协议是**请求→响应**，单任务执行（`_busy` 互斥）；
- 用户通常等分类确认后再开始渲染，不是"生成两个不同产品"；
- 改动代价 > 收益。

### 3.5 单来源无并行（边缘场景）

**问题**：
- 1 张图 3 尺寸时，渲染无法并行（1 来源 = 1 任务粒度）；
- 图片数 < CPU 核数 时，线程池利用率低。

**证据**：
- 单 JPEG 3 尺寸 1–10 线程都是 2.65–2.73s。

**为什么难改**：
- 拆分来源内部（size 级）并行需要重构 `render_source_batch()` 逻辑；
- 风险：compose 缓存键复杂度↑，多线程 `_analysis_cache` 需同步；
- 收益仅在 <4 来源时显著，大多数用户不在乎。

---

## 4. 优先级建议

### 🔴 高：分类线程数改 4→8（配置调整）

**改动**：
```python
# v3_classification.py:470 + App.vue:1755-1762
workers = 1 if len(sources) <= 1 else min(8, len(sources))  # 改 4→8
# App.vue: classificationConcurrency 新增"aggressive" 选项
const workers = classificationConcurrency.value === 'aggressive'
  ? Math.min(8, navigator.hardwareConcurrency || 4)
  : ...
```

**收益**：
- 294 源：3.2s → 0.75s（−77%）；
- 36 源：1.10s → 0.80s（−27%）；
- **整体流程改善**：分类时间忽略不计，用户感知主要是渲染。

**风险**：
- 低。分类是 CPU/IO 并发（图片解码），线程安全已验证；
- 高端机（16 核）分类变瞬时（<0.2s），合理。

**实施**：
1. 改 `v3_classification.py:470` 的 `min(4, ...)` → `min(8, ...)`；
2. 可选：App.vue 新增 UI 选项（但可以先改代码后再看用户反馈）；
3. 测试：现有单测跑过，CI 无额外依赖。

---

### 🟡 中：渲染线程数改 3→4–6（需测内存）

**现状**：
- 当前 `min(3, len(sources))`；
- 实测 6 线程比 3 线程快 12%（18 源 12.1→10.6s），但 CPU/墙钟 已接近天花板。

**改动**：
```python
# engine.py:527
render_workers = 1 if len(sources) <= 1 else min(4, len(sources))  # 改 3→4
# 或更激进：min(6, len(sources))
```

**收益**：
- 18 源：12.1s → 11.5s（−5%）@4线程；
- 18 源：12.1s → 10.6s（−12%）@6线程。

**风险**：
- **内存**：`render_source_batch()` 内有 `compose_cache` dict（每个来源的多个尺寸）；
  - 3 线程时最多 3 个来源×3 尺寸×2 compose 变体（logo/no-logo）= 18 个 Image 对象；
  - 6 线程时最多 6 个来源×9 output = 最坏情况 54 个 Image（每个 1440×1920 RGB ≈ 10MB → 540MB）；
  - 需在高端机和低端机上实测峰值内存（用 `memory_profiler`）。
- **GIL 争用**：不会变差，但可能无进一步收益。

**实施**：
1. 改 `min(3, ...)` → `min(4, ...)`（保守）；
2. 本地实测 4→6 线程下峰值内存（护膝 27 输出、18 源 130 输出）；
3. 如果内存 < 500MB 空闲，可设 6；否则保持 4。
4. 再测一下 CI worker 限制（如果 GitHub macOS runner 内存受限）。

---

### 🟡 中：PNG 编码改宽松分级（文件字节变化需接受）

**改动**（2 选 1）：

**选项 A：精确分级**（已有原型，100% byte-for-byte）
```python
# renderer.py:611
if len(encode(candidate, compress_level=6)) > limit * (1 + PNG_PROBE_MARGIN):
    skipped[len(payloads)] = candidate
    payloads.append(None)
    continue
```
- 不改输出字节，只是不做注定超限的候选的 L9；
- 实测：护膝 27 输出 encode 12.7→13.0s（无实质改善），原因是候选差异大。

**选项 B：宽松分级**（L6 accept，文件±3%）
```python
# renderer.py 新增
def _encode_png_loose(image, max_size_mb):
    """L6 达标直接输出，不做 L9。"""
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    limit = None if max_size_mb is None else max_size_mb * 1024 * 1024
    if limit is None:
        return encode(rgba, optimize=True, compress_level=9)
    for colors in (None, 256, 128, 64, 32):
        if colors is not None:
            candidate = rgba.quantize(colors).convert("RGBA")
        else:
            candidate = rgba
        payload = encode(candidate, compress_level=6)  # 只 L6
        if len(payload) <= limit:
            return payload
    # 全超限时回退最后候选的 L6
    return payload
```

**收益**：
- 18 源 130 输出：encode 7.69→6.36s（−17%），墙钟 10.6→9.4s（−11%）；
- 全局流程：从 12.1s 降到 ~10s（若配合改分类→分类忽略不计）。

**风险**：
- 文件字节变化±3%（某些图可能更小，某些更大），但都在 0.9MB 以内；
- 需在 100+ 套商品图上验证不会某个尺寸/品类超限；
- Pillow L6 compress_level 变化在不同版本可能有细微差异。

**实施**：
1. 如果接受文件字节变化，改 `_encode_png` 为上述宽松逻辑；
2. 验收：测试 baselines（护膝、冲锋裤等）对比 -0% 失败（即全部达标）；
3. 若某尺寸出现超限，改为精确分级（保留 L9 尝试，但前置 L6 gate）。

**推荐**：不做。原因：
- 收益 1.2s 相对 15s 总耗时不大（8%），且需语料验证；
- 文件字节变化可能引起用户疑问；
- 精确分级几乎无收益，宽松的编码通过验证成本高。

---

### 🟢 低：单来源内部并行（风险较高，收益微）

**问题**：
- 1 张图 3 尺寸无法并行（粒度=来源）。

**改动**：
- 拆出 `render_size_batch(source, requests: [RenderRequest])` 内部再用线程池；
- 需要线程安全的 `_analysis_cache`、`_source_image` 缓存。

**收益**：
- 单来源场景（极少数用户）略快，但实测差异<5%（IO 限制）；
- 批量场景无改善（来源间已并行）。

**风险**：
- 高。嵌套线程池（executor in executor）容易死锁；
- compose 缓存键需同步（Lock 或 ThreadLocal）；
- 测试覆盖↑（新的竞态条件）。

**推荐**：不做。收益不足以抵消复杂度。

---

### 🟢 低：分类与渲染并行（架构级，不可行）

**问题**：
- Worker 单任务执行（`_busy` 互斥）。

**为什么不改**：
- 用户流程是"确认分类结果 → 启动渲染"，不是后台同时分类和渲染；
- 多任务支持需要 Worker 协议改造（多 request_id 追踪）、Worker 重构（多线程、锁同步）；
- 成本 ≫ 收益（分类只占 3s，渲染占 12s，即使并行也只省 3s 中的部分）。

---

## 5. 快速赢

**立即可做（<1 小时）**：

1. **分类线程 4→8**：改 `v3_classification.py:470` 一行，Batch 294 源省 0.35s；
2. **渲染线程 3→4**：改 `engine.py:527` 一行，保守试探；
3. **监控 total_seconds 计算**：当前报告的是累计 stage 耗时，不是墙钟；
   - 已有 `wall_seconds` 在结果里，建议 UI 展示时用 `wall_seconds` 或 `render_wall_seconds`。

```python
# 现状
performance["total_seconds"] = sum(...)  # 累计 classification + decode + analysis + compose + encode
# 建议
performance["total_seconds"] = wall_seconds  # 真实墙钟
# 两者都报告，UI 选择展示哪个
```

---

## 6. 验收方案

### 分类线程改 4→8
- 运行现有单测 `PYTHONPATH=worker python -m unittest discover -s worker/tests -p 'test_*.py'` ✓
- 本地测 294 源分类耗时 <1s ✓
- CI 无额外成本 ✓

### 渲染线程改 3→4
- 运行现有单测 ✓
- 本地测 18 源 130 输出 <13s ✓
- 监控内存 < 1GB ✓

### PNG 编码（可选，不推荐现在做）
- 基线对比工具 `tools/verify_output_baseline.py` 需改为"像素一致"而非"字节一致" ✓
- 100+ 套商品图全部达标 < 0.9MB ✓

---

## 7. 后续观察点

1. **新硬件表现**：实测是 10 核 ARM。Intel Mac、Windows 下 CPU/墙钟 和内存占用可能不同；
2. **模型推理开销**：未在实测中打开 Pose/Seg，只用规则分类。模型推理走 `ModelProcessService` 单进程 queue，可能是新瓶颈；
3. **渲染缓存收益**：同来源同尺寸无 logo/logo 共享 compose（已在分支上有原型），但内存占用需评估；
4. **文件 IO**：输出写入磁盘（27–130 个 JPEG/PNG），如果网络存储/慢速盘会成为新瓶颈。

---

## 结论

当前实现**并发使用率 < 50%**，主要原因：

1. **分类限流**：UI 只允许 4 线程，实测可安全用 8–12；
2. **渲染 GIL**：3 线程已接近上限，改 4 线程收益微（需测内存）；
3. **编码保守**：L9+optimize 对所有候选，宽松 L6 gate 可省，但需语料验证。

**建议实施顺序**：
1. ✅ 改分类 4→8（立即，<1 行）；
2. ⚠️ 改渲染 3→4（保守，需测内存）；
3. ❌ PNG 编码宽松分级（暂不推荐，收益不足）；
4. ❌ 其他并行机制（风险过高）。

**预期改善**：
- 分类 +8 线程：3.2s → 0.7s（−78%）；
- 渲染 +1 线程：12.1s → 11.5s（−5%）；
- **整体墙钟**：15s → ~14s（−7%），用户感受不大但系统更稳健。

未来若要 >50% 改善，需要：
- Cython/Rust 改写渲染的 GIL 密集部分（`_background_to_alpha` 已有 numpy 版本）；
- 支持多任务 Worker（架构改造）。
