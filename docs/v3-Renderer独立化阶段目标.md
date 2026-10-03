# PixelFlow v3 Renderer 独立化阶段目标

## 1. 目标

将 v3 Renderer 从“迁移中的兼容写出层”升级为唯一的 v3 构图与像素写出实现：

- v3 Renderer 自己完成分类结果消费、构图计划消费、背景处理、缩放裁剪、Logo、透明图、唯品图和文件写出；
- `legacy` 只保留显式兼容入口和 v3 失败回退；
- `compare` 只负责隔离运行和差异报告；
- Renderer 与 legacy 不互相调用；
- 两条路径可以使用相同的 typed 输入，但不能共享对方的运行时模块、动态字典或写出函数。

本目标不删除 legacy 核心。legacy 至少保留一个版本周期，直到新的 v3 独立实现通过全部验收。

## 2. 当前迁移基线

> 本节保留 2026-09-27 的阶段性基线。文中的旧测试数量、规则版本和像素差异用于追溯历史，不作为当前验收结论；当前状态以 [`baselines/v3/current-20261003/acceptance-report.json`](../baselines/v3/current-20261003/acceptance-report.json) 为准。

截至 2026-09-27：

- v3 已通过 `V3EngineAdapter → Renderer` 正式写出产品主图、模特图、透明产品图和唯品图；
- `RenderRequest`、`CompositionPlan`、`RenderResult` 已进入 Worker typed contract；
- `compare` 已支持尺寸、模式、字节数、文件 SHA-256、RGBA 像素 SHA-256 和差异路径；
- v3 失败回退、源码 Worker 独立运行和 83 项自动化测试已通过；
- 固定 16 张素材输出数量和路径为 `120/120`，独立 v3 实现相对历史 `.app` 基线为 `exact=36`、`mismatches=84`；
- `renderer.py` 已移除 `_legacy_helpers()` 和 `generate_main_images.py` 调用，运行时只读取共享静态资源；
- Renderer 逐文件失败现由 `ImageEngine` 统一提升为 legacy fallback，结果保留 `fallback=true` 与 `fallback_reason`；鞋类 typed plan evidence 与缓存 identity 均写入 `v3-composition-1` 规则版本；
- 四类 request builder、Pose/Seg typed plan 和 fallback/人工复核摘要已接入 Worker；
- 鞋类快照中的相对路径计划已能在无 Pose/Seg 进程时被 Renderer 重放，单样本 `1/1` typed 校验通过；
- 鞋类双人/多人案例按本轮范围跳过，记录为 `skipped_by_scope`；使用用户提供模型目录完成 v3 原生现场运行，20/20 Pose、20/20 Seg、60 个 typed 计划均成功且 fallback 为 0，证据见 `v3-native-model-evidence.json`；
- 原生模型计划已在无模型进程的 Renderer 中重放 60 个模特图输出，typed 校验 `60/60` 通过，临时目录已清理；
- v3 正常路径已切换到无 legacy 导入的分类与 Pose/Seg façade；模型结果转换为 typed `CompositionPlan`，缺模型时明确记录 fallback，不伪造模型证据；
- v3 Renderer 默认读取仓库内 `worker/resources`，打包 spec 的 v3 静态资源输入已与 legacy 兼容资源分开；
- Worker、`.app`、v3 资源目录和模型目录的逐文件 SHA-256 清单已固化到 `baselines/archive/2026-09-27-v3-recheck/worker-artifact-hashes.json`；当前 onedir Worker 已同步到 `.app`，两者哈希均为 `627ebbff…`，正式 onefile 构建另记为 `027a624b…`；
- 已使用 v2.0 `.venv` 的 PyInstaller 6.22.0 重建 onefile Worker；历史正式 onefile 与当前 onedir `.app` 均在允许 semaphore 的宿主权限下通过启停、分类、生成和回退，普通沙箱尝试仍会触发 `semctl: Operation not permitted`。
- `.app` 主程序已在允许 semaphore 的宿主权限下完成受控启动审计：主进程和 `.app/Contents/Resources/pixelflow-worker` 均存活至 8 秒观察点，随后 SIGTERM 受控退出且无立即崩溃；证据见 `baselines/archive/2026-09-27-v3-recheck/desktop-app-startup-audit.json`。该审计覆盖启动/退出链路，不替代 UI 操作和完整业务回归。
- 当前正式 onefile Worker 哈希为 `027a624bb13bd9a739acfca2a5e53e2723bf777c93d1ab72141251469bee49bd`；旧 `6c5fadabe95c815b42ff3165cc606ffdf1d53d3a036f4e3b3b1da9777d3b05d0` 的完整协议报告保留在 `frozen-worker-latest-evidence.json`，作为历史证据。当前 `.app` 使用 onedir Worker，哈希为 `627ebbffb0f07e173963809de6dff7e8f851fd532cea58f8c7ca0bfb9a3bbde2`；`ping`/`shutdown` 与双跑证据见 `app-package-evidence-20260927.json`。
- 正式 onefile 产物位于 `worker/dist/pixelflow-worker`，哈希为 `027a624bb13bd9a739acfca2a5e53e2723bf777c93d1ab72141251469bee49bd`；当前 `.app` onedir Worker 哈希为 `627ebbffb0f07e173963809de6dff7e8f851fd532cea58f8c7ca0bfb9a3bbde2`，`ping`/`shutdown` 验收见 `app-package-evidence-20260927.json`。

