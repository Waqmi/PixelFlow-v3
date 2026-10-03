# PixelFlow v3 算法迁移与 Renderer 分阶段更新计划

> 独立化目标已重新整理到：[v3 Renderer 独立化阶段目标](./v3-Renderer独立化阶段目标.md)。本文件保留原迁移过程和历史证据。

> 当前技术收口以 [`baselines/v3/current-20261003/acceptance-report.json`](../baselines/v3/current-20261003/acceptance-report.json) 为准；下文的旧测试数量、像素差异和阶段性退出条件均保留作历史追溯。

## 1. 文档目的

当前 v3 Worker 已具备 typed contracts、鞋类风险 façade、内置兼容核心和独立 Renderer；legacy 仅保留显式兼容、compare 正式输出和失败回退路径。

本计划把 `legacy` 像素算法逐步迁移到 v3 Renderer，同时保持：

- 输出数量、尺寸、目录和命名规则不变；
- `legacy` 作为正式回退路径；
- `compare` 正式输出只写 legacy；
- 任一阶段出现基线差异时可以立即回退；
- 不新增图像生成模型，不修改无关 UI。

## 2. 当前状态

已完成：

- `RenderRequest` / `RenderResult` typed 接口；
- PIL 等比例缩放、安全裁剪、背景延展、透明图、Logo、唯品图和大小限制的 v3 Renderer façade；
- v3 生成结果通过 typed Renderer 做只读尺寸、模式和大小校验；
- 鞋类结果可导出通用 `CompositionPlan` / `ProtectedRegion`；
- Seg 只有显式可靠鞋部轮廓证据时才允许改变鞋部保护框；
- Pose/Seg 缓存版本键和模型状态接口；
- 内置 v2.6 兼容核心，默认运行不需要外部 `PixelFlow-App-v2.0`。
- 当前正式 onefile Worker 哈希为 `027a624bb13bd9a739acfca2a5e53e2723bf777c93d1ab72141251469bee49bd`；旧 `6c5fadabe95c815b42ff3165cc606ffdf1d53d3a036f4e3b3b1da9777d3b05d0` 仅作为历史证据保留。当前 `.app` 使用 onedir Worker，哈希为 `627ebbffb0f07e173963809de6dff7e8f851fd532cea58f8c7ca0bfb9a3bbde2`，详见 `app-package-evidence-20260927.json`。

仍需跟踪：

- 历史 `.app` 基线与当前 v3 输出仍存在像素差异，继续按基线来源记录，不宣称 parity；
- 桌面 `.app` UI smoke 已到分类确认页，但确认后的完整 UI 生成尚未形成通过证据；
- legacy 兼容周期尚未结束，周期清单见 `baselines/archive/2026-09-27-v3-recheck/legacy-compatibility-cycle.json`。

## 3. 迁移原则

### 3.1 先保持行为，再替换实现

每个输出类型先建立 v2.6 基线，再把同一输入、同一构图计划和同一资源交给 v3 Renderer。只有数量、尺寸、目录、命名、色彩模式和像素摘要通过后，才允许切换默认路径。

### 3.2 构图与渲染分离

构图层只产生 typed `CompositionPlan`：比例、位置、保护区域、风险和复核提示。Renderer 只读取 typed plan 和 `RenderRequest`，不读取 v2 动态字典或模型内部变量。

### 3.3 每一步都可回退

- `legacy`：直接调用内置兼容核心；
- `v3`：调用已迁移的 Renderer；发生异常时写入 legacy，并返回 `fallback=true`；
- `compare`：v3 只写临时目录，正式输出仍由 legacy 产生；
- 迁移开关通过 `PIXELFLOW_ENGINE_MODE` 和显式请求 `mode` 控制。

## 4. 分阶段实施

### 阶段 A：冻结渲染输入和输出契约

目标：明确 Renderer 每个输出文件实际需要的全部输入。

工作内容：

1. 从 `generate_main_images.py` 提取四类输出的输入表：
   - 产品主图；
   - 模特图；
   - 透明产品图；
   - 唯品图。
2. 为每个输出建立 typed `RenderRequest`，包括：
   - 源文件；
   - 输出尺寸和背景；
   - 品类和素材类型；
   - `CompositionPlan`；
   - Logo 资源和 Logo 状态；
   - 透明图和唯品图选项；
   - 文件大小限制。
3. 为每个输出保存正式路径、尺寸、模式、文件大小和校验结果。

退出条件：

