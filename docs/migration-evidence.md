# v3 迁移阶段证据：鞋类矩阵与独立打包

记录日期：2026-09-27

## 本轮 A/D/F 迁移记录

- `RenderAssets`、`RenderLayout`、`RenderSummary` 和四类 request builder 已进入稳定契约；Worker 可从 JSON 重放 `composition_plans`。
- v3 主图、模特图、透明图、唯品图均由对应 builder 直接交给 `Renderer`，不再把构图输入塞入 Renderer 的动态 metadata。
- Pose/Seg 结果转换为 typed `CompositionPlan`；缺模型、模型异常和多人鞋类分别记录 `fallback_reason`、`review_hint`、`skipped_by_scope`。跳过项从 `validation.passed` 中扣除并单独计数。
- v3 分类器和 Pose/Seg 服务使用无 legacy 导入的自有 façade；显式 legacy 适配器仍保留兼容入口。模型缺失或推理失败时返回 typed fallback 计划，不伪造模型证据。
- Renderer 默认资源已迁移到 `worker/resources`；legacy 资源仍单独保留给显式兼容入口，避免 v3 默认路径从 legacy 目录取资源。
- Renderer 单文件失败即使以 typed `failures` 返回，也会由 `ImageEngine` 提升为 v3 批次失败并统一走 legacy fallback；结果保留 `fallback=true` 与 `fallback_reason`，避免正式输出只留下部分 v3 文件。

## 鞋类真实素材矩阵

矩阵文件：[fixtures/shoe-matrix.json](../fixtures/shoe-matrix.json)

- 素材目录：`../05_测试素材与输出/鞋`
- 实际图片：20 张 JPG；每张记录文件 SHA-256、尺寸和色彩模式。
- Worker 规则分类：20/20 返回 `detail`，品类分类为 `shoes`（名称规则，置信度 0.95）。
- 原始规则矩阵运行关闭外部 Pose/Seg 模型，因此该次运行的 `pose_evidence`、`segmentation_evidence`、保护框、裁剪比例和最终输出路径均没有伪造，按 `not_run` / `missing_fixture` 记录；矩阵顶层状态已更新为已挂接原生模型证据，不能把这些历史字段解读为当前现场运行状态。
- 另有一份可复核的真实模型证据快照：20/20 张素材的 Pose cache、60 个 typed `CompositionPlan`（每张 3 个尺寸）以及 2 张素材的 Seg cache；快照记录源文件 SHA-256、模型 SHA-256、cache 版本和构图规则版本，见 [shoe-composition-evidence.json](../baselines/v2.6/v3-recheck-20260927/shoe-composition-evidence.json)。
- 已使用用户提供的 `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v2.0/models` 作为外部模型根目录验证模型发现；系统 Python 环境缺少 `onnxruntime` 时，Pose 服务会返回 `fallback_reason=未安装 ONNX Runtime 或 NumPy`，且进程中未加载任何 `pixelflow_core.legacy_engine.*` 模块。
- 该目录的 Pose/Seg 文件与仓库 `models/` 对应文件 SHA-256 分别为 `86e1275f…` 和 `52269a89…`，逐文件一致。
- Worker 协议 `probe_models` 已对该目录返回 Pose/Seg `unavailable` + `fallback=true`，随后 `shutdown` 正常退出；失败原因仅为当前环境缺少 `onnxruntime/NumPy`。

| 类别 | 真实素材 | 状态 |
|---|---:|---|
| normal_stance | 3 | `observed_visual_review` |
| large_bend | 7 | `observed_visual_review` |
| single_foot | 4 | `observed_visual_review` |
| crossed_legs | 1 | `observed_visual_review`，待 Pose 确认 |
| foot_occlusion | 4 | `observed_visual_review` |
| sole_facing_camera | 3 | `observed_visual_review` |
| person_frame_edge | 3 | `observed_visual_review`，待 Pose 确认 |
| multiple_people | 0 | `skipped_by_scope` |