历史 `.app` 基线和当前仓库内置 legacy 规则可能不是同一版本。所有像素结论必须标明基线来源，不能把不同版本的结果混为一个通过条件。

## 3. 目标架构

```text
用户请求
   ↓
Worker typed request
   ↓
分类结果 + CompositionPlan
   ↓
V3 Renderer ──→ 正式 v3 输出
   │
   └──失败──→ ImageEngine fallback ──→ LegacyAdapter ──→ legacy 输出

Compare：LegacyAdapter 写正式输出；V3 Renderer 写临时目录；比较后清理临时目录。
```

### 3.1 允许的依赖

- `contracts.py` 中的 typed contract；
- 独立的无状态图像工具模块；
- Renderer 自己的构图算法和资源选择逻辑；
- legacy 和 v3 共同读取的静态资源文件。

### 3.2 禁止的依赖

- `renderer.py` 导入 `legacy_engine`；
- `legacy_engine` 导入 `renderer.py`；
- v3 运行时动态导入外部 `PixelFlow-App-v2.0`；
- 用 legacy 写出的文件再冒充 v3 正式写出；
- 用 compare 临时文件污染正式输出目录。

## 4. A–G 新阶段目标

### 阶段 A：冻结独立输入契约

目标：让四类输出拥有完整、可序列化、与实现无关的 typed 输入。

实施项：

1. 固化产品主图、模特图、透明产品图、唯品图四类 request builder；
2. 将输出路径、尺寸、背景、素材类型、Logo、边框、透明选项、大小限制和构图计划全部放入 typed 字段或稳定子结构；
3. 将分类、Pose/Seg 证据、保护框、风险标记、人工复核提示和 fallback 原因写入结果摘要；
4. 让 legacy 和 v3 接收同一份 request 摘要，但由各自实现独立解释；
5. 缓存身份包含源文件版本、模型版本、构图规则版本、品类和输出尺寸。

退出条件：

- Renderer 不需要读取 legacy 的 metadata 约定才能完成一次渲染；
- 四类 request 可以单独序列化、重放和比较；
- request 中不包含 v2 模块对象或不可序列化的运行时状态。

### 阶段 B：建立 v3 自有基础图像算法

目标：把 v3 所需的基础图像算法从 legacy 中移出，形成 v3 自己可测试的实现。

实施项：

