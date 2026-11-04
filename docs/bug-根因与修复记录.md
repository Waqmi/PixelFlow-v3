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
| 2026-11-04 | **Windows 点「开始生成」完全没反应**、设置页「测试模型」一直检测中 | 错误关联 | Worker 解析失败时回 `request_id: "unknown"`，前端无法匹配就丢弃整条错误，`isRunning` / `modelProbePending` 永远不清空，后续点击被「已在运行」守卫静默拦下 | 见 3.8 |
| 2026-11-04 | **Windows 退出 App 后删不掉安装目录**，任务管理器里残留 2 个进程（`PixelFlow.exe` + `pixelflow-worker.exe`） | 窗口生命周期 | 辅助窗口关闭时只 `hide()` 不销毁，隐藏窗口仍占窗口表；Tauri 只在窗口表为空时才退出，于是主窗口关了进程却不退，Worker 跟着活，两个 exe 一直被锁 | 见 3.7 |
| 2026-11-03 | 设置窗口关掉后再点没反应（三端） | Tauri ACL | 复用窗口要调 `show()`/`setFocus()`，但 capability 从未授予这两个权限 | `8b1278c` |
| 2026-11-03 | Windows 打包图标是旧版 | 资源同步 | 换品牌只换了 `icns`/`png`，`icon.ico` 停留在 09-13 的旧图 | `a6ece63` |
| 2026-11-03 | Windows 打开输出文件夹打开错目录 | 跨平台路径 | 前端用 `/` 拼 Windows 路径得到混合分隔符，原样交给 explorer | `8b1278c` |
| 2026-11-03 | Windows 打开 App 附带控制台黑窗，关掉黑窗 App 一起退出 | Windows 子系统 | 主 exe 是控制台子系统（PE `Subsystem=3`）；`windows_subsystem` 属性 11-02 才进主仓，用户手上的 10-31 包早于它 | `314e59f` + CI 断言 |
| 2026-11-03 | 设置页「测试模型」等很久 / 检测不到 / 一直"检测中"（Intel、Windows） | 探测成本 | `probe` 会真正加载三个 ONNX 会话并对每个模型全量算 SHA-256；macOS 在 Worker 进程内跑、Windows 还要 spawn 冻结子进程；前端互斥锁 30s 短于后端 180s 超时 | `0ef9bf1`（两级探测）；编码问题另见 3.6 |
| 2026-11-03 | **Windows 点「测试模型」完全没反应**，等多久都没结果（ARM 正常） | 协议编码 | Worker 按系统代码页（cp936）输出中文，Rust 按 UTF-8 读取失败后被 `.lines().flatten()` 静默丢弃，前端永远收不到 `done`/`error` | 待修（侧分支 `5106baf` 有 10-18 的旧修复，从未合回） |
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

待修（等 Intel 实机测完再动手，避免同时改动多端）：

1. `worker_entry.py` 无条件 reconfigure stdin/stdout 为 UTF-8（macOS 本就是 UTF-8，无副作用）
2. `main.rs` 读取失败写进 `worker.log`，并向前端发一条 `error`，杜绝"永远转圈"
3. `spawn_worker` 在 Windows 上补 `PYTHONIOENCODING=utf-8` 作双保险
4. `App.vue` 给 `modelProbePending` 加超时释放（60s）并明确报错
5. 移植 `5106baf` 的 cp936 + 中文路径回归测试

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

1. `worker-events.jsonl` 里有 **9 条 `request_id: "unknown"` 的 error**（事件 13-19、24、25、30），`code: invalid_message`，`message: invalid JSON: Invalid escape`。
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

**仍未闭环**：什么把 JSON 弄成非法转义，尚未定。已排除的方向——不是 cp936 误解码（用真实路径 `C:\Users\admin\Desktop\跑步运动棉马甲v2` 实测，cp936 会抛 `UnicodeDecodeError` 并解出 `璺戞` 乱码，而 Worker 报错里的中文是完好的），也不是路径尾随空格（用户确认没有）。下次复现时 error 的 `message` 会带上原始行，可直接定位。

## 四、已知未闭环

| 问题 | 现状 | 下一步 |
|---|---|---|
| Intel Big Sur 保存设置闪退 | 当前源码保存只 `hide()` 不销毁；崩溃栈指向 WebView dealloc。用户测试包的 Worker 哈希 `c5f28e36` 与 `5958038` x64 包实测的 `37c32cb2`、10-31 包 `1ed4a45d`、10-30 包 `2744fda9` 均不一致 | 用当前 x64 包在 Big Sur 复测；若仍崩溃，取 crash report 并核对二进制 UUID |
| Intel 设置窗口上下蓝色横线 | `8f64c00` 已加 `[role="dialog"][tabindex="-1"]:focus{outline:none}`，当前源码无其他规则能画出该效果 | 同上，先确认测试包版本 |
| Windows release EXE 的 GUI 子系统 | `314e59f` 已加属性；`2745635` 加了 PE `Subsystem` 断言，`5958038` 修正偏移后 CI 实测 `subsystem = 2` | 已闭环；遗留盲区：断言只查打包前的 `target/.../release/*.exe`，未查 NSIS 载荷内的 exe |
| 设置/报告窗口二次打开 | 权限已补（`8b1278c`） | ARM 已实机确认正常（2026-11-03）；Windows 设置页正常 |
| 设置页「测试模型」等很久 / 检测不到 | 两级探测 + 心跳锁已合入（`0ef9bf1`，详见 3.5）：轻量探测毫秒级返回「已找到」，只有「加载测试」才真正建会话 | ARM 已确认正常；Intel 未测 |
| **Windows 点「测试模型」完全没反应** | 根因已定位到代码级：协议行按 cp936 输出、Rust 按 UTF-8 读失败被静默丢弃（详见 3.6）。侧分支 `5106baf` 有 2026-10-18 的旧修复，从未合回 | 等 Intel 实机测完再改；改法见 3.6 待修清单 |
| **Windows 退出后删不掉安装目录、残留 2 个进程** | 已修复（详见 3.7）：辅助窗口只 `hide()` 不销毁，窗口表非空，Tauri 永不退出；顺带修掉 `os._exit` 跳过 atexit 导致的模型子进程孤儿 | 待 Windows 实机复测：开设置页 → 关设置页 → 关主窗口 → 任务管理器应为 0 进程，且安装目录可删 |
| **Windows 点「开始生成」没反应 / 测试模型一直检测中** | 已修复（详见 3.8）：错误回 `request_id: "unknown"` 无法关联，前端丢弃后状态卡死；已改为从原始行抠 id 并回显原始行，另加两处超时释放 | 待复测；**JSON 为何变成非法转义仍未定**，下次复现时 error message 会带原始行 |

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