`multiple_people` 按当前验收范围暂时跳过，矩阵保留 `skipped_by_scope` 记录，不把它计入本轮通过数量。

### 真实 Pose/Seg 计划证据快照

`fixtures/shoe-matrix.json` 的 `plan_evidence` 指向上述快照。快照来自 2026-09-26
的 Worker 模型缓存，源文件 SHA-256 与矩阵 20 张素材逐一一致；这份缓存快照只能用于重放和审查，不能单独视为现场推理证据。
Seg 的普通人物 mask 只作为证据保存，没有被提升为鞋部保护框；鞋部保护框均标记为
Pose 来源。该快照补齐了 D 阶段的可复核输入与计划证据，不能替代后续新环境中的
模型重跑和四类输出报告。

已从快照重放 `shoe-01` 的 `1440x1440` 计划，在不启动 Pose/Seg 进程的情况下生成 1 个
模特图；结果为 `mode=v3`、`renderer=typed-v3`、`fallback=false`、`failures=[]`，typed
校验 `1/1` 通过。

随后使用 `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v2.0/.venv/bin/python`（含
`onnxruntime 1.19.2` 与 `numpy 1.26.4`）运行 v3 原生模型服务，并将缓存目录隔离到临时目录，确保不是旧缓存重放：20/20 Pose、20/20 Seg、60 个 typed 计划、0 个 fallback，8 个素材标记 `requires_review`。完整逐素材证据见
[v3-native-model-evidence.json](../baselines/v2.6/v3-recheck-20260927/v3-native-model-evidence.json)，运行进程未加载 `pixelflow_core.legacy_engine.*`。

同一份现场证据随后在无 Pose/Seg 进程的系统 Python 中重放 Renderer：20 张素材、3 个尺寸共 60 个模特图输出，`failures=0`、`typed_renderer.validated=60`，临时输出目录已清理。报告见
[v3-native-model-render-report.json](../baselines/v2.6/v3-recheck-20260927/v3-native-model-render-report.json)。

## 无外部 v2 checkout 的 Worker 进程证据

源代码 Worker 使用仓库内置的 `worker/pixelflow_core/legacy_engine`，未设置或导入外部
`PixelFlow-App-v2.0` 路径。使用系统 Python（含 Pillow）执行以下协议冒烟：

1. `ping`：返回 `ready`。
2. `classify_materials`：20 张规则分类完成，品类为 `shoes`。
3. `start_generation`：单张鞋素材、`mode=v3`、`1440x1440`、无 Logo/唯品图成功写出
   `产品主图/1440x1440/无Logo/2026-9-4BMAI0840.jpg`，返回 `renderer=typed-v3`、
   `fallback=false`。
4. `shutdown`：返回 `done`，进程退出码为 0。

同一源代码 Worker 的 `legacy`、`v3`、`compare` 三种模式均已做进程级复核；compare
正式目录只保留 legacy 输出，v3 临时目录在返回前清理。另以
`source_metadata_overrides={source: {"logo_luminance": "bad"}}` 做受控 Renderer
故障注入，进程返回 `mode=v3`、`fallback=true`，legacy 正式输出 1 个文件并正常退出。
这项记录证明的是回退路由，不代表自然损坏素材；模型缺失探测的 `fallback=true` 也不
等同于 Renderer 回退。

可复现的最小环境变量为：

```bash
PYTHONPATH="$PWD/worker" \
  /opt/homebrew/bin/python3 worker/worker_entry.py
```

正式 onefile Worker 在无额外宿主权限的当前沙箱中尝试时返回：

```text
[PYI-9200:ERROR] Failed to initialize sync semaphore!
semctl: Operation not permitted
```

