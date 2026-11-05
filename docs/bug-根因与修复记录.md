# Bug 根因与修复记录

用途：把已经踩过的坑固化下来，避免同一个问题反复修。**改代码前先读第二节的自检清单**；修完 bug 在第一节索引表追加一行，必要时补一个小节——**只改代码不更新这份文档，视为修复未完成**（`CLAUDE.md` 第 11 条）。

记录原则：

- 只写能验证的事实（commit、文件、实测数据），不写推测。
- 根因写到"为什么之前没发现/为什么上一轮没修好"，这一句比修复本身更有用。
- 现象按用户看到的描述写，方便以后搜索。

---

## 一、索引

| 日期 | 现象 | 根因类别 | 一句话根因 | 修复 |
|---|---|---|---|---|
| 2026-11-05 | **点「选择模型目录」卡死，随后双击打不开 App** | 线程与窗口 | 同步命令占主线程起 PowerShell 弹窗；无 owner 的对话框落到窗口后面；强制退出留下孤儿 WebView2 锁住用户数据目录 | 见 3.16 |
| 2026-11-05 | **Windows 首次安装 WebView2 无进度**，看起来像卡死 | 打包配置 | `webviewInstallMode` 未配置，默认 `silent: true`，微软 bootstrapper 在后台静默跑 | 见 3.15 |
| 2026-11-05 | **分类进度条与实际进度不成比例**，快结束时才一半 | 进度权重 | 权重按阶段写死（40/30/29），与实测耗时（9%/89%/0%）完全错位 | 见 3.14 |
| 2026-11-05 | **Windows 交互动画全部消失**，但设置里的「关闭动画」未勾选 | 样式与状态脱节 | 两条 `@media(prefers-reduced-motion:reduce)` 全局规则强制清零动画；App 开关只读 localStorage，两者不通 | 见 3.13 |
| 2026-11-05 | **Windows 确认素材页一片空白**，但生成正常 | 渲染依赖 | 卡片默认 `opacity:0`，只靠 IntersectionObserver 置 `data-visible` 才显示；隐藏窗口里观察器判定全不可见且不重算 | 见 3.12 |
| 2026-11-05 | **Windows「加载测试」失败 / 生成卡在 Pose**（安装路径含中文） | 冻结包子进程 | `spawn` 的子进程死在 `pyiboot01_bootstrap`：中文路径把 `base_library.zip` 的路径塌缩成 `Cbase_library.zip`，stdlib 读不到 | 见 3.11 |
| 2026-11-05 | **macOS Intel 确认页底部出现灰色遮挡带**（ARM 正常） | 渲染兼容 | 底部滚动提示的 `linear-gradient(..., transparent)` 在旧 WebKit 上向黑色插值，渐变变成灰色纱罩 | 见 3.10 |
| 2026-11-05 | **重启后仍显示上次未完成任务的路径**，改路径再点开始也不生效 | 状态恢复 | 启动时无条件把恢复快照的 `input_root`/`output_root` 写进输入框，旧路径覆盖用户选择，且「旧值」与「刚选的值」在界面上无法区分 | 见 3.9 |
| 2026-11-04 | **Windows 点「开始生成」完全没反应**、设置页「测试模型」一直检测中 | 错误关联 | Worker 解析失败时回 `request_id: "unknown"`，前端无法匹配就丢弃整条错误，`isRunning` / `modelProbePending` 永远不清空，后续点击被「已在运行」守卫静默拦下 | 见 3.8 |
| 2026-11-04 | **Windows 退出 App 后删不掉安装目录**，任务管理器里残留 2 个进程（`PixelFlow.exe` + `pixelflow-worker.exe`） | 窗口生命周期 | 辅助窗口关闭时只 `hide()` 不销毁，隐藏窗口仍占窗口表；Tauri 只在窗口表为空时才退出，于是主窗口关了进程却不退，Worker 跟着活，两个 exe 一直被锁 | 见 3.7 |
| 2026-11-03 | 设置窗口关掉后再点没反应（三端） | Tauri ACL | 复用窗口要调 `show()`/`setFocus()`，但 capability 从未授予这两个权限 | `8b1278c` |
| 2026-11-03 | Windows 打包图标是旧版 | 资源同步 | 换品牌只换了 `icns`/`png`，`icon.ico` 停留在 09-13 的旧图 | `a6ece63` |
| 2026-11-03 | Windows 打开输出文件夹打开错目录 | 跨平台路径 | 前端用 `/` 拼 Windows 路径得到混合分隔符，原样交给 explorer | `8b1278c` |
| 2026-11-03 | Windows 打开 App 附带控制台黑窗，关掉黑窗 App 一起退出 | Windows 子系统 | 主 exe 是控制台子系统（PE `Subsystem=3`）；`windows_subsystem` 属性 11-02 才进主仓，用户手上的 10-31 包早于它 | `314e59f` + CI 断言 |
| 2026-11-03 | 设置页「测试模型」等很久 / 检测不到 / 一直"检测中"（Intel、Windows） | 探测成本 | `probe` 会真正加载三个 ONNX 会话并对每个模型全量算 SHA-256；macOS 在 Worker 进程内跑、Windows 还要 spawn 冻结子进程；前端互斥锁 30s 短于后端 180s 超时 | `0ef9bf1`（两级探测）；编码问题另见 3.6 |
| 2026-11-03 | **Windows 点「测试模型」完全没反应**，等多久都没结果（ARM 正常） | 协议编码 | Worker 按系统代码页（cp936）输出中文，Rust 按 UTF-8 读取失败后被 `.lines().flatten()` 静默丢弃，前端永远收不到 `done`/`error` | 已修（见 3.6） |
| 2026-11-02 | 设置窗口保存后重开空白/失败 | 窗口生命周期 | 复用隐藏窗口时依赖 `isVisible()`，跨 WebView 事件存在竞态 | `314e59f` |
| 2026-11-02 | macOS 关闭辅助窗口时崩溃 | 窗口生命周期 | 在窗口自己的 JS 里 `close()`，WebKit 在 dealloc 时访问已释放页面 | `8f64c00` |
| 2026-11-01 | 生成进度会往回跳 | 进度语义 | 不同阶段 `completed/total` 会重置，前端插值器还接受更低目标 | `0ce3e2e`、`beedb34`、`134883e` |
| 2026-11-01 | Windows 检出/测试失败 | 测试可移植性 | 测试直接调用 `os.sysconf`，Windows 没有该接口 | `7c0e66a` |
| 2026-11-01 | 测试 import 失败 | 模块加载顺序 | `legacy_engine` 用扁平 import，模块级 import 时 `sys.path` 还没就绪 | `04618a8` |
| 2026-10-31 | Windows 便携版起不来 Worker / 冒烟测试误判 | 打包与验证 | 打包 Worker 可执行文件查找无回退；冒烟测试用正则数 `ready` 次数，无法区分 worker ready 与 ping ready | `327ea1a` |
| 2026-10-30 | VIP 输出大量重复图（`_2` 后缀成对） | 输出正确性 | VIP 例程递归扫描整个输出树并拍平，同名冲突自动加后缀 | `134883e` |
| 2026-10-01 | macOS 文件选择器不可用/窗口白闪 | 原生集成 | `osascript` 选择器不可靠；窗口未设背景色 | `e06c722` |
| 2026-09-27 | 设置页模型显示未就绪/无 SHA | 参数未转发 | `file_version()` 没把 `include_digest` 透传给 `_file_version()` | `3359911` |

