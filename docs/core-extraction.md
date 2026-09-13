# Python Core 抽离记录

## 当前阶段

v3 Worker 已通过 `V26EngineAdapter` 调用 v2.6 的 `generate_images`。这是
过渡层，不复制或重写图像算法，先保证新协议能够复用已验证的输出逻辑。
Adapter 会按 `generate_images` 的函数签名过滤 v2.6 后续新增参数，因此可以
对照记录中的旧提交运行；这只是迁移兼容层，不改变 v2.6 的算法行为。

```text
v3 GenerationRequest
        ↓
V26EngineAdapter
        ↓
v2.6 generate_images
```

已完成一次 16 张素材的真实 Adapter 烟测：生成 24 个输出文件，与
`baselines/v2.6/no-model-no-logo-1440/baseline.json` 的文件路径和 SHA-256
全部一致。

## 下一步拆分顺序

1. 把 v2.6 的请求参数和校验迁入 v3 Core；
2. 把输出目录和文件命名逻辑迁入 v3 Core；
3. 把 Pillow 渲染器迁入 v3 Core；
4. 把分类引擎迁入 v3 Core；
5. 把模型服务和缓存迁入 v3 Core；
6. 用同一份基线逐项对比，最后删除 Adapter。

每一步都必须保留 v2.6 基线回归，不允许先改算法再迁移。

## 当前分类边界

`worker/pixelflow_core/classification.py` 通过小型 Adapter 复用 v2.6 的
`RuleSourceTypeClassifier`，不导入 Qt。当前只迁移规则分类和进度回调；Pose/Seg
增强、分类确认页和人工修改数据结构仍由后续 Worker/UI 阶段负责。素材扫描会
跳过输入目录中可识别的历史生成目录，避免成品被再次当作输入素材。
