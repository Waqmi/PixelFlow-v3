# Task Plan: V3 旧输出清理改为按清单删除

## Goal
V3 完整批次只删除“上一次 V3 自己生成、本次不再需要”的图片；用户自己放的文件、未被 PixelFlow 记录的文件一律不动。

## Steps & Acceptance
1. 新增 V3 清单读写（私有缓存目录，按输出目录路径哈希；不写进输出目录）。
   - 验收：清单文件在 `~/Library/Caches/PixelFlow/manifests/v3-*.json`。
2. `_remove_stale_outputs` 改为：只删 `上次清单 − 本次预计`，且路径必须在输出目录内、在已知生成分类下。
   - 验收：用户自放文件保留；上次生成、本次不再需要的文件被删。
3. 清理移到渲染之后；取消时不清理、不写清单。
   - 验收：渲染失败时旧图不会先被删。
4. 重试（source_filter）不删除，只把本次结果并入旧清单。
   - 验收：重试后再跑完整批次，之前的输出仍能被正确识别。
5. 测试：替换原 stale 测试；新增“用户文件保留”“重试不删除”测试；跑定向与全量 Worker 测试。

## Out of Scope
- 复用判断不看规则版本（问题 2），不改。
- 同名文件夹编号随批次变化（问题 4），不改。
- 已存在但没有 V3 清单的旧输出目录不会自动清理（安全优先）；需要用户换空目录。

## Status
- [x] 1 [x] 2 [x] 3 [x] 4 [x] 5

## Results
- 定向测试（engine/anchor/shoe）：72 项通过。
- 全量 Worker：135 项，134 通过；唯一失败为原有缺夹具 `test_model_fixture_manifest_is_available_and_rule_classifies_model`。
- 红灯验证：新“用户文件保留”测试在旧 engine 上失败（2 != 1，用户文件被删），新代码通过。
- 已知副作用：test_classification 等未隔离清单路径的测试会在 `~/Library/Caches/PixelFlow/manifests` 留下 v3-*.json（仅记录，不含图片）；本次已手动删除。
- 已完成 App 重新打包；真实目录重跑仍未执行。

## Packaging Result
- Built with the project Python 3.14 venv and offline Cargo cache.
- Signed and verified `/Users/weiheng/Desktop/PixelFlow/PixelFlow-v3.0.0-beta.1.app`.
- Bundled Worker handshake reports `rules_version: v3-composition-4` and arm64 binaries.
