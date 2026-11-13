# PixelFlow v3 当前状态

更新日期：2026-11-13
版本：`3.0.0`（`apps/desktop/package.json`、`src-tauri/tauri.conf.json`）
源码：`main` @ `e12cb50`

## 当前结论

- **单一主线多平台构建已生效**：`main` push 触发 6 个 job（Desktop checks、Worker tests ×2、macOS arm64 / macOS x64 / Windows x64 构建），三端从同一 commit 出包，产物上传为 Actions artifact；推 `v*` tag 才走 Release job。
- **Runner 标签**（2026-11-12 起）：`macos-15` = arm64（Worker 测试、Desktop checks、arm64 构建）、`macos-15-intel` = x64 构建、`windows-2022` = Worker 测试 + Windows 构建、`ubuntu-latest` = Release。原 `macos-14` 已被 GitHub 下线，当天 arm64 job 曾以 `steps=0`、报 `The configuration 'macos-14-arm64-us-default' is not supported` 秒失败，换标签后恢复。
- 最近一次完整 CI（`e12cb50`，run `43386157947`）**六个 job 全部成功**，Release job 因未推 tag 跳过。
- 规则版本 `v3-composition-11`，四处一致（`worker.py`、`model_cache.py`、`App.vue`、`test_shoe_composition.py`）。
- 辅助窗口生命周期改造已合入（`8f64c00`、`729f623`、`314e59f`）：关闭改为隐藏复用、`settings-ready` 握手、标题栏关闭请求拦截、隐藏期间暂停确认页预览、Windows release 使用 GUI 子系统。**但这三项只有源码与 CI 证据，没有实机验收**，见下方"进行中"。
- **分类并发改为三档**（`App.vue` 的 `classificationConcurrency`）：保守 = 1 线程、自动 = `min(8, ⌊核数/2⌋)`、高性能 = `min(8, 核数)`；预览并发按档位取 2 或 4。渲染线程上限保持 `min(4, 素材数)`。
- **白底图尺度归一 + 细节图手动缩放已合入**（`21cb238`）：品类级 `normalize` 目前只对 `shoes` 启用（`reference_aspect` 2.09、`strength` 1.0），其余 9 个品类 `enabled=false`，`other` 不配置；细节图滑块范围 -40~0、只缩不放。非启用品类（长裤、长袖、帽子、袜子、手套）各抽 3–4 张，改动前后**逐字节相同**。
- 本机 `dist/PixelFlow-v3.0.app` 生成于 2026-10-30，**不含 11-01 之后的改动**，不能代表当前源码；当前源码包以 CI artifact 为准，成品包统一落在 `~/Desktop/PixelFlow/01_当前成品App/`。

## 进行中

| 项目 | 状态 | 证据 / 缺口 |
|---|---|---|
| Intel Big Sur 崩溃复验 | ⏳ 待实机 | 已定位为 Wry 自定义 scheme 任务在 `WKWebView dealloc` 期间清理导致的 `EXC_BAD_ACCESS`；之前那份崩溃报告的可执行文件 UUID 与 `729f623` x64 产物不一致，不能作为验证。需要当前 x64 包在 Big Sur 上跑确认/保存/退出。 |
| 设置页保存后重开（ARM / Windows） | ⏳ 已修复，待实机 | 根因是 `capabilities/main.json` 缺 `core:window:allow-show` / `allow-set-focus`：窗口改为隐藏复用后，复用分支的 `show()` 被 ACL 拒绝且被 `catch` 吞掉。`8b1278c` 已补权限，报告/确认窗口同源问题一并解决。 |
| Windows 图标 / 打开输出文件夹 | ⏳ 已修复，待实机 | `icon.ico` 停留在 09-13 的旧扁平图标，已按当前品牌图重生成（`a6ece63`）；输出路径改用平台分隔符、`explorer` 前统一为反斜杠并修正退出码误判（`8b1278c`）。 |
| Windows release EXE 的 GUI 子系统 | ⏳ 待验证 | `windows_subsystem = "windows"` 已加，Windows x64 构建成功；尚未对产物做 PE 子系统检查。 |
| 设置页蓝色横条 | ⏳ 待复现 | 用户确认窗口失焦即消失，倾向老 WebKit 整窗焦点环；当前 CSS 无对应规则，等匹配的 Big Sur 包复现后再动。 |

## 性能现状（2026-11-01 实测，未重新测）

- **渲染**：线程池上限 4 是拐点，CPU/墙钟最高约 2.1–2.4，再加线程无效（受 GIL 限制）。批次 C（18 来源 / 130 输出）4→6 线程再快约 8%，峰值内存未测。
- **分类**：改为三档后上限 8（自动档按核数取半）。11-01 实测 36 张素材 1.10s（4 线程）→ 0.80s（8 线程），三档口径下端到端未复测。
- **PNG 编码**：宽松分级可省约 23% 时间，但会改变输出文件字节，属产品决策，未采纳。
- **模型推理**：Pose/Seg 逐张串行；macOS 进程内推理按内存取 1/2/4 并行，Windows 的模型子进程仍串行。
- 结果里的 `performance.total_seconds` 是各阶段累计，不是墙钟；真实耗时看 `wall_seconds`。

## 待办与阻塞