该错误发生在 PyInstaller 启动器初始化阶段。随后在允许 PyInstaller semaphore 的宿主
权限下，正式 onefile 和 `.app` 内同一 Worker 资源路径均完成 `ping`、分类、v3 生成、
缺失模型探测、受控回退和 `shutdown`，退出码为 0，stderr 为空。完整协议摘要见
[frozen-worker-latest-evidence.json](../baselines/v2.6/v3-recheck-20260927/frozen-worker-latest-evidence.json)。
另用同一源码完成 PyInstaller 6.22.0 onedir 实验构建：启动、`ping`、分类、v3 生成、缺失模型探测
回退、受控回退和 `shutdown` 均通过，退出码为 0；实验产物 SHA-256 为
`6b4aa3da9af69ca93e1c0c8edda4444e91bbc333e9b5692494e69d9439a2c1c6`，并实测 VIP 输出
`2/2` typed 校验通过。该 onedir 实验记录见
[frozen-worker-ondir-evidence.json](../baselines/v2.6/v3-recheck-20260927/frozen-worker-ondir-evidence.json)。正式
onefile/.app 宿主权限验收及当前 Worker 哈希以
[frozen-worker-latest-evidence.json](../baselines/v2.6/v3-recheck-20260927/frozen-worker-latest-evidence.json) 为准。

## 回归测试

```bash
PYTHONPATH=worker /opt/homebrew/bin/python3 \
  -m unittest discover -s worker/tests -p 'test_*.py'
```

结果：`83 tests OK`。其中鞋类矩阵测试会校验 20 张真实素材的文件哈希、八类键集合以及
`multiple_people=skipped`，不会把跳过类别当成通过。

## 固定 16 张素材 Renderer 复核

使用 `tools/run_renderer_baseline.py` 重新生成 120 个 v3 文件，并运行
`tools/verify_output_baseline.py`。结果为数量/路径 `120/120`，逐文件
`exact=36`、`mismatches=84`。详细清单保存在：

- `baselines/v2.6/v3-recheck-20260927/render-report.json`
- `baselines/v2.6/v3-recheck-20260927/verify-summary.json`

本次复核以历史 `.app` 版本为基线；Logo、唯品边框和透明图仍有版本差异，legacy
继续保留为正式输出和回退入口。

阶段 B/F 切断后，v3 Renderer 使用本地 Pillow 算法重新生成同一批 120 个文件：
数量/路径仍为 `120/120`，`typed_renderer.validated=120`、失败为 0；相对历史
`.app` 基线为 `exact=36`、`mismatches=84`。该变化来自构图实现从 legacy 纯函数切换为
v3 自有实现，不能解释为单纯参数差异。当前结果摘要保存在：

- `baselines/v2.6/v3-recheck-20260927/render-report-isolated.json`
- `baselines/v2.6/v3-recheck-20260927/verify-summary-isolated.json`

补充四类 v3 输出覆盖报告：产品主图 96、模特图 60、透明产品图 8、唯品图 16，合计
180 个输出，typed renderer `180/180` 通过，失败 0。产品主图、透明图和唯品图来自同一
次隔离运行；模特图来自保存的 Pose/Seg typed plan 重放。报告见
[v3-output-kind-report-20260927.json](../baselines/v2.6/v3-recheck-20260927/v3-output-kind-report-20260927.json)。
本轮同时修正 VIP 路径没有尺寸目录导致未纳入 typed validation 的问题，唯品图现在按
`1440x1440` 独立校验。
compare 结果新增 `output_kind_summary`，可分别汇总产品主图、模特图、透明图和唯品图的
文件数、缺失数与差异数。

## Worker 与 `.app` 构建输入哈希

| 产物 | 架构 | SHA-256 |
|---|---|---|
| `worker/dist/pixelflow-worker` | Mach-O arm64 | `027a624bb13bd9a739acfca2a5e53e2723bf777c93d1ab72141251469bee49bd` |
| `dist/PixelFlow-v3-beta.app/Contents/Resources/pixelflow-worker` | Mach-O arm64 | `027a624bb13bd9a739acfca2a5e53e2723bf777c93d1ab72141251469bee49bd` |
| `dist/PixelFlow-v3-beta.app/Contents/MacOS/pixelflow-desktop` | Mach-O arm64 | `31eb16a8b05020e86f61aec1d698dba6206514e6a1ce4520a45834633e211976` |

