# Progress

- 2026-09-30 方案文档完成，开始 S1。
- S1–S5 已实现：分割逐行范围（含越界修复）、V26/V3 两条分类路径传入分割、anchor_crop_box、CompositionPlan.crop_box + 渲染器直裁、缓存版本 v3-composition-2。
- 现有测试：113 项，仅剩改动前已存在的 1 项夹具缺失失败；已更新 5 处测试（fake 接口加 segmentation 参数、版本号）。
- 修正：鞋左右宽度只计双脚（原先计入手、蹲姿大腿，导致窗口暴涨）。
- 阻塞：方案 A（左右不切）与 3:4 竖图的上下锚点冲突，需用户决策；新增单测待规则定稿后补。
- 用户选折中：放大上限 1.3 倍，超出裁左右并提示。已实现并复测（见方案文档第 7 节）。
- 新增 tests/test_anchor_composition.py（14 项）；全量 127 项仅存旧失败 1 项；端到端逐像素一致。
- 提示阈值改为裁切 > 15%：鞋 12/26、上衣 9/26 张有提示；构图窗口不变；测试 128 项仅旧失败 1 项。
- 抽样修复（方案 C：5 张均匀抽样 + 有模特则全文件夹逐张 Pose）；测试 129 项仅旧失败 1 项；真实文件夹无计划模特图 0 张。规则版本同步 v3-composition-2。重新打包中。
- 2026-09-30 打包完成：`/Users/weiheng/Desktop/PixelFlow/PixelFlow-v3.0.0-beta.1.app`（152 MB，arm64，ad-hoc 签名，deep verify 通过）；Worker SHA-256 c10879117a4f0a077ff22d3540222919548d1bce4f9c919d4651b5e886bc2bdf；冻结 Worker ready=v3-composition-2，6 张鞋图全部 anchor_crop，shutdown 正常，stderr 为空。旧包备份为 `PixelFlow-v3.0.0-beta.1-backup-20260930.app`。未做：DMG、公证、宿主 UI 人工点击验证。
- 用户实测反馈后修订：上衣下沿到大腿根（+25% 大腿长）；鞋改为按鞋大小构图（默认占宽 65%，≤70% 避膝，绝不切鞋）；规则版本 v3-composition-3；测试 132 项仅旧失败 1 项。重新打包中。
- 2026-09-30 v3-composition-3 已打包安装：PixelFlow-v3.0.0-beta.1.app（deep verify 通过），冻结 Worker 鞋/上衣构图与源码一致；原始包备份 PixelFlow-v3.0.0-beta.1-backup-20260930.app 保留。
- 2026-09-30 用户反馈：鞋仍偏大、上衣需多露大腿；按要求不改代码，方案文档重写为定稿（已修复 F1–F9、待修复 T1/T2、待确认 Q1–Q5），交由 Codex 实施。验收脚本已存入本目录。