- contain / cover 等比例缩放；
- 保护框约束和安全裁剪；
- 四角背景采样、浅色背景连通区域和背景延展；
- 模特图安全镜像延展；
- RGB / RGBA 转换；
- JPG / PNG 大小限制；
- 输出尺寸、模式、字节数和像素摘要校验。

退出条件：

- `renderer.py` 不再导入或调用 `legacy_engine`；
- 基础算法测试只依赖 v3 模块和 Pillow；
- 算法规则版本号写入构图计划和缓存身份；
- 失败返回可读错误，不能静默生成不符合模式或尺寸的文件。

### 阶段 C：v3 独立完成产品主图

目标：产品主图的构图、资源选择和写出全部由 v3 Renderer 完成。

实施项：

1. v3 Renderer 自己实现品类模板比例和位置；
2. v3 Renderer 自己实现浅色背景保护和边缘融合；
3. 按尺寸和背景亮度选择四套 Logo 资源；
4. 保留 `含Logo` / `无Logo` 目录、尺寸和命名；
5. 单文件失败继续处理同批其他文件；
6. legacy 只通过 `V26EngineAdapter` 响应显式 legacy 请求或 fallback。

退出条件：

- v3 正式输出目录中没有 legacy 写入调用链；
- v3 和 legacy 可以在同一输入上分别生成，互不覆盖对方文件；
- 当前选定基线的数量、路径、尺寸、模式和允许像素差异有持久化报告。

### 阶段 D：v3 独立完成模特图和鞋类构图

目标：构图计划只由 typed 计划表达，Renderer 不直接依赖 Pose/Seg 实现。

实施项：

- Pose/Seg 结果转换为 `CompositionPlan`；
- 保护框、裁剪比例、放大比例、风险标记、`requires_review` 和 `review_hint` 全部进入计划；
- 可靠鞋部轮廓才允许覆盖保护框；
- 高风险动作自动使用保守构图；
- 双人/多人案例在本轮标记为 `skipped_by_scope`，不计入通过数量。

退出条件：

- 已有真实样本的类别都有分类记录和可复核计划；
- Renderer 可在没有 Pose/Seg 进程的情况下消费已保存的 typed plan；
- 缺模型时结果明确标记回退或未运行，不伪造模型证据。

### 阶段 E：v3 独立完成透明图和唯品图

目标：透明图和唯品图不再通过 legacy 生成中间文件。

实施项：

1. v3 自己完成透明背景和 alpha 处理；
2. v3 自己完成透明尺寸表、RGBA 写出和 PNG 大小限制；
3. v3 自己完成唯品专享目录、无 Logo 规则、边框叠加和命名；
4. 唯品图不得依赖 legacy 已写出的主图文件；
5. 透明图和唯品图支持独立重试。

退出条件：

- 四类输出都可以从源文件和 typed request 直接写出；
- 输出目录中不存在 legacy 中间文件依赖；
- compare 可以单独报告透明图和唯品图差异。

### 阶段 F：切断 Renderer 与 legacy 的运行时调用

目标：完成真正的实现隔离。

实施项：

- 删除 Renderer 对 `_legacy_helpers()` 和 legacy 构图函数的调用；
- 删除 legacy 对 Renderer 的任何导入或反向调用；
- `ImageEngine` 只负责模式路由、fallback 和 compare 隔离；
- v3 异常时由 `ImageEngine` 统一调用 legacy，不在 Renderer 内部回退；
- compare 的正式输出固定由 legacy 产生，v3 只写临时目录。

退出条件：

- 静态搜索确认 `renderer.py` 不包含 `legacy_engine` 运行时导入；
- v3、legacy、compare 三种模式分别有进程级测试；
- v3 失败时正式输出仍完整，结果包含 `fallback=true` 和 `fallback_reason`；
- compare 临时目录在返回前清理。

### 阶段 G：独立 Worker 和 macOS 打包