- 同一输入可以同时生成 legacy request 和 v3 request；
- 两套 request 的输出路径完全一致；
- 旧适配器无需读取 v3 Renderer 内部变量。

### 阶段 B：抽取无状态图像基础操作

目标：把不涉及品类判断的 PIL 操作迁移为可单测函数。

迁移顺序：

1. 等比例 contain / cover；
2. 保护框约束裁剪；
3. 边缘采样和背景延展；
4. RGB / RGBA 色彩模式转换；
5. JPG 文件大小压缩；
6. PNG 透明背景和大小限制；
7. 输出尺寸、模式和大小校验。

退出条件：

- 不允许非等比例拉伸；
- 三种正式尺寸和透明尺寸均通过；
- 白底 JPG 转透明图时背景 alpha 正确；
- 极小文件大小限制会返回可读失败原因；
- 基础操作单测不依赖 v2 模块。

### 阶段 C：迁移产品主图

目标：先迁移最稳定的产品主图，建立完整双写比较链路。

工作内容：

1. 把 `build_canvas` 的输入改为 typed plan 和 typed render request；
2. 保留现有浅色背景保护、模板参数和源文件缩放调整；
3. 按尺寸选择正确的 Logo 资源；
4. 严格保留 `含Logo` / `无Logo` 目录；
5. 用 v3 Renderer 写入临时输出目录；
6. 通过 compare 报告比较：
   - 输出路径；
   - 尺寸和模式；
   - 文件大小；
   - 像素摘要和 SHA-256。

退出条件：

- 产品类无构图变化样本达到 120/120 路径和尺寸一致；
- 默认 Logo 配置下差异为 0，若使用用户自定义 Logo，报告必须记录 Logo 文件哈希；
- compare 临时目录不会污染用户输出目录。

### 阶段 D：迁移模特图和鞋类构图

目标：把风险最高的模特图构图与 Renderer 解耦。

工作内容：

1. Pose 结果先转换为通用 `CompositionPlan`；
2. 鞋类计划至少包含：
   - 鞋部保护框；
   - 方图和竖图裁剪比例；
   - 动作幅度风险；
   - 单脚、交叉腿、脚部遮挡和鞋底朝镜头风险；
   - 人物框贴边风险；
   - 双人/多人风险；
   - `requires_review` 和 `review_hint`。
3. Seg 普通人物轮廓只能作为风险证据；
4. 只有 `reliable_shoe_bbox` 或等价可靠轮廓字段可以覆盖鞋部保护框；
5. 高风险计划自动降低放大比例，并保留人工复核提示。

退出条件：

- 正常站姿、弯腿、单脚、交叉腿、脚部遮挡、鞋底朝镜头、贴边、双人/多人八类样本均有记录；
- 每个样本记录分类、Pose/Seg 证据、保护框、裁剪比例、风险和最终输出；
- 模特图目标区域未被裁掉；
- 高风险样本不会自动补全被遮挡鞋部。

### 阶段 E：迁移透明图和唯品图

目标：迁移两个独立输出分支，避免主图逻辑影响渠道图。

工作内容：

1. 透明图使用独立的透明背景处理和透明尺寸表；
2. 唯品图固定使用 `唯品专享1440/无Logo` 路由；
3. 保留旧边框资源、颜色模式和文件大小限制；
4. compare 只比较元数据和像素摘要，不把 v3 临时文件复制到正式目录。

退出条件：

- 透明图全部为 RGBA，背景 alpha 与 v2.6 规则一致；
- 唯品图目录、尺寸、边框和命名一致；
- 文件大小限制和失败清单可观察；
- 四类输出可以独立重试。

### 阶段 F：切换 v3 默认渲染路径

目标：让默认 `v3` 真正负责像素写出。

工作内容：

1. 将 `V3EngineAdapter` 的批量输出改为：
   - 分类；
   - 生成 typed 构图计划；
   - 创建 typed `RenderRequest`；
   - 调用 v3 Renderer 写出文件；
   - 运行输出树校验。
2. legacy 只保留为：
   - 显式兼容模式；
   - v3 异常回退；
   - compare 正式输出。
3. 把每个迁移完成的输出类型从 legacy renderer 列表移除。

退出条件：

- 默认 `v3` 的正式文件由 v3 Renderer 写出；
- v3 异常时 legacy 回退仍能完成任务；
- `compare` 不改变正式输出；
- v3 运行过程中不动态导入外部 `PixelFlow-App-v2.0`。

