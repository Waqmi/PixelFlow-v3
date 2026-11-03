# PixelFlow v3 当前状态

更新日期：2026-11-03
版本：`3.0.0`（`apps/desktop/package.json`、`src-tauri/tauri.conf.json`）
源码：`main` @ `314e59f`

## 当前结论

- **单一主线多平台构建已生效**：`main` push 触发 6 个 job（Desktop checks、Worker tests ×2、macOS arm64 / macOS x64 / Windows x64 构建），三端从同一 commit 出包，产物上传为 Actions artifact；推 `v*` tag 才走 Release job。
- 最近一次完整 CI（`314e59f`，run `41415505474`）**六个 job 全部成功**，Release job 因未推 tag 未触发。
- 规则版本 `v3-composition-11`，四处一致（`worker.py`、`model_cache.py`、`App.vue`、`test_shoe_composition.py`）。
- 辅助窗口生命周期改造已合入（`8f64c00`、`729f623`、`314e59f`）：关闭改为隐藏复用、`settings-ready` 握手、标题栏关闭请求拦截、隐藏期间暂停确认页预览、Windows release 使用 GUI 子系统。**但这三项只有源码与 CI 证据，没有实机验收**，见下方"进行中"。
- 分类并发上限已从 4 提到 8（`4e97eaf`），设置页文案同步；渲染上限保持 4。
- 本机 `dist/PixelFlow-v3.0.app` 生成于 2026-10-30，**不含 11-01 之后的改动**，不能代表当前源码；当前源码包以 CI artifact 为准。

## 进行中

| 项目 | 状态 | 证据 / 缺口 |
|---|---|---|
| Intel Big Sur 崩溃复验 | ⏳ 待实机 | 已定位为 Wry 自定义 scheme 任务在 `WKWebView dealloc` 期间清理导致的 `EXC_BAD_ACCESS`；之前那份崩溃报告的可执行文件 UUID 与 `729f623` x64 产物不一致，不能作为验证。需要当前 x64 包在 Big Sur 上跑确认/保存/退出。 |
| 设置页保存后重开（ARM / Windows） | ⏳ 待实机 | 握手与焦点逻辑已合入，`protocol:check`、`npm run build` 通过；尚未在真实 App 上点验。 |
| Windows release EXE 的 GUI 子系统 | ⏳ 待验证 | `windows_subsystem = "windows"` 已加，Windows x64 构建成功；尚未对产物做 PE 子系统检查。 |
| 设置页蓝色横条 | ⏳ 待复现 | 用户确认窗口失焦即消失，倾向老 WebKit 整窗焦点环；当前 CSS 无对应规则，等匹配的 Big Sur 包复现后再动。 |

## 性能现状（2026-11-01 实测，未重新测）

- **渲染**：线程池上限 4 是拐点，CPU/墙钟最高约 2.1–2.4，再加线程无效（受 GIL 限制）。批次 C（18 来源 / 130 输出）4→6 线程再快约 8%，峰值内存未测。
- **分类**：上限已提到 8。36 张素材实测 1.10s（4 线程）→ 0.80s（8 线程），端到端未复测。
- **PNG 编码**：宽松分级可省约 23% 时间，但会改变输出文件字节，属产品决策，未采纳。
- **模型推理**：Pose/Seg 逐张串行；macOS 进程内推理按内存取 1/2/4 并行，Windows 的模型子进程仍串行。
- 结果里的 `performance.total_seconds` 是各阶段累计，不是墙钟；真实耗时看 `wall_seconds`。

## 待办与阻塞

1. **实机验证**：Intel Big Sur、ARM/Windows 设置重开、Windows PE 子系统。这是当前唯一阻塞主线收口的项。
2. **发布**：Developer ID 签名、公证、DMG 正式分发、Windows 代码签名均未配置；首版 Release 只能是未签名或 ad-hoc 签名。
3. **功能对齐**：`docs/v2.6-feature-parity.md` 仍有未收口条目。
4. **前端工程**：views 拆分与 Vitest 未做（当前环境装不了新 npm 依赖）。
5. **旧分支**：`fix/windows-*`、`integration/*`、`merge/*` 按单一主线规则只作历史参考，可择机清理。

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

2026-11-03 本机（源码 `314e59f`，工作区无代码改动）：

- Worker 全量：`PYTHONPATH=worker worker/.venv/bin/python -m unittest discover -s worker/tests -p 'test_*.py'` → **210 项，209 通过 + 1 跳过**（跳过为外部素材/模型缺失的既有跳过）。
- `npm run protocol:check`：通过，16 类消息解析正常。
- `npm run build`：`vue-tsc --noEmit` + Vite 生产构建通过（34 modules，JS 178.09 kB）。
- CI run `41415505474`（`314e59f`）：Desktop checks、Worker tests（macos-14 / windows-2022）、macOS arm64、macOS x64、Windows x64 全部成功。

历史验证记录见 `baselines/` 与 `.planning/` 下的各期记录，不再在此重复。

## 历史任务表（2026-09-27 制定，现状已更新）

| 项目 | 现状 |
|---|---|
| T1 仓库整理 | 进行中；构建产物已改为 Actions artifact，不再回提交源码 |
| T2 `.app` 完整回归 | 2026-10-03 用户人工验收通过；10-30 之后有改动，未重做全量回归 |
| T3 legacy 基线 | 通过，旧机器报告已归档 |
| T4 预览与安全 | 已实现（有界缓存、canonical scope）；桌面首屏性能未单独验 |
| T5 Host 稳健性 | 已实现（stderr 滚动日志、重启预算与退避、版本握手）；Windows 故障注入待实机 |
| T6 功能对齐 | 进行中 |
| T7 onedir / Python 3.12 | CI 已用 3.12 完成三端构建；本机仍为 3.14.5 |
| T8 复核与回退可见性 | 已实现 |
| T9 前端和 CI | CI 完成；views 拆分与 Vitest 未做 |
| T10 发布 | 受签名/公证/Windows 环境阻塞 |
| T11 legacy 周期 | 观察中，保留一个兼容周期后再评估删除 |
