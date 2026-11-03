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
| 2026-11-03 | 设置窗口关掉后再点没反应（三端） | Tauri ACL | 复用窗口要调 `show()`/`setFocus()`，但 capability 从未授予这两个权限 | `8b1278c` |
| 2026-11-03 | Windows 打包图标是旧版 | 资源同步 | 换品牌只换了 `icns`/`png`，`icon.ico` 停留在 09-13 的旧图 | `a6ece63` |
| 2026-11-03 | Windows 打开输出文件夹打开错目录 | 跨平台路径 | 前端用 `/` 拼 Windows 路径得到混合分隔符，原样交给 explorer | `8b1278c` |
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

## 四、已知未闭环

| 问题 | 现状 | 下一步 |
|---|---|---|
| Intel Big Sur 保存设置闪退 | 当前源码保存只 `hide()` 不销毁；崩溃栈指向 WebView dealloc。用户测试包的 Worker 哈希 `c5f28e36` 与当前 CI x64 产物 `5c2f6c25`、10-31 包 `1ed4a45d`、10-30 包 `2744fda9` 均不一致 | 用当前 x64 包在 Big Sur 复测；若仍崩溃，取 crash report 并核对二进制 UUID |
| Intel 设置窗口上下蓝色横线 | `8f64c00` 已加 `[role="dialog"][tabindex="-1"]:focus{outline:none}`，当前源码无其他规则能画出该效果 | 同上，先确认测试包版本 |
| Windows release EXE 的 GUI 子系统 | `windows_subsystem = "windows"` 已加，构建通过 | 对 CI 产物做 PE 可选头 `Subsystem` 检查（应为 2） |
| 设置/报告窗口二次打开 | 权限已补 | ARM 与 Windows 实机点验 |

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
```