## 二、改代码前自检清单

### Tauri / 桌面宿主

1. **调用任何 `window.*` / `webview.*` API 前，先在 `apps/desktop/src-tauri/capabilities/main.json` 里确认权限。** `core:default` 只给 getter 类命令（`is-visible`、`theme`、`get-all-webviews` 等），`show`、`set-focus`、`hide`、`close`、`destroy`、`set-theme` 都要单独授予。
2. **权限失败不会报错弹窗**。JS 侧是 Promise reject，没写 catch 就变成静默失败；写了 catch 只 set 一行状态文字，用户仍然觉得"点了没反应"。改窗口相关逻辑时，失败路径必须可见。
3. **辅助窗口（settings / report / confirm）是三条并行链路**。改其中一条的显示/隐藏/焦点逻辑，另外两条大概率同病。历史上四次窗口问题都是同一个模式复制出来的。
4. **不要在窗口自己的 JS 里销毁自己**（`close()`/`destroy()`）。macOS 上会撞 wry#1730：WebKit 在下一轮 runloop 访问已释放的页面。需要"关掉"就 `hide()`。
5. **跨 WebView 的握手不要依赖 `isVisible()` 之类的瞬时状态**。事件监听注册是异步的，用"子窗口注册完成后回发 ready"这类显式握手。

### 跨平台路径与外部命令

6. **不要用字符串拼路径分隔符**。Windows 下 `C:\a` + `/` + `b` 会得到混合分隔符：`PathBuf::is_dir()` 能容忍，但 `explorer.exe` 不一定，表现是打开父目录或"此电脑"，而不是报错。
7. **`explorer.exe` 成功时退出码是 1**。用 `status.success()` 判定会误报失败，Windows 分支不要校验退出码，只校验进程能否启动。
8. **平台差异用 `sys.platform` / `#[cfg(target_os = ...)]` 隔离**，不另开平台分支。测试代码同样要跨平台（`os.sysconf` 只在 Unix 存在）。

### 资源与品牌

9. **换品牌图时三件套一起换**：`icons/icon.icns`（macOS）、`icons/icon.ico`（Windows exe / NSIS / portable 启动器）、`icons/icon.png`（Linux 与运行时）。只换 icns 会导致 macOS 新、Windows 旧。
10. **`icon.ico` 必须是多尺寸**（16/24/32/48/64/128/256），从 1024px 源图导出，别手工缩放单帧。

### 构图、规则与缓存

11. **规则版本四处同步**：`worker.py` 的 `RULES_VERSION`、`model_cache.py` 的 `COMPOSITION_RULES_VERSION`、`App.vue` 的 `expectedRulesVersion`、`test_shoe_composition.py` 的断言。漏一处会导致旧缓存被复用或前端报版本不匹配。
12. **判断"是否修好"之前先清缓存并勾选强制重跑**。v3 同名输出默认复用，看起来"没变化"经常是复用了旧结果，不是没修。
13. **Worker 测试全绿 ≠ 路径被覆盖**。外部模型/素材缺失时 `test_classification.py`、`test_engine.py`、`test_shoe_composition.py`、`test_worker_process.py` 会 skip。

### 输出正确性

14. **任何"汇总/补生成"类例程的输入必须是本批产物**，不能递归扫描输出目录，否则会把历史输出和汇总副本再吃一遍，产生同名 `_2` 重复。
15. **去重按解码后的像素哈希**，不要按文件名或文件字节——同名不同图要保留，不同名同图要去重。
16. **进度只允许单调递增**，100% 只留给 Worker 最终的 `done` 事件。阶段切换、回退 legacy 都不能让目标值下降。

### 仓库卫生

17. **`git add` 只加本次改动的文件**，不要 `git add -A`。历史上有两次误提交：`:memory:.ses`（加进 `.gitignore` 但没 `git rm --cached`，所以第一次"修完"其实没生效）和 `.planning` 笔记。
18. **`.gitignore` 不影响已跟踪文件**。要停止跟踪必须 `git rm --cached`，两步都做才算完。

### 构建与产物

19. **源码修了不等于产物修了**。用户"装了新包还复现"时，先核对那个包对应的 commit（`build-metadata-*.json`、包内 Worker 的 sha256），再回源码找问题。Windows 控制台黑窗和旧图标两个 bug 都是"源码已修、包里没有"。
20. **能用产物属性自动断言的，别留给人眼验收**。PE `Subsystem`（应为 2）、`icon.ico` 的尺寸档位、包内 Worker 哈希，都能在 CI 里直接读文件判定；`v3.yml` 的 `Assert GUI subsystem (no console window)` 就是这条的落地。

### 模型与推理

21. **设置页的"检测/测试"不能做重活**。`probe_models` 的默认路径只做 `glob` + `.classes.json` + 文件大小/时间（状态 `已找到`）；真正加载模型是 `deep=True` 的「加载测试」。别再往默认路径里塞会话创建或全量哈希。

### 跨进程通信（Worker 协议）

22. **协议流必须显式声明 UTF-8，不能依赖进程 locale**。Worker 侧 `reconfigure(encoding="utf-8")` 或 `PYTHONIOENCODING=utf-8` 二选一，最好都做；Rust 侧按 UTF-8 读。`Message.to_json()` 用的是 `ensure_ascii=False`，中文是真实字节，管道场景下 Windows 会按 cp936 输出。详见 3.6。
23. **读取端不要用 `.lines().flatten()` 这类静默容错**。解码失败要留痕（写 `worker.log`）并转成前端可见的 `error`，否则"传输失败"会被误判成"对方没回"，排查方向直接跑偏。同理，前端等待响应的 Map 必须有超时释放，不能让一次丢行永久卡死界面。

### 进程与退出

24. **辅助窗口用 `hide()` 复用，就必须显式处理「主窗口关闭后退出」**。Tauri 只在窗口表为空时才退出，隐藏窗口仍在表里。验收动作固定为：打开设置页 → 关闭设置页 → 关闭主窗口 → **进程必须消失**（任务管理器 / `pgrep` 各看一次）。只看「窗口关了」不算通过。
25. **`os._exit()` 跳过所有 `atexit`**。凡是靠 `atexit` 注册的清理——包括 `multiprocessing` 的 daemon 子进程回收——在 `os._exit` 前都要显式调用，否则子进程会被孤儿化并锁住产物。

26. **错误响应必须能被关联回原请求**。回 `request_id: "unknown"` 等于把错误丢进黑洞：前端匹配不上就整条丢弃，等待中的状态永远不会复位。解析失败时也要尽力从原始文本里抠出 `request_id`，并把原始行截断回显——否则这类问题只能靠猜。
27. **前端每个「等待中」标记都要有超时释放**，包括 `isRunning` 这种看似不会丢的。守卫写成 `if (running) return` 时，一次丢失的响应就等于永久禁用，而且**不报错、不改状态**，用户只会说「点了没反应」。

