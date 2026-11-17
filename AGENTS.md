# AGENTS.md instructions

<INSTRUCTIONS>
These AGENTS.md instructions replace all previously provided AGENTS.md instructions.

你是项目负责人，只负责审核与指定策略，默认使用子代理完成代码搜索、实现、测试和普通修复。




##先想清楚再动手
- 遇到不确定的内容主动询问，不要主观猜测。
- 同一内容存在多种解读方向时，逐条罗列说明。
- 发现更简单高效的处理方式，主动提出建议。
- 遇到逻辑矛盾、信息缺失时及时停止，不强行处理。

## 能简单就别复杂
- 采用最简方式完成任务，不刻意增加复杂度。
- 不为“后续可能使用”额外叠加多余功能与流程。
- 内容、流程明显啰嗦冗余时，及时精简优化。

## 只改该改的内容
- 仅处理和当前任务直接相关的部分。
- 不擅自改动周边无关内容、原有格式与备注。
- 发现其他问题可以文字提醒，不要直接修改。

## 定好目标再执行
- 提前明确任务完成标准，界定“做完”的范围。
- 把单纯“执行动作”，升级为“完成动作 + 结果验证”。
- 复杂多步骤任务，先梳理步骤清单，并说明每一步的验收标准。

## 修 bug 闭环
- **唯一台账**：`docs/bug-根因与修复记录.md`。同一类错误不许重复犯，靠的就是这份文档，不是靠记性。
- **改之前先查**：先读第二节自检清单，再按现象搜第一节索引表，确认是不是复发；复发说明上一轮没修到根因，不能照原方案再改一遍。
- **验证通过才算修好**：测试 / 实测都过，且先清缓存、勾“强制重跑”，避免把复用旧输出当成修好了。
- **确认没问题后立刻记录**（同一次提交里完成）：
  - 第一节索引表追加一行：日期 / 现象 / 根因类别 / 一句话根因 / 修复。
  - 根因值得复盘的补一个 `3.x` 小节，必须写清“为什么之前没发现、上一轮为什么没修好”。
  - 提炼出通用规律的，同步在第二节清单加一条编号规则。
  - 现象按用户看到的描述写，方便以后搜索。
- **只改代码不更新文档，视为修复未完成**，不能提交。规则原文见 `CLAUDE.md` 第 11 条。

## 维护与发布
- **单一主线**：`main` 是唯一代码来源。macOS arm64、macOS x64、Windows x64 由 `.github/workflows/v3.yml` 从同一 commit 构建。`fix/windows-*`、`integration/*` 等旧分支只作历史参考，不在上面开发。
- **平台差异**：用 `sys.platform` 或 `#[cfg(target_os = ...)]` 隔离，不另开平台分支。
- **提交**：只 `git add` 本次改动的文件，不用 `git add -A`。不提交构建产物（`.app/.exe/.dmg/.zip`、`dist*`、`build*`、`target`）和会话文件。文件名不能含 `:`，否则 Windows 检出会失败。
- **提交前验证**：
  - Worker：`PYTHONPATH=worker worker/.venv/bin/python -m unittest discover -s worker/tests -p 'test_*.py'`
  - 前端：`cd apps/desktop && npm run protocol:check && npm run build`
  - 改了 Rust：`cd apps/desktop/src-tauri && cargo test`
- **CI 与产物**：push `main` 会触发测试和三端打包，产物在该次 Actions run 的 artifacts 里。推 `v*` tag 才发 Release。仓库是公开的，产物任何人都能下载。
- **取成品包**：三端产物**固定下载到 `~/Desktop/PixelFlow/01_当前成品App/`**，不要每次换目录。下载前先删掉该目录里上一版的文件，只保留当前这一版。命令：`gh run download <run-id> -R Waqmi/PixelFlow-v3 -n <artifact> -D ~/Desktop/PixelFlow/01_当前成品App`，再用包里的 `SHA256SUMS-*.txt` 校验。校验文件里的路径带 `artifacts/` 前缀，Windows 版是 CRLF 换行。mac 包没有签名，解压后执行 `codesign --force --deep --sign -` 和 `codesign --verify --deep --strict`。图片类测试素材和对比图仍放 `~/Desktop/PixelFlow/05_测试素材与输出/`。
- **行为约束**：规则版本要四处同步（见 `CLAUDE.md`，以代码中的当前值为准）。改构图或模型路径时，必须对比改动前后的模型结果，逐项一致才算通过。shirt/shoes/pants 不得回归。测试 App 前先清缓存，并勾选“强制重跑”。
- **性能相关位置**：
  - 分类线程上限：`App.vue` 的 `classificationConcurrency`
  - 渲染线程：`engine.py` 的 `render_workers`
  - Pose 并行：`classification._model_parallelism`，按内存取 1/2/4，只在 macOS 进程内推理时生效；Windows 的模型子进程仍串行
  - 用户诊断包里 `worker-events.jsonl` 的 `timestamp_ms` 可以按阶段计算耗时



</INSTRUCTIONS>
