# legacy 兼容周期记录：v3.0.0-beta.2

状态：🔄 进行中

本记录定义 T11 的最小可审计周期。开始日期、真实批次和回退计数必须来自实际运行证据；未运行的项目保留为 `null` 或 `pending`，不能用自动化 smoke 或故障注入结果代替真实批次。

## 完成条件

- 周期覆盖 `3.0.0-beta.2`，并满足 **至少 14 个自然日** 或 **至少 2 个真实批次**。
- 自然运行的 v3 回退次数为 `0`；故障注入和明确选择 `mode=legacy` 的运行单独统计。
- T3 compare 报告中的 `classification_summary.needs_fix` 为 `0`。
- T8 每次结果的复核、接受、保守重跑、回退、跳过和失败统计可以从结果报告追溯。
- 只有完成上述条件后，才单独评估移除 `legacy_engine` 和 compare 对 legacy 的依赖。

## 当前周期

结构化记录见 [`legacy-cycle-beta.2.json`](./legacy-cycle-beta.2.json)。当前基线在 2026-09-27 建立，尚未满足 14 天或 2 批次条件，因此不能标记完成。

| 项目 | 当前值 | 证据/说明 |
|---|---:|---|
| 周期开始 | `2026-09-27` | beta.1 基线收尾后建立记录 |
| 已过自然日 | `0` | 由记录脚本按时间更新 |
| 真实批次 | `0` | 待实际用户批次登记 |
| 自然回退 | `pending` | 需每个真实批次的结果报告 |
| T3 `needs_fix` | `52` | [`v3-current-20260927-compare-report.json`](../baselines/v2.6/v3-current-20260927-compare-report.json) |
| T8 统计 | `implemented; pending real batch` | 结果页已提供复核、保护框、接受/保守重跑和回退统计 |

## 记录真实批次

每个真实批次完成后，复制 `docs/legacy-cycle-beta.2.json` 中的 `real_batches` 项，填写：

- 批次 ID、运行时间、输入素材摘要和输出报告路径；
- `natural_fallback_count`、`skipped_count`、`failure_count`、`review_count`、`accepted_review_count`；
- 是否为用户自然运行；故障注入、显式 legacy 和开发 smoke 不得计入该字段。

建议在批次完成后执行：

```bash
python3 tools/run_acceptance.py \
  --skip-desktop \
  --legacy-cycle-record docs/legacy-cycle-beta.2.json
```

该命令只刷新检查时间、验收报告引用和可读取的 T3 差异计数，不会把周期自动标记为完成，也不会把缺失的真实批次推断为成功。