## 三、重点案例

### 3.1 辅助窗口：四轮才修完

| 轮次 | 做法 | 结果 | 为什么不够 |
|---|---|---|---|
| `8f64c00` | 关闭改成 `hide()` + 200ms 后 `destroy()` | macOS 仍崩溃 | 销毁还是发生了，wry#1730 只是被延后 200ms |
| `729f623` | 去掉 `destroy()`，改为隐藏复用，复用前 `show()` | 三端"关掉再点没反应" | 隐藏复用让 `existing` 分支第一次被真正执行，而 `show()` 一直没有权限 |
| `314e59f` | 加 `settings-ready` 握手，复用后重新推送快照 | 逻辑对了，仍然打不开 | 没意识到失败点在 ACL，不在握手时序 |
| `8b1278c` | 补 `core:window:allow-show`、`allow-set-focus` | 待实机验证 | — |

教训：前三轮都在改"时序"，没人验证"这个 API 到底有没有被允许调用"。**改窗口逻辑时，先把 `show/hide/focus/close/destroy` 五条路径的权限逐条对一遍**，再看时序。

### 3.2 VIP 重复输出

- 现象：`唯品专享1440/无Logo` 下大量 `xxx.jpg` 与 `xxx_2.jpg` 字节完全相同。
- 根因：VIP 例程递归扫描整个输出树，把分类目录和汇总目录的副本拍平到同一个目标目录；平面目标名冲突时自动加 `_2` 后缀，于是同一张图存了两份。
- 修复：VIP 输入限定为**当前批次**渲染产物；去重按解码像素 SHA-256，保留同名不同图。
- 教训：批量产物目录里同时存在"分类副本"和"汇总副本"时，任何二次收集都必须显式限定范围。

### 3.3 Windows 打开输出文件夹

- 现象：生成完成后点"打开输出文件夹"，打开的是父目录或"此电脑"，不是输出目录。
- 根因两条叠加：
  1. `suggestedOutputRoot()` 用 `${parent}/${name}主图` 拼字符串，Windows 下得到 `C:\Users\x\Desktop/冲锋裤主图`。`PathBuf::is_dir()` 通过，`explorer` 解析失败。
  2. `open_path()` 用 `status.success()` 判定，而 `explorer.exe` 成功时返回 1，会被误报为失败。
- 修复：路径按自身分隔符拼接；Windows 分支统一转反斜杠后再调用；不校验 explorer 退出码。
- 教训：路径是"数据"，不是"字符串"。凡是要交给外部程序（explorer、open、osascript、PowerShell）的路径，先规范化。

### 3.4 Windows 控制台黑窗：修了两次，第二次才到产物

- 现象：Windows 双击打开 App，旁边多一个黑色控制台窗口；**关掉这个黑窗，App 一起退出**。macOS 没有这个现象。
- 判据：Windows 关闭控制台窗口只对"挂在该控制台上"的进程发 `CTRL_CLOSE_EVENT`。所以关黑窗能带走 App，说明黑窗是 **App 自己的**控制台，不是子进程的。
- 排除过程（都验证过，别再重复怀疑）：
  - Worker：spec 里 `console=True`，但 `spawn_worker` 调 `hide_console()` → `CREATE_NO_WINDOW`（`main.rs:872-877`、`991`），不会弹窗。
  - Worker 死掉：只发 `worker-exit` + 最多重启 3 次（`main.rs:919-944`），主窗口不会退出。所以"黑窗归 Worker"这条解释不成立。
  - portable / setup 启动器：四个版本的安装包 PE `Subsystem` 都是 2（GUI）。
  - PowerShell 文件对话框：两处都走了 `hide_console`。
- 根因：主 exe 编译成控制台子系统。Rust 在 Windows 上默认就是控制台子系统，必须靠 `#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]`（`main.rs:1`）切到 GUI。
- 为什么"修过又出现"：这条修过两次，但**两次都没到用户手上**。
  1. 10-18 `PixelFlow-App-v3-windows-sync` 仓库的 `9558cb7 fix: hide Windows app console and refresh icon` 就加了属性、也换了 ico，但那笔改动从没合回主仓。
  2. 11-02 主仓 `314e59f` 才独立加了同样的属性。而 10-31 的交付包 `d245bf38` 早于它——交叉验证：那个包的图标还是旧的，而同步仓 10-18 已换过 ico，说明包是从主仓构建的，那棵树上既没有新图标也没有 GUI 子系统属性。
     - 该产物已于 2026-11-03 从 `01_当前成品App/` 删除（避免与新版混装），上述推理是当时的实测记录，如需复现请从 Actions 里取对应 run 的 artifact。
- 修复：`314e59f` 保留属性；`.github/workflows/v3.yml` 新增 `Assert GUI subsystem (no console window)` 步骤，构建后直接读 `target/x86_64-pc-windows-msvc/release/*.exe` 的 PE 可选头，`Subsystem != 2` 就让构建失败。
- 断言本身也踩过一次坑（首次上线 CI 即误报）：初版按位数分流，PE32+ 读 `+88`，结果对 GUI 产物报出 `Subsystem = 0`。**`Subsystem` 在 PE32 与 PE32+ 中都是可选头 `+68`**——PE32+ 删掉 `BaseOfData`（-4）同时把 `ImageBase` 从 4 字节加宽到 8 字节（+4），两者抵消，`ImageBase` 之后所有字段偏移不变。本地用 PE32 产物可自证：`+68 = 2`、`+88 = 0`。现在断言对 `magic` 只做合法性校验，偏移恒取 `+68`，并在报错里区分 `3`（真控制台）与其它值（多半是偏移算错）。
- 教训：**源码修了不等于产物修了**。凡是"用户装了包还复现"的问题，第一步先确认那个包的来源 commit，别在源码里再找一遍。另外，能用产物属性自动断言的（PE 子系统、图标尺寸、Worker 哈希），就别留给人眼验收。但断言写错会制造**假失败**，代价和假通过一样高——上线断言前先用已知正确/错误的样本各跑一遍。

### 3.5 「测试模型」很久 / 检测不到：探测成本与预算错配

现象：设置页「模型增强」添加模型目录后点「测试模型」，等很久，结果是"检测不到"或一直停在"检测中…"。ARM 上不明显，Intel 和 Windows 上常见。

已从代码确认的事实：