### 阶段 G：删除运行时外部路径并固化打包

目标：完成独立 Worker 和 macOS `.app` 的可复现构建。

工作内容：

1. 保留内置兼容核心，移除正常运行对外部 v2 checkout 的要求；
2. Tauri 打包前自动构建 Worker；
3. 固化 Worker、图标和资源文件的构建输入；
4. 在没有外部 v2 目录时执行：
   - `ping`；
   - 分类；
   - 生成；
   - 模型缺失回退；
   - `shutdown`。
5. 保存 `.app` 的架构、资源哈希和 Worker 哈希。

退出条件：

- `.app` 可以在没有 `PixelFlow-App-v2.0` 目录时启动和生成；
- `.app` 内 Worker 与构建输入逐字节一致；
- `legacy`、`v3`、`compare` 行为可复现；
- 发布签名和公证作为独立发布工作处理。

## 5. 回归矩阵

每个迁移阶段至少运行：

```bash
cd /Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/worker
../../PixelFlow-App-v2.0/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

产品类全量基线：

```bash
cd /Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta
python3 tools/verify_output_baseline.py \
  baselines/v2.6/app-20260920-120/manifest.json \
  /path/to/generated/output
```

Renderer 迁移还必须单独检查：

- 1440×1440；
- 1440×1920；
- 1125×1500；
- RGB / RGBA；
- 含 Logo / 无 Logo；
- 透明图；
- 唯品图；
- 文件大小限制；
- 保护框和人物目标区域；
- 输出目录和命名。

## 6. 回退与失败处理

- 单个文件 Renderer 失败：记录失败项，继续处理其他文件；
- 单类输出迁移失败：该类回退 legacy，其他已通过类别继续走 v3；
- 全批次 v3 失败：回退 legacy，并返回 `fallback_reason`；
- compare 失败：保留 legacy 正式输出，差异报告记录 v3 错误；
- 缓存身份变化：清除旧构图结果，不触碰用户输出目录。

## 7. 完成定义

只有同时满足以下条件，才可以移除 legacy 像素写出路径：

1. 四类输出均由 v3 Renderer 正式写出；
2. 鞋类八类矩阵和真实输出证据齐全；
3. v2.6 数量、尺寸、目录、命名和允许差异均有基线报告；
4. 默认 v3 失败回退通过进程级测试；
5. 无外部 v2 目录时 `.app` 可启动、生成和退出；
6. 迁移规则版本和缓存版本已更新；
7. legacy 仍保留至少一个版本周期作为兼容入口。

## 8. 2026-09-27 收尾执行状态

已完成：

- v3 Renderer 正式写出四类输出；
- `RenderRequest` 增加输出路径、素材类型、Logo、边框、背景和大小限制字段；
- `compare` 增加文件级尺寸、模式、字节数、文件 SHA-256 和 RGBA 像素 SHA-256 报告；
- 现有 20 张鞋类素材已登记到 `fixtures/shoe-matrix.json`；
- Worker 源码在无外部 v2 checkout 时通过 `ping`、分类、v3 生成和 `shutdown` 冒烟；
- Worker 自动化测试更新为 83 项并通过。

当前技术验收已完成；尚未满足的退出条件：

- 固定 120 文件复核已形成两份持久化报告：`verify-summary.json` 为 `84/120` 一致，切断后隔离报告 `verify-summary-isolated.json` 为 `36/120` 一致；两者数量/路径均为 `120/120`，历史 `.app` 像素 parity 仍需按基线来源记录，不能宣称已完成 parity；
- legacy 像素写出路径仍需作为兼容和回退入口保留一个完整版本周期，再单独评估删除；周期清单见 `baselines/archive/2026-09-27-v3-recheck/legacy-compatibility-cycle.json`；
- 普通沙箱启动冻结 Worker 仍受 `semctl: Operation not permitted` 限制，这是环境权限记录；正式 onefile、`.app` 内 Worker 及 `.app` 主程序已在允许 semaphore 的宿主权限下通过对应启动、分类、生成、回退和受控退出验收。

鞋类矩阵中的 `multiple_people` 已按当前范围跳过；20/20 Pose、20/20 Seg、60 个 typed 计划和 60 个模特图重放输出均已有证据，四类输出覆盖报告的 180 个输出 typed 校验通过。`.app` 主程序启动审计证据见 `baselines/archive/2026-09-27-v3-recheck/desktop-app-startup-audit.json`；该项只覆盖启动/退出链路。