1. **实机验证**：Intel Big Sur、ARM/Windows 设置重开、Windows PE 子系统。这是当前唯一阻塞主线收口的项。
2. **发布**：Developer ID 签名、公证、DMG 正式分发、Windows 代码签名均未配置；首版 Release 只能是未签名或 ad-hoc 签名。mac 包解压后仍需手工 `codesign --force --deep --sign -` 才能通过 Gatekeeper。
3. **功能对齐**：`docs/v2.6-feature-parity.md` 剩 2 项未收口（JOB-04 失败报告、JOB-06 增量缓存），均标 🔄，都需要完整 `.app` 人工回归才能转 ✅。
4. **前端工程**：views 拆分与 Vitest 未做（当前环境装不了新 npm 依赖）。
5. **细节图滑块 0% 的显示方向待定**：0 是 -40~0 的最大值，所以滑块停在最右、整条填充；若希望"0 在左端、往右拖才缩小"，需把显示轴反转为 0~40，存储值仍保持 -40~0。
6. ~~旧分支清理~~ **已完成**：远端只剩 `main` 与 `origin/ci/v3.0-platform-builds-20261006`，本地只剩 `main`；STATUS 早先列出的 `fix/windows-*`、`integration/*`、`merge/*` 已不存在。

## 版本与模型

- 目标平台：macOS arm64、macOS x64、Windows x64，均由 `main` 同一 commit 构建。
- CI 与 Worker 测试使用 Python 3.12；本机 `worker/.venv` 为 3.14.5。
- ONNX 模型不入 Git、不打包，通过设置页指定目录；缺失时 Worker 回退到规则判断。SHA-256 记录在 `models/README.md`。

## 证据约定

- 生成图片和构建目录保持忽略，不进入提交。
- JSON 报告、协议、测试、源码和文档进入版本控制。
- 手工 UI 证据必须使用只含源素材的临时输入目录，不能直接使用包含历史成品的目录。
- 静态检查不等于实机验证，Intel / Windows 的限制必须如实标注。

## 最近验证

2026-11-13 本机（源码 `e12cb50`，工作区无代码改动）：

- Worker 全量：`PYTHONPATH=worker worker/.venv/bin/python -m unittest discover -s worker/tests -p 'test_*.py'` → **232 项，231 通过 + 1 跳过**（跳过为外部素材/模型缺失的既有跳过）。
- `npm run protocol:check`：通过，16 类消息解析正常，UI guards 通过。
- `npm run build`：`vue-tsc --noEmit` + Vite 生产构建通过（`index-Da0E5B1k.css` 53.93 kB、`index-BWkOLXjC.js` 180.63 kB）。
- CI run `43386157947`（`e12cb50`）：Desktop checks、Worker tests（macos-15 / windows-2022）、macOS arm64、macOS x64、Windows x64 全部成功。
- CI run `43352120873`（`4ce41b5`）：同上六项全部成功，三端包已下载到 `01_当前成品App/` 并通过 9 个文件的 SHA256 校验。

2026-11-12 修复轮（`21cb238`、`0867dd8`）：

- 白底图尺度归一（仅 shoes）+ 细节图手动缩放两阶段合入，`worker/tests` 新增 8 个测试；非启用品类逐字节不变已逐张比对。
- 细节图滑块"整条蓝 + 滑块居中"根因是 range 绑到 `scaleAdjustments[source]` 拿到空值，浏览器把空值滑块画在中点；改为 `:value="... ?? 0"` + `@input`，记录保持稀疏以免污染旧引擎 manifest 的复用指纹。
- CI run `43197579198`（`2b01541`）：`macos-14` → `macos-15` 后 arm64 job 首次拿到 runner，16 个 step 全绿（含 `uname -m = arm64` 断言）。

2026-11-03 本机（源码 `314e59f`，工作区无代码改动）：

- Worker 全量：210 项，209 通过 + 1 跳过。
- `npm run protocol:check`：通过，16 类消息解析正常。
- `npm run build`：`vue-tsc --noEmit` + Vite 生产构建通过（34 modules，JS 178.09 kB）。
- CI run `41415505474`（`314e59f`）：六个 job 全部成功（当时 Worker 测试运行在 `macos-14`，该标签 2026-11-12 起改为 `macos-15`）。

历史验证记录见 `baselines/` 与 `.planning/` 下的各期记录，不再在此重复。

2026-11-03 修复轮（`8b1278c`、`a6ece63`）：

- 补齐 `core:window:allow-show` / `allow-set-focus`，恢复设置、报告、确认窗口的二次打开。
- 按当前品牌图重生成 `icon.ico`（16–256 七个尺寸，中心为蓝色 `#2876FD`）。
- Windows 打开输出目录：路径按平台分隔符拼接、`explorer` 前统一反斜杠、不再把退出码 1 判为失败。
- 验证：`cargo test` 14 项通过（含新增 `windows_shell_path` 用例）、`npm run protocol:check` 通过、`npm run build` 通过。三项均未做真机点验。

## 历史任务表（2026-09-27 制定，现状已更新）

| 项目 | 现状 |
|---|---|
| T1 仓库整理 | 进行中；构建产物已改为 Actions artifact，不再回提交源码 |
| T2 `.app` 完整回归 | 2026-10-03 用户人工验收通过；10-30 之后有改动（含构图归一、细节图缩放、滑块修复），未重做全量回归 |
| T3 legacy 基线 | 通过，旧机器报告已归档 |
| T4 预览与安全 | 已实现（有界缓存、canonical scope）；桌面首屏性能未单独验 |
| T5 Host 稳健性 | 已实现（stderr 滚动日志、重启预算与退避、版本握手）；Windows 故障注入待实机 |
| T6 功能对齐 | 进行中；剩 JOB-04、JOB-06 待人工回归 |
| T7 onedir / Python 3.12 | CI 已用 3.12 完成三端构建；本机仍为 3.14.5 |
| T8 复核与回退可见性 | 已实现 |
| T9 前端和 CI | CI 完成（含 macos-15 标签迁移）；views 拆分与 Vitest 未做 |
| T10 发布 | 受签名/公证/Windows 环境阻塞 |
| T11 legacy 周期 | 观察中，保留一个兼容周期后再评估删除 |