1. **`probe` 是重操作，不是存在性检查**。`ModelProcessService.probe_models` → `_request("probe")` 会遍历 `MODEL_PURPOSES`（pose、segmentation、**detection**），每个都 `_load_assistant()` 真正建 ONNX Runtime 会话，再用 `file_version(..., include_digest=True)` 把整个模型文件读一遍算 SHA-256（`model_cache.py` 的 `_file_version`）。三个模型加起来是数百 MB 的读取 + 三次会话创建。
2. **平台路径不同**。`model_service.py:301` 的 `self._direct = sys.platform == "darwin"`：macOS 直接在 Worker 进程内跑（TCC 原因），Windows 走 `multiprocessing.get_context("spawn")`，先起一个冻结的 Worker 子进程再执行。Windows 每次冷启动要多付一次"重新加载整个冻结包"的代价。
3. **时间预算不匹配**。`_request` 的超时是 `180s`；前端 `modelProbeLockTtlMs = 30_000`。主窗口在 Worker ready 时也会自动 probe 一次（`App.vue:1665`）。设置页的 probe 超过 30s 还没回来时，另一侧会抢到锁再发一次，而 Worker 侧 `_request` 用 `self._lock` 串行化，第二个只能排在第一个后面 → 等待翻倍；且 `modelProbeRequestId` 被覆盖后，第一个请求的 `done` 会被当成无关消息丢弃，界面上就是"一直检测中"。
4. **`detection` 也在 probe 里加载**，但设置页状态行只需要 pose/seg。这一步在慢机器上是纯浪费。

修复（`0ef9bf1`）：

- `probe_models(model_root, *, deep=False)` 拆两级。默认 `deep=False` 走 `_probe_presence()`：只做 `glob`、`.classes.json` 检查、文件大小/时间，**不导入推理运行时**，状态是新的 `已找到`（`loaded: false`）。`deep=True` 才走原来的加载路径，状态是 `已就绪`/`不可用`（`loaded: true`）。
- 轻量探测**不加载 detection**，只报 `已找到`；deep 才加载。
- SHA-256 改为**只对小模型算**（≤ 64MB，pose/seg 各约 6MB，实测毫秒级）。大模型交给 deep 检查。完全去掉会让设置页的 SHA 列变空——那正好是 `3359911` 修过的症状，所以保留必需模型的摘要。
- 前端互斥锁加**心跳续期**（每 10s 刷新 `startedAt`），TTL 仍是 30s：deep 加载再久也不会被另一个窗口抢锁，窗口崩了也只会阻塞 30s。
- 前端把单个 `modelProbeRequestId` 换成 `Map<requestId, root>`，晚到的 `done`/`error` 不再因为 id 被覆盖而丢弃。
- 设置页新增「加载测试」按钮（`probeModels(true)`），保留原来"真的加载一遍"的能力。

验证：`worker/tests/test_model_service.py` 5 项新增用例（轻量探测不建会话、空目录报未配置、detection 缺 `.classes.json` 报不可用、小模型仍有摘要、deep 标记 `loaded`）；`test_model_cache.py` 的 macOS 进程内用例改为分别断言轻量探测不 spawn、deep 探测仍走进程内。

未闭环：ARM 已实机验证正常（2026-11-03）。Windows 实机点「测试模型」仍然没有结果，但那是**另一个根因**——协议行编码，见 3.6。Intel 尚未实机验证。

### 3.6 Windows：中文协议行被静默丢弃（「测试模型」点了没反应）

现象：Windows 设置页添加模型目录后点「测试模型」，界面一直停在"测试中"，等多久都没有结果；重开设置页再点也一样。ARM 正常，Intel 尚未测。

**结论：不是模型文件、不是目录、不是锁，是协议行的编码。** Worker 按系统代码页（cp936）写出中文，Rust 按 UTF-8 读失败后把整行静默丢掉，前端永远收不到 `done` / `error`。

证据链（逐环都能在代码里核对）：

1. `contracts.py` 的 `Message.to_json()` 用 `ensure_ascii=False` —— 中文是**真实 UTF-8 字节**，不是 `\uXXXX` 转义。这是前提：如果转义成 ASCII，编码问题根本不存在。
2. `worker.py:570` 用 `print(line, flush=True)` 直接写 `sys.stdout`。
3. `spawn_worker`（`main.rs:986-998`）没有设置 `PYTHONIOENCODING`；`worker_entry.py` 也没有 `reconfigure(encoding="utf-8")`。
4. Worker 的 stdout 是**管道**（`Stdio::piped()`）。Python 对管道不使用控制台代码页，而是 `locale.getpreferredencoding(False)`：简体中文 Windows 上是 `cp936`。
5. Rust 侧 `read_line` 按 UTF-8 解码 → `Err(InvalidData)`。
6. `main.rs:1002` 是 `for line in BufReader::new(stdout).lines().flatten()` —— `flatten()` 把 `Err` 静默跳过。这行连 `append_worker_event` 都到不了，**日志里没有任何痕迹**。

本机实测（macOS 上手工构造同一条消息）：`ensure_ascii=False` 的 `done` JSON 在 cp936 下是 101 字节，UTF-8 解码在第 89 字节失败——正是「已」字的第一个字节；用 GBK 能完整还原。macOS 管道下 `sys.stdout.encoding` 是 `utf-8`，所以三端只有 Windows 会踩到。

**为什么时好时坏**：Windows 若开启「Beta: 使用 Unicode UTF-8 提供全球语言支持」，ANSI 代码页会变成 65001，问题消失；系统语言是英文同理。所以这个 bug 依赖机器的区域设置，不是每次必现。

**为什么上一轮（3.5）没修好**：3.5 改的是**探测要花多久**，没碰**结果怎么传回来**。`probe_models` 的 `done` 里 `state` 就是「已找到」/「已就绪」，中文，整行照旧被丢。改错了层。

**加重因素（3.5 引入）**：`App.vue:1006` 的 `if (modelProbePending.size) return`。`done` 收不到 → 这个 Map 永不清空 → 之后每次点击在第一行就 return，**请求根本发不出去**。只有重载页面或重启 App 能恢复（30s 的 localStorage 租约到期也没用，内存里的 Map 先拦住了）。所以现在用户看到的是"点了完全没反应"，而不是"转圈很久"。

**侧分支已有修复（2026-10-18，从未合回 main）**：

- `5106baf fix: force UTF-8 worker protocol on Windows` —— `worker_entry.py` 强制 UTF-8；Rust 改成 `read_line` 循环、读取失败不再静默；测试用 `PYTHONIOENCODING=cp936` + 中文目录名「模型目录-」复现本场景。
- `761411b fix: keep model probe independent from inference lock` —— 探测不走推理锁。3.5 的两级改造已覆盖快速路径（`_probe_presence` 直接读文件、不拿锁），但 `deep` 路径仍走 `_request` 拿锁。

**现场确认（不改代码）**：点「测试模型」后立刻看 `%LOCALAPPDATA%\PixelFlow\logs\worker-events.jsonl`：

- 该 `request_id` **一条记录都没有**（连 `status` 都没有）→ 编码问题坐实。因为 `status` 文案「正在检测外部模型…」也是中文，同样被丢。
- 有 `status` 但没有 `done` → 那是另一个问题（Worker 端抛异常），排查方向不同。
- `worker.log` 干净得反常，本身就是信号：编码错误不产生 Python 异常。

**教训**：**协议流必须显式声明编码，不能依赖进程 locale**。跨进程通信里"默认编码"等于"随机器而变"；而读取端的 `flatten()`、`unwrap_or_default()` 这类静默容错，会把传输故障伪装成"对方没回"，让人往模型、锁、超时的方向查——这次就是绕了整整一圈。

已修（2026-11-05）。五项全部落地：