目标：v3 在没有外部 v2 checkout 的环境中可构建、启动、生成和退出。

实施项：

1. Worker 构建输入只来自当前仓库的 v3 Worker、内置资源和模型资源；
2. 临时环境移除外部 `PixelFlow-App-v2.0` 路径；
3. 执行 `ping`、分类、v3 生成、缺失模型回退和 `shutdown`；
4. 对 Worker、`.app` 主程序、内置资源和模型目录记录架构与 SHA-256；
5. 在允许 PyInstaller semaphore 的 macOS 宿主环境完成冻结进程验收。

退出条件：

- `.app` 无外部 v2 目录仍可启动、生成和退出；
- `.app` 内 Worker 与构建输入逐字节一致；
- 签名和公证作为发布阶段单独处理。

## 5. 验收顺序

1. 先完成 A，冻结四类 request 和结果摘要；
2. 完成 B，切断 Renderer 对 legacy 纯函数的依赖；
3. 依次完成 C、D、E，并为每类输出保留独立报告；
4. 完成 F，执行静态依赖检查、v3 失败回退和 compare 隔离测试；
5. 重新生成当前内置 legacy 基线，同时保留历史 `.app` 基线作为版本差异记录；
6. 完成 G 的无外部 v2 冻结进程验收；
7. 通过全部条件后，再考虑结束 legacy 兼容周期。

## 6. 必须保留的证据

- 四类 request / result JSON 摘要；
- 产品主图、模特图、透明图、唯品图的输出清单；
- compare 文件级差异报告；
- 当前内置 legacy 基线和历史 `.app` 基线的来源、版本和哈希；
- Worker 测试结果（当前 83 项）；
- 鞋类矩阵及 `multiple_people=skipped_by_scope` 记录；
- 无外部 v2 的 Worker 与 `.app` 启停日志；
- Worker、`.app` 和资源架构哈希。

## 7. 最终完成定义

只有以下条件同时满足，才能把迁移标记为“Renderer 独立完成”：

1. v3 Renderer 不导入、不调用 legacy；
2. legacy 只用于显式兼容入口和失败回退；
3. 四类输出均由 v3 Renderer 从源文件和 typed request 直接写出；
4. compare 不污染正式输出；
5. 当前选定基线的数量、路径、尺寸、模式和像素结果有可复现报告；
6. v3 失败回退通过进程级测试；
7. 无外部 v2 的冻结 Worker 和 `.app` 通过启动、生成、回退和退出验收；
8. legacy 至少保留一个版本周期后，再单独评估删除旧核心。

## 8. 当前下一步

真实 Pose/Seg 样本的可复核计划证据和一次 v3 原生现场运行已保存到鞋类矩阵关联快照；
四类输出覆盖报告已补齐，产品主图 96、模特图 60、透明产品图 8、唯品图 16，合计 180
个输出 typed 校验通过。正式 onefile 和 `.app` 内 Worker 已在允许 PyInstaller semaphore
的宿主权限下通过启动、分类、生成、回退、退出及哈希验收；普通沙箱仍会阻止
`semctl`，该限制已单独记录。

当前仍保留内置 legacy 核心作为显式兼容和失败回退入口；历史 `.app` 的像素差异继续按基线来源记录，不解释为单纯参数问题。

技术验收完成后，先保留 legacy 一个完整版本周期，再单独评估旧核心删除；周期状态和复核清单见 `baselines/archive/2026-09-27-v3-recheck/legacy-compatibility-cycle.json`；签名和公证仍属于发布阶段工作。

当前审查已确认 v3 正常分类与 Pose/Seg 路径使用自有 façade，子进程中不会加载 legacy 模型模块；现场 ONNX 已使用 v2.0 `.venv` 完成，系统 Python 仍需安装 `onnxruntime` 才能独立重跑，模型目录按 `models/README.md` 的外部选择设计保留，不作为 `.app` 内置资源宣称。
