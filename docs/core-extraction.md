# Python Core 抽离记录

## 当前阶段

v3 Worker 已通过 `V26EngineAdapter` 调用 v2.6 的 `generate_images`。这是
过渡层，不复制或重写图像算法，先保证新协议能够复用已验证的输出逻辑。

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