资源与模型目录的逐文件清单及聚合 SHA-256 已保存到
[worker-artifact-hashes.json](../baselines/v2.6/v3-recheck-20260927/worker-artifact-hashes.json)：
`worker/resources` 聚合哈希为
`3d27d0639aa1bd498c4de42f14a22d9a2e04839e581d55a57a95d1069058e9d5`，模型目录聚合哈希为
`2d8086fdbcbef70fdc86fd9ed37f78e6112dbc7ab758d7eefd67ebdaa41844ff`。

已将最新打包 Worker 同步到 `.app` 内资源；当前 `.app` Worker 与
`worker/dist/pixelflow-worker` SHA-256 一致（均为
`027a624bb13bd9a739acfca2a5e53e2723bf777c93d1ab72141251469bee49bd`）。本次 `.app` 打包与 Worker `ping`/`shutdown` 验证记录在
[app-package-evidence-20260927.json](../baselines/v2.6/v3-recheck-20260927/app-package-evidence-20260927.json)；完整生成协议仍以冻结证据
[frozen-worker-latest-evidence.json](../baselines/v2.6/v3-recheck-20260927/frozen-worker-latest-evidence.json) 为准。前一版哈希和历史 `.app` 哈希仍保存在清单的 `previous_sha256` / `historical_sha256` 字段。
已使用 v2.0 `.venv` 的 PyInstaller 6.22.0 成功重建 `worker/dist/pixelflow-worker`；无权限
沙箱直接启动仍返回 `semctl: Operation not permitted`，宿主权限验收已通过。
重建命令为：`PIXELFLOW_PYTHON=/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v2.0/.venv/bin/python python worker/build_worker.py`。
当前 `worker/pixelflow-worker.spec` 已移除外部 `PixelFlow-App-v2.0` 的 `pathex` 和顶层
legacy hidden import，冻结输入限定为仓库内 `pixelflow_core`、内置兼容包和静态资源；正式
onefile/.app 的运行证据保存在冻结 Worker 报告中。
模型目录当前按 `models/README.md` 作为用户选择的外部目录维护，不随 `.app` 打包；因此本次模型 SHA-256 记录证明的是仓库模型与缓存身份一致，不证明模型已嵌入 `.app`。无模型时 Worker 仍应走明确 fallback。

桌面主程序启动审计也已完成：在允许 semaphore 的宿主权限下直接启动
`dist/PixelFlow-v3-beta.app/Contents/MacOS/pixelflow-desktop`，主进程及其
`Contents/Resources/pixelflow-worker` 子进程均存活至 8 秒观察点，随后 SIGTERM 在
0.011 秒内受控退出，未发现立即崩溃、stderr 输出或本次 Worker 残留。证据见
[desktop-app-startup-audit.json](../baselines/v2.6/v3-recheck-20260927/desktop-app-startup-audit.json)。
该审计只覆盖主程序启动、Worker 拉起和受控退出，不包含 UI 点击或完整业务回归。

补充的 `.app` UI smoke 已完成启动、目录填写和 36 张素材分类，确认页正常显示；本次未完成“确认并开始”后的生成，输出为 0，且进程已受控终止。该边界记录在
[desktop-app-ui-audit-20260927.json](../baselines/v2.6/v3-recheck-20260927/desktop-app-ui-audit-20260927.json)，不把确认页误报为 `.app` 业务生成通过。

## 当前退出条件

- D/E：真实素材已登记，20/20 Pose、20/20 Seg 和 60 个 typed 计划已有 v3 原生现场证据，`multiple_people` 按范围跳过；四类输出覆盖报告已补齐，180 个输出 typed 校验通过。
- G：正式 onefile 和 `.app` 内 Worker 已在允许 semaphore 的宿主权限下通过启动、分类、
  v3 生成、缺失模型回退、受控回退和退出；`.app` 内 Worker 与重建 Worker 哈希一致。