1. `worker.py` 新增 `_configure_stdio()`，在 `main()` 第一行把 stdin/stdout/stderr 全部 `reconfigure(encoding="utf-8")`。放在 `worker.py` 而不是 `worker_entry.py`，是因为它同时覆盖打包产物和源码模式（`python -m pixelflow_core.worker`），本地调试也能复现。
2. `main.rs` 的读取循环从 `lines().flatten()` 改成 `read_until` + `String::from_utf8`：解码失败时把原始字节写进 `worker.log`，并向前端发一条 `request_id: "worker"` 的 error，不再静默丢行。
3. `spawn_worker` 补 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`，覆盖 Worker 派生的模型子进程。
4. `modelProbePending` 的 60s 超时（`4e917fb`）保留作兜底。
5. 回归测试 `test_worker_writes_utf8_even_when_the_locale_is_not`：用 `PYTHONIOENCODING=cp936` 起 Worker，断言探测回执可按 UTF-8 解码且含 `done`。

**2026-11-05 的现场证据（Windows，`bff0605`）**：

- `worker.log` 里出现 `<_io.TextIOWrapper name='<stdout>' mode='w' encoding='gbk'>` —— 直接读到 stdout 的编码。
- 该包 `worker-events.jsonl` 里，探测的 `status` 事件（文案「正在检测外部模型…」）一条都没有，而 `done` 有；`diagnostics.json` 的 `models` 为空、`model_status` 停在「模型检测超时，请重试」。
- 本机复现：同一条 `probe_models` 请求，`PYTHONIOENCODING=gbk` 时 stdout 在第 331 字节出现 `0xd5`（非法 UTF-8 续字节），`PYTHONIOENCODING=utf-8` 时两行全部合法。

**为什么之前一次复核会误判**：同一台机器上，19 小时前的会话里中文事件是完好的（115~139 条）。差别不在构建，而在**启动方式**——从终端启动时控制台代码页可能是 65001，双击启动则是 cp936。这正好印证了 3.6 的结论：编码依赖进程环境，而不是代码。凡是「同一版本时好时坏」的编码问题，都应该先看启动上下文。

> 不要把这个修复只放在 Windows 分支：编码是协议契约的一部分，三端共用同一份 Rust 与 Worker 代码。控制台黑窗（3.4）就是"修在侧分支、从未合回"的直接后果，别让同一剧本演第三次。

### 3.7 Windows 退出 App 后删不掉安装目录（进程常驻）

现象：Windows 上关掉 App 后想删安装目录，提示文件被占用；任务管理器里还剩 2 个进程——`PixelFlow.exe`（主程序本身）和 `pixelflow-worker.exe`。macOS 从未有人报过。

**结论：主程序压根没退出，Worker 只是因为它还活着所以活着。** 触发条件是「开过设置页 / 报告页 / 确认页」中的任意一个，所以只要用过「测试模型」就必然踩到。

证据链：

1. `App.vue:1407-1412` 辅助窗口（settings / report / confirm）在 `onCloseRequested` 里 `event.preventDefault()`，然后调 `closeSettings()` / `dismissMaterialConfirmation()` / `closeReport()`。窗口的关闭被取消了。
2. `App.vue:616-635` 这几个函数最终都走 `hideOwnWindow()` → `getCurrentWindow().hide()`。**窗口只是隐藏，从不销毁。**
3. `tauri-runtime-wry-2.11.4/src/lib.rs:4310-4324`：Tauri 只在 `TaoWindowEvent::Destroyed` 时把窗口从 `windows` 表里移除，并且只有 `windows.0.borrow().is_empty()` 为真才发 `ExitRequested`。`hide()` 不会移除表项。
4. 于是关掉主窗口后，表里还剩一个隐藏的设置窗口 → `is_empty()` 为 false → **`ExitRequested` 永不触发**。`main.rs:1077-1094` 的 Worker shutdown 挂在 `RunEvent::Exit` 上，自然也不会执行。
5. 进程就这么常驻：没有窗口、没有任务栏图标，看着像已经退出。Worker 因为宿主 stdin 还开着，继续正常运行。Windows 上两个 exe 都被占用 → 安装目录删不掉。

为什么另外两端没暴露：

- **macOS 行为完全相同**，但 Dock 里有图标，看起来像正常的「应用还在后台」；而且 macOS 允许把正在运行的 `.app` 拖进废纸篓。所以同样的缺陷在 macOS 上不可见。**注意：用 SIGTERM 杀进程无法复现**（直接终止，绕过整个退出判定），只能靠「关窗口」触发——这一点让 macOS 侧的排查很容易走空。
- **Linux** 逻辑一致，但 CI 只构建 macOS arm64 / macOS x64 / Windows 三端，不发版。

为什么之前没发现：窗口复用改造（`729f623`、`314e59f`、`8b1278c`）都在解决「关掉再点打不开」，验收动作只到「设置页能不能重新打开」为止，**没有人关完主窗口再去任务管理器看进程**。3.1 那张四轮表里，四轮都盯着「能不能打开」，没一轮验证「能不能退出」。

修复（两处，同一笔提交）：

- `main.rs`：`.run()` 闭包里监听 `RunEvent::WindowEvent { label, event: WindowEvent::Destroyed }`，`label == "main"` 时 `app.exit(0)`。走的是 `Message::RequestExit` 路径（`tauri-runtime-wry` lib.rs:4354-4366），**不受窗口表是否为空影响**，并且会完整触发 `ExitRequested` → `Exit`，既有的 Worker shutdown + 10 秒等待 + kill 逻辑照常执行。三端统一为「关主窗口即退出」。
- `worker_entry.py`：`os._exit(code)` 之前显式关掉模型子进程。这是同一场景下**被掩盖的第二个缺陷**：Worker 退出走 `os._exit`，会跳过 `atexit`，而 `model_service.py:792` 的 `atexit.register(_SHARED_SERVICE.close)` 是模型子进程唯一的回收点（`close()` 全库只在 `clear_cache` 里另调过一次）。Windows / Linux 用 `multiprocessing` spawn 出第二个 `pixelflow-worker.exe`，`daemon=True` 也救不了——`multiprocessing` 的回收同样挂在 `atexit` 上。macOS 因为 `_direct = sys.platform == "darwin"` 不派生子进程，天然免疫。主程序不退出的情况下这个坑不会触发，所以之前只看到 2 个进程而不是 3 个；**退出修好后它会立刻暴露**，因此两处必须一起改。

验证：`cargo test` 14 项通过；Worker 测试 215 项通过 1 项跳过；`protocol:check` 与 `npm run build` 通过。Windows 实机复测待做。

### 3.8 Windows 点「开始生成」完全没反应（错误无法关联导致状态卡死）

现象：Windows 上选好目录点「开始生成」，界面停在「生成失败」后再点毫无反应——不报错、状态文字不变、进度不动。设置页「测试模型」同样一直停在「检测中」。

**结论：不是路径、不是模型、不是锁，是错误响应关联不上，前端把错误丢了。** 一次失败之后界面就永久禁用，只有重启能恢复。

证据链（`PixelFlow-诊断包(2).zip`，Windows，`ddfeebf` 构建）：

1. `worker-events.jsonl` 里有 **10 条 `request_id: "unknown"` 的 error**（事件 13-19、24、25、30），`code: invalid_message`，`message: invalid JSON: Invalid escape`。
2. `worker.py` 原来的异常分支写死 `_event("unknown", "error", ...)`——Worker 解析不了这行，就不知道是谁发的，于是回了个前端认不出的 id。
3. `App.vue:1838` 的错误入口按 id 过滤：`request_id !== 'worker' && !== generationRequestId && !== classificationRequestId` 就直接 `return`。`"unknown"` 三条都不匹配，**整条错误被丢弃**。
4. 于是 `isRunning` / `classificationRunning` / `modelProbePending` 都不会复位。下一次点击在 `classifyMaterials` 的 `if (classificationRunning.value || isRunning.value) return` 或 `probeModels` 的 `if (modelProbePending.size) return` 被静默拦下——**不报错、不改状态**，表现就是「点了没反应」。
5. `classificationRunning` 有 120s 看门狗兜底，`isRunning` **没有**，所以生成侧是真的永久锁死。

同源的第 4 条（重开仍显示上次路径）：`App.vue:1418-1426` 在启动时无条件用恢复快照覆盖 `inputRoot` / `outputRoot`，用户改过的路径会被旧快照压回去。

修复（`4e917fb`）：

- `worker.py`：解析失败时用正则从原始行抠 `request_id`（`_salvage_request_id`），并在 message 里附带截断 200 字符的原始行（`_raw_line_excerpt`）。错误因此能回到原请求，锁能释放，坏在哪也直接看得见。
- `App.vue`：`isRunning` 增加**停滞看门狗**——每收到一次 `progress` 续期，180s 无事件则释放并报「生成超时」。用「无事件」而不是固定总时长，避免误杀正常的长任务。
- `App.vue`：`modelProbePending` 增加 60s 超时释放并报错。

验证：Worker 测试 216 项通过 1 项跳过（新增 `test_salvage_request_id_reads_the_id_from_broken_json`）；`vue-tsc --noEmit` + `vite build` 通过。

**仍未闭环**：什么把 JSON 弄成非法转义，尚未定。已排除的方向——不是 cp936 误解码（用真实路径 `C:\Users\admin\Desktop\跑步运动棉马甲v2` 实测，cp936 会抛 `UnicodeDecodeError` 并解出 `璺戞` 乱码，而 Worker 报错里的中文是完好的），也不是路径尾随空格（用户确认没有），也不是 Rust 侧手工拼 JSON（全仓只有两处写 Worker stdin：`worker_send` 和退出时的 `host_shutdown`，后者是硬编码合法 JSON）。

2026-11-05 复核补充：

- ~~同一个包里 15 条含中文事件全部完好落盘，所以该机器 stdout 是 UTF-8、3.6 未发生~~。**这条 2026-11-05 已推翻**：那 15 条来自「从终端启动」的会话（控制台代码页为 65001），而当天双击启动的会话里中文事件为 0，`worker.log` 明确写着 `encoding='gbk'`。编码取决于启动上下文，不能拿一次会话的结果否定机制。详见 3.6 的现场证据。
- 包里 `probe_models` 的 `status` 事件为 **0 条**。`worker.py` 在起探测线程前一定会先发一条 status，没有它就说明探测请求**从未被解析**。方向指向请求侧，不是响应侧。
- `privacy_text` 会把 `message` 里的 `\` 归一成 `/`。实测 Python 3.14 原文是 `Invalid \escape`，日志里只剩 `Invalid escape`——**唯一能判断非法转义的字符恰好被脱敏抹掉**。因此新增 `51789c0`：把出错位置前后 60/140 字符写进 stderr，`worker.log` 原样进诊断包。

### 3.9 重启后仍显示上次任务的路径，改路径也不生效

现象：Windows 上关掉 App 再打开，输入/输出框里还是上一次未完成任务的目录；手动改成新目录、点「开始生成」，界面上没有任何变化。

**根因**：`App.vue:1476-1487` 在 `onMounted` 里无条件用恢复快照覆盖表单：

```js
if (savedResume) {
  resumeSnapshot.value = savedResume
  inputRoot.value = savedResume.input_root      // 无条件写回
  outputRoot.value = savedResume.output_root
}
```

`pixelflow.output-settings` 里不含输入/输出路径，所以这是启动时唯一的覆盖点。它有两个后果：

1. 只要本地还有未完成快照，用户上次选的目录就会被旧值压回去，看起来像「改不动」；
2. 「这是上次的值」和「这是我刚选的值」在界面上长得完全一样，用户无法分辨该信哪个。

注意这条与 3.8 是两件事：3.8 解释「点了没反应」（守卫静默拦截），这里解释「路径为什么是旧的」。两个现象叠在一起时，用户看到的就是「改路径也没用」。

修复（`bff0605`，按「只在点继续时恢复」方案）：

- 启动时只保留 `resumeSnapshot`，**不写** `inputRoot`/`outputRoot`；
- 顶部提示改为「检测到未完成任务，可点「继续未完成」恢复」，明确路径没有被自动填入；
- 路径只在 `resumeTask()` 里写回（原本就是这个行为），「继续未完成」按钮已由 `resumeAvailable` 控制显隐；
- 用户直接选新目录开始新任务时，`classifyMaterials()` 会 `clearResumeTask()`，提示随之消失。

验证：`protocol:check` + `vue-tsc` + `vite build` 通过。待 Windows 实机确认：重启后输入框应为空、提示可见；点「继续未完成」后路径才出现；直接选新目录开始生成时提示消失。

### 3.10 macOS Intel：确认页底部出现灰色遮挡带

现象：Intel Mac 上「确认素材分类」窗口，最后一行卡片的**下半部分被一条灰色半透明带盖住**，卡片仍隐约可见；ARM 正常。

**根因（高度可能，待实机确认）**：`.confirm-grid-scroll-hint` 是贴在网格底部的 60px 滚动提示，样式为

```css
background: linear-gradient(to top, var(--confirm-grid-bg), transparent);
```

`transparent` 在部分 WebKit 版本里按 `rgba(0,0,0,0)` 参与插值，渐变末端会向**黑色**过渡，而不是向背景色淡出——于是本该"渐隐"的一层变成了灰色纱罩。ARM 的 WebKit 版本插值正确，所以只有 Intel 出现。

修复：新增 `--confirm-grid-bg-fade`（浅色 `rgba(238,241,245,0)`、深色 `rgba(48,52,58,0)`），渐变终点改用它，让两端同色、只有 alpha 变化。

**注意**：这条判断基于截图几何（遮挡带正好压在滚动提示的位置、卡片透出）与样式实现，尚未在 Intel 实机复现确认。若复测仍有遮挡，说明不是这一层，需要重新取元素。

### 3.11 Windows：模型子进程起不来（安装路径含中文）

现象：模型检测（轻量）能出「已找到」，但点「加载测试」失败；直接开始生成会在 `Pose 0/46` 之后卡死，120 秒后前端看门狗报超时。把 App 挪到纯英文路径（`C:\PixelFlow\`）后全部正常。

**根因**：`model_service.py:315` 里 `_direct = sys.platform == "darwin"`，Windows 走 `multiprocessing.get_context("spawn")`，子进程要重新执行冻结后的 exe。子进程死在 PyInstaller 的第一引导阶段：

```
pyiboot01_bootstrap.py:95 → _pyi_bootstrap
  → pyimod03_ctypes.install → ctypes/util.py → subprocess.py
  → zipimport._get_data
