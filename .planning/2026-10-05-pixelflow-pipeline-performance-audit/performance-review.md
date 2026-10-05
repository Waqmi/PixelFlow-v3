# PixelFlow v3 全链路性能审核（2026-10-05）

代码基线：a6a6131。三个 luna worker 分别检查 UI/Rust、Python Worker 和现有性能记录，主代理复核关键构图与编码代码。本轮未实现优化，未提交或推送。

## 结论

优先减少重复构图、重复 PNG 预处理及不必要的模型探测，再决定是否提高并发。现有样本显示生成阶段的累计耗时集中在构图与编码；没有证据支持“线程越多越快”。启动、确认前模型分析和报告可见时间尚缺独立墙钟计时。

## 从启动到报告的线程与等待

| 环节 | 当前执行方式 | 可优化点/判断 |
|---|---|---|
| 启动 App | Vue 挂载不等待 Worker ready；Rust 启动持久 Python 子进程，独立线程读 stdout | Worker 冷启动会计算构建哈希；尚无启动计时，不应优先重构 |
| Worker 就绪与模型探测 | ping 回 ready，广播给各 WebView；符合条件的窗口会发 probe | 重复探测路径成立；probe 全量读取模型哈希，并占用模型调用锁 |
| 扫描与规则分类 | 一条后台分类任务；规则池默认最多4，保守模式1，小样本可能串行 | 已有并发；UI 的性能模式主要作用于分类/预览，不会让生成池自动增加 |
| Pose/Seg 分析 | 正常 UI 使用 V26 分类适配器；模型阶段逐素材运行；macOS 同进程共用请求锁 | 原生 ONNX CPU 内部线程未显式限制，因此串行请求不等于只用一个 CPU 核；须先测模型耗时和原生线程争抢 |
| 确认页与缩略图 | WebView 主线程渲染全量卡片；UI最多4请求，保守2；Worker预览池4 | 有重复线性查找、每次滚动全量几何扫描；后台预览350ms逐项排队。主要影响千张批次交互 |
| 批量生成 | 一条调度后台任务；按来源最多3个并发job；单来源尺寸/Logo/VIP/透明版本串行 | 已复用来源解码与分析，但每个输出仍重复构图；这3个job不是整个应用的OS线程总数 |
| 编码与落盘 | 在来源job内编码，超文件大小上限时尝试更多候选 | PNG候选被提前全部量化；JPEG可多轮编码；当前写盘占比低 |
| 校验与报告 | 结果汇总、文件校验；stdout经IPC广播；前端同步序列化/localStorage后显示报告 | 大结果可能造成停顿，需记录done→报告可见；现有累计阶段计时不含整个收尾链路 |

详细调用链：[UI/Rust](ui-host.md)、[Worker](worker-pipeline.md)。

## 已有测量能证明什么

复用 /tmp/pixelflow-smoke-20261005 的已保存报告：5个来源、27个输出、3个渲染job、输出复用0、失败0。

| 阶段 | 累计秒数 |
|---|---:|
| 解码 | 1.430 |
| 来源分析 | 2.631 |
| 构图 | 42.405 |
| 叠加 | 0.783 |
| 编码 | 27.363 |
| 写盘 | 0.677 |

构图+编码约占以上阶段累计耗时的92.7%。这不是用户等待时间或CPU时间占比：各并发job计时相加，可能重叠，也包含等待。报告中的75.291秒不能用于计算实际吞吐量。

classification接近0是因为生成复用了确认页分类结果，不能说明Pose/Seg很快。记录缺少完整wall time、确认前模型分项、缓存冷热状态和run-linked构建hash。已保存报告与当前源码/二进制关联来自会话记录，报告自身不能独立证明版本。详见[测量证据与局限](measurements.md)。

## 建议实施顺序

### 第一批：保持输出语义，先减少明确重复工作

1. **PNG候选按需生成**（低风险）。当前有大小上限时，先产生256/128/64/32色四份量化图，然后才尝试原图编码。原图已达标时四次量化全部浪费。改为编码失败后才构造下一候选，保留候选顺序与编码参数，能避免不必要CPU与内存工作。依据：[renderer.py:567](/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/worker/pixelflow_core/renderer.py:567)。
2. **同来源同尺寸的Logo/无Logo共享构图底图**（中等风险、优先验证收益）。两种输出构图参数一致，只在叠加阶段不同；现在每份输出都重新compose。缓存仅存在于该来源job内，叠加前复制底图。VIP背景/layout参数不保证相同，仅在所有影响构图的参数完全一致时复用。依据：[renderer.py:1124](/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/worker/pixelflow_core/renderer.py:1124)。
3. **避免多窗口重复probe**（低至中等风险）。一个窗口负责就绪探测，合并在途重复请求；模型路径/配置变化、Worker重启时仍必须重新探测。不要单纯删除模型锁或长期缓存未知新模型状态。依据：[App.vue:1427](/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/apps/desktop/src/App.vue:1427)、[model_service.py:377](/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/worker/pixelflow_core/model_service.py:377)。

### 第二批：针对素材与批量规模优化

4. **透明图只做一次背景转Alpha/裁边**。无category_template的浅背景PNG分支，同来源最多4个透明尺寸可重复全分辨率Python像素循环；先缓存精确抠图结果，各尺寸再缩放。启用模板时提前走另一分支，不能推断本次42秒都在Alpha处理。依据：[renderer.py:543](/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/worker/pixelflow_core/renderer.py:543)。
5. **确认页Map/Set索引与滚动合并**。先消除初始化/可见项回调中重复some/find/includes；滚动扫描用requestAnimationFrame合并。大批次仍有长帧时再评估虚拟列表。依据：[App.vue:1708](/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/apps/desktop/src/App.vue:1708)、[App.vue:1805](/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/apps/desktop/src/App.vue:1805)。
6. **编码重试、报告复制与IPC频率先计数**。JPEG质量循环、PNG候选重试、VIP中间JPEG均有实际或兼容性目的，不能先降画质。多窗口JSON复制和progress广播没有实测证明占大头，暂不改协议。

## 怎样证明优化有效

- 固定同一build、输入、设置和独立输出，记录选择尺寸/Logo/VIP/文件上限/模板、来源格式及模型缓存状态。
- 增加少量关键计时：spawn→ready；分类开始→确认可见（规则/Pose/Seg/cache）；确认→生成结束wall；结束→报告可见；编码尝试数与probe次数。将人为确认停留时间单列。
- 先对上述第一批逐项改动做同负载前后对照；输出像素/尺寸/透明度/文件上限/复核结果不能回退，并记录峰值内存。编码字节是否一致取决于原有确定性，至少要求解码后像素一致。
- 渲染池1/2/3/4与ONNX内部线程组合留到重复工作减少之后再测；不直接把3改为CPU核数。Python像素循环受GIL影响，多线程可能增加争抢，过多高分辨率画布也会提高峰值内存。

legacy_engine观察周期与两个deleted detection sidecar保持现状。本轮产物仅审计文档及计划指针；无产品代码修改。
