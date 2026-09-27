# T3 当前内置 legacy 基线（2026-09-27）

## 运行范围

- 输入清单：fixtures/manifest.json，固定 16 张商品素材。
- 输出尺寸：1440x1440、1440x1920、1125x1500。
- 输出选项：含 Logo、无 Logo、唯品图；透明 PNG 按现有引擎规则生成。
- legacy 与 v3 都使用同一份临时 16 张输入树，避免未选素材影响短文件名编号。
- 模型图未纳入本批基线（include_model_images=false）。

## 持久化证据

| 证据 | 内容 |
|---|---|
| baselines/v2.6/legacy-current-20260927-report.json | 当前内置 legacy 输出清单、哈希和运行统计 |
| baselines/v2.6/v3-current-20260927-report.json | 当前 v3 输出清单和 legacy↔v3 逐文件比较 |
| baselines/v2.6/v3-current-20260927-compare-report.json | 独立差异报告，含指标和分类 |

当前 legacy_engine 聚合 SHA-256：

fbf88815188742ce228d9f8f884c330a3c08eddf96a3071421f94098c614f830

## 结果

| 指标 | legacy | v3 |
|---|---:|---:|
| 输入数 | 16 | 16 |
| 产品主图 | 96 | 96 |
| 透明图 | 8 | 8 |
| 唯品图 | 16 | 16 |
| 输出文件 | 120 | 120 |
| 失败 | 0 | 0 |

legacy↔v3 路径、尺寸和模式均可配对比较：120/120。首轮像素指标为：

- max_diff: 255
- MAE: 6.336666494652448
- PSNR: 17.698542498997334 dB
- 差异文件：52/120
- 差异 bbox：[0, 88, 1440, 1920]

按 docs/v2.6-output-baseline.md 的首轮阈值分类：

| 分类 | 文件数 |
|---|---:|
| 版本差异 | 0 |
| 可接受实现差异 | 0 |
| 需修复 | 52 |

差异集中在产品主图 48 个文件和唯品图 4 个文件；透明图 8 个文件像素一致。
具体路径、逐文件 max_diff、MAE、PSNR 和 diff_bbox 见独立差异报告。
当前结论是“输出数量与路径对齐，像素差异仍需修复或登记”，不能宣称
legacy 与 v3 已完成像素 parity。

## 可复现命令

    python tools/run_renderer_baseline.py \
      baselines/v2.6/legacy-current-20260927 \
      --mode legacy \
      --report baselines/v2.6/legacy-current-20260927-report.json

    python tools/run_renderer_baseline.py \
      baselines/v2.6/v3-current-20260927 \
      --mode v3 \
      --compare-root baselines/v2.6/legacy-current-20260927 \
      --report baselines/v2.6/v3-current-20260927-report.json

脚本每次会先清理目标输出目录，并为两种模式建立相同的临时输入树。