FileNotFoundError: [Errno 2] No such file or directory: 'Cbase_library.zip'
```

`base_library.zip` 是 PyInstaller 打包的 stdlib 归档。它要找的路径是 `Cbase_library.zip`——**盘符和文件名之间什么都没了**。安装目录是 `...\image\魏大恒1号-Windows-x64 v2\`，路径含中文，bootloader 用 ANSI 代码页拼路径时中文的 UTF-8 字节把反斜杠吃掉，整条路径塌缩。这和 3.6 是同一个字节级机制，只是发生在 bootloader 里，跑在任何业务代码之前。

同一份日志里的连带现象：`ModuleNotFoundError: No module named 'collections.abc'`、`'unicodedata'`（这两个模块就住在 `base_library.zip` 里）、`PermissionError: [WinError 5] 拒绝访问`。

**为什么补 hiddenimports 没用**：模块本来就在包里，坏的是归档路径。补进去只治表象。

修复：`_direct` 改为 `sys.platform in ("darwin", "win32")`，Windows 也在 Worker 进程内推理，**不再派生子进程**。子进程不出现，`base_library.zip` 路径、`WinError 5`、孤儿进程、`os._exit` 这几类问题一次性消失。

并行度不受影响：`_model_parallelism` 用 `os.sysconf` 取内存，Windows 上没有这个函数 → `AttributeError` → 返回 1，仍是串行。代价是失去崩溃隔离，但 Worker 本就有 3 次重启预算。

**顺带修掉的两个误判**：

- 冷启动实测 **168 秒**（三台 ONNX 会话，Windows Server 2022 虚拟机）。前端 deep 探测的超时是 60 秒，所以「加载成功」被报成「检测超时」。deep 超时改为 300 秒，提示文案改成「首次可能需要两分钟以上」。
- 分类停滞看门狗是 120 秒，而首次 Pose 调用要冷加载 168 秒、期间没有任何 progress → 工作还在跑就被取消。分类看门狗改为 300 秒；生成侧保持 180 秒（那时模型已被分类预热）。

验证：Worker 218 项通过 1 项跳过；`protocol:check` + `vite build` 通过。待 Windows 实机复测。

### 3.12 Windows：素材确认页一片空白（生成正常）

现象：确认素材分类窗口只有顶部摘要和底部按钮，中间网格全空；点「确认并开始」后生成与输出都正常。

**根因**：卡片显隐完全由 JS 驱动。

```css
.confirmation-dialog .confirm-card { opacity: 0; }
.confirmation-dialog .confirm-card[data-visible="true"] { animation: fade-in-up .3s ease-out forwards; }
```

`data-visible` 由 `IntersectionObserver` 在进入视口时置为 `true`。Windows 上确认窗口是先以隐藏状态创建、再 `show()` 出来的：观察器在窗口不可见时判定全部卡片不进入视口，而窗口显示后尺寸没变、不触发重新计算，于是没有任何一张卡片被标记 → 整页 `opacity: 0`。

修复：卡片默认 `opacity: 1`，动画只作为增强。内容不应该依赖 JS 才可见——这是「空白页」这类故障的共同形态。

### 3.13 Windows：交互动画全部消失（设置里的开关却是关的）

现象：Windows 上界面没有任何过渡动画，但设置 → 通用 → 「关闭动画」并没有勾选。

**根因**：样式表里有两条全局降级规则，只要系统偏好是「减少动效」，就把所有动画和过渡强制清零：

```css
@media(prefers-reduced-motion:reduce){*{animation-duration:1ms!important;transition-duration:1ms!important}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
```

而 App 自己的开关只读 `localStorage`，两者互不相通。Windows Server 2022（远程桌面）默认关闭「显示动画」，WebView2 如实上报 `reduce`，于是动画全灭，开关却显示未勾选——用户无法从界面上得知原因，也关不掉。

**修复**：

- 删掉两条 `@media(prefers-reduced-motion:reduce)` 全局规则，让 `.reduce-motion` 类成为唯一开关。内容层的动效偏好由 App 自己负责，用户有明确可操作的入口。
- 初始化时若本地没有存过偏好，就读一次系统偏好作为初值（`readInitialReducedMotion`），让勾选状态和实际表现一致。
- 诊断包新增 `reduced_motion` 与 `os_reduced_motion` 两个字段，下次这类问题可以直接从包里读，不用再猜。

### 3.14 分类进度条与实际进度不成比例

现象：素材分类快结束时，底部进度条才走到一半左右，最后一段直接跳到底。

**根因**：进度条按阶段给固定权重，而权重和真实耗时无关。一次 57 张的实测：

| 阶段 | 耗时 | 占总时长 | 原权重 |
|---|---|---|---|
| 规则分类 | 6.2s | 9% | **40%** |
| Pose | 59.4s | 89% | **30%** |
| Seg | 0.0s | 0% | **29%** |

规则分类是纯规则、几秒跑完，却先吃掉 40% 的进度条；真正占 89% 时间的 Pose 只有 30%，所以整段 Pose 期间进度条几乎不动，看起来"卡在一半"。Seg 结束时再一跳到底。

修复：按实测耗时重新分配权重——规则分类 0–15%、Pose 15–85%、Seg 85–99%，`Pose/Seg` 合并阶段按 15–99% 插值。

### 3.15 Windows 首次安装 WebView2 时静默等待，没有进度

现象：没装 WebView2 的机器第一次安装 App，界面停在安装页很久，没有任何进度，看起来像卡死。

**根因**：`tauri.conf.json` 未配置 `bundle.windows.webviewInstallMode`，走 Tauri 默认的 `downloadBootstrapper`，其 `silent` 默认为 `true`。NSIS 模板里对应这一条：

```nsis
ExecWait "$6 ${WEBVIEW2INSTALLERARGS} /install" $1
```

`silent: true` 让微软的 bootstrapper 在后台跑，安装期间安装页只有一行 DetailPrint，没有进度。

修复：显式配置 `webviewInstallMode: { type: "downloadBootstrapper", silent: false }`。安装时会出现微软自己的进度窗口，安装包体积不变（仍只下载 ~2MB bootstrapper）。

### 3.16 点「选择模型目录」界面卡死，随后打不开 App

现象：设置页点「选择模型目录」，主窗口短暂显示「未响应」，对话框迟迟不出现，只能强制退出；强制退出后再双击图标没反应，要重启系统才能再打开。

**根因链**：

1. `pick_directory` 是普通 `#[tauri::command]`。Tauri 官方文档写得很明确：**不带 `async` 的命令跑在主线程**。它在主线程上起 PowerShell 弹 `FolderBrowserDialog`，冷启动 1–3 秒，这段时间主线程被占住 → Windows 判定「未响应」。
2. 对话框没有 owner（`$d.ShowDialog()` 不带参数），Win32 上这种模态框不抢焦点，容易落在应用窗口后面 → 用户看不到，只能强制退出。
3. 强制退出只杀主进程，`msedgewebview2.exe` 子进程变孤儿，继续占着 `%LOCALAPPDATA%\com.pixelflow.desktop\EBWebView`。新实例初始化 WebView2 失败，窗口建不出来 → 「双击没反应」。重启清掉孤儿才恢复。

三步是串联的：**卡住是因，强制退出是果，打不开是果的果**。

修复（`66f1c7a` + `5529fda`）：

- 命令加 `(async)`，跑在独立任务上，不再占主线程。macOS 安全，因为 `native_open_panel` 内部本来就 `run_on_main_thread` + channel 自己切回主线程。
- PowerShell 先建一个 1×1 透明置顶窗体作为 owner，`ShowDialog($owner)` 让对话框出现在应用前面。目录选择和 Logo 文件选择两个调用点都改了。

**排查依据**：用户复测时观察到「点按钮后主窗口短暂未响应」——这条直接坐实了主线程被占住，而不是对话框加载慢。

## 四、已知未闭环

| 问题 | 现状 | 下一步 |
|---|---|---|
| Intel Big Sur 保存设置闪退 | 当前源码保存只 `hide()` 不销毁；崩溃栈指向 WebView dealloc。用户测试包的 Worker 哈希 `c5f28e36` 与 `5958038` x64 包实测的 `37c32cb2`、10-31 包 `1ed4a45d`、10-30 包 `2744fda9` 均不一致 | 用当前 x64 包在 Big Sur 复测；若仍崩溃，取 crash report 并核对二进制 UUID |
| Intel 设置窗口上下蓝色横线 | `8f64c00` 已加 `[role="dialog"][tabindex="-1"]:focus{outline:none}`，当前源码无其他规则能画出该效果 | 同上，先确认测试包版本 |
| Windows release EXE 的 GUI 子系统 | `314e59f` 已加属性；`2745635` 加了 PE `Subsystem` 断言，`5958038` 修正偏移后 CI 实测 `subsystem = 2` | 已闭环；遗留盲区：断言只查打包前的 `target/.../release/*.exe`，未查 NSIS 载荷内的 exe |
| 设置/报告窗口二次打开 | 权限已补（`8b1278c`） | ARM 已实机确认正常（2026-11-03）；Windows 设置页正常 |
| 设置页「测试模型」等很久 / 检测不到 | 两级探测 + 心跳锁已合入（`0ef9bf1`，详见 3.5）：轻量探测毫秒级返回「已找到」，只有「加载测试」才真正建会话 | ARM 已确认正常；Intel 未测 |
| **Windows 点「测试模型」完全没反应** | 已修复（详见 3.6）：Worker 按 cp936 输出中文，Host 按 UTF-8 读失败后静默丢行。现已双端强制 UTF-8，Host 解码失败会留痕并报错 | 待 Windows 复测：探测应返回真实模型状态；若仍失败，`worker.log` 会有明确记录 |
| **Windows「加载测试」失败 / 生成卡在 Pose** | 已修复（详见 3.11）：冻结包子进程在中文安装路径下死在 `pyiboot01_bootstrap`。Windows 改为进程内推理，不再派生子进程；deep 超时 60s→300s，分类看门狗 120s→300s | 待 Windows 复测：中文路径下「加载测试」应成功（冷启动约 2–3 分钟）；分类应能跑完 Pose |
| **Windows 确认素材页空白** | 已修复（详见 3.12）：卡片默认 `opacity:0`，靠观察器置位才显示，隐藏窗口里观察器不重算 | 待 Windows 复测：确认页应直接显示 57 张卡片 |
| **Windows 退出后删不掉安装目录、残留 2 个进程** | 已修复（详见 3.7）：辅助窗口只 `hide()` 不销毁，窗口表非空，Tauri 永不退出；顺带修掉 `os._exit` 跳过 atexit 导致的模型子进程孤儿 | 待 Windows 实机复测：开设置页 → 关设置页 → 关主窗口 → 任务管理器应为 0 进程，且安装目录可删 |
| **Windows 点「开始生成」没反应 / 测试模型一直检测中** | 已修复（详见 3.8）：错误回 `request_id: "unknown"` 无法关联，前端丢弃后状态卡死；已改为从原始行抠 id，另加两处超时释放 | 待复测；**JSON 为何变成非法转义仍未定**，`51789c0` 已把出错位置写进 `worker.log` |
| **重启后仍显示上次任务路径 / 改路径不生效** | 已修复（详见 3.9）：启动时无条件用恢复快照覆盖输入框，旧路径压回用户选择 | 待 Windows 实机确认：重启后输入框为空、提示可见，点「继续未完成」才填入 |

## 五、命令速查

```bash
# Worker 全量测试
PYTHONPATH=worker worker/.venv/bin/python -m unittest discover -s worker/tests -p 'test_*.py'

# 前端协议检查 + 构建
cd apps/desktop && npm run protocol:check && npm run build

# Rust Host 测试（含窗口路径规范化用例）
cd apps/desktop/src-tauri && cargo test

# 从当前品牌图重新导出 Windows 图标
worker/.venv/bin/python - <<'EOF'
from PIL import Image
src = Image.open('apps/desktop/src-tauri/icons/icon.icns').convert('RGBA')
src.save('apps/desktop/src-tauri/icons/icon.ico', format='ICO',
         sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
EOF

# 读 Windows 产物的 PE 子系统（2 = GUI；3 = 控制台，双击会附带黑窗）
# 注意：Subsystem 在 PE32 与 PE32+ 中都在可选头 +68。不要按 32/64 位分流——
# PE32+ 删掉了 BaseOfData 但 ImageBase 从 4 字节变 8 字节，两者抵消，后续字段偏移不变。
python3 - <<'EOF'
import struct, sys, glob
for path in sys.argv[1:] or glob.glob('*.exe'):
    data = open(path, 'rb').read(0x1000)
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    assert data[offset:offset + 4] == b'PE\0\0', f'not a PE file: {path}'
    magic = struct.unpack_from('<H', data, offset + 24)[0]
    assert magic in (0x10b, 0x20b), f'unknown optional header magic {magic:#x}'
    subsystem = struct.unpack_from('<H', data, offset + 24 + 68)[0]
    print(f'{path}: subsystem = {subsystem} ({"PE32+" if magic == 0x20b else "PE32"})')
EOF
```

## 六、日志位置

排查"Worker 卡住 / 检测超时"这类问题先看这两个文件（`worker.log` 是 stderr，`worker-events.jsonl` 是协议事件）：

| 平台 | 路径 |
|---|---|
| Windows | `%LOCALAPPDATA%\PixelFlow\logs\worker.log`、`worker-events.jsonl` |
| macOS | `~/Library/Logs/PixelFlow/worker.log`、`worker-events.jsonl` |
| Linux | `~/.local/state/PixelFlow/logs/` |
