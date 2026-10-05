# PixelFlow 全链路性能审计

## Goal
梳理 a6a6131 从应用启动到报告的线程/进程和关键等待，交付有代码与测量依据的优化优先级；本轮不实现优化。

## Next Step
审计已完成；后续按报告第一批逐项优化并验证，当前不实现。

## Phases
1. 范围与分工 — complete
2. 前端/Rust、Python链路与现有性能证据 — complete
3. 审核瓶颈与风险、形成报告 — complete
4. 核对无产品代码修改并交付 — complete

## Constraints
- luna worker 执行，主代理审核策略。
- 保留 legacy_engine 观察期与两个 deleted detection sidecar。
- 不提交、不推送；只新增审计记录，采样输出在独立 /tmp。
- 区分代码事实、实测数据和待测假设，不虚构加速比例。

## Errors
fast-context 未暴露，退回 rg 定向搜索。
