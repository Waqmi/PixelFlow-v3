# Findings
- 当前基线 a6a6131；此前 smoke 5来源、27输出、0失败，但没有可靠阶段计时，不能据此断言速度。
- UI 手工验收已通过；本轮关注线程、等待、数据传输、推理和编码。
- 既有命令行 GUI 启动异常尚未定位，不计为应用冷启动耗时。
- 分工：perf_ui_host → ui-host.md；perf_worker_pipeline → worker-pipeline.md；runtime_ui_verify → measurements.md。

## 中间审查
- 正常确认前分类使用 V26ClassifierAdapter；不能用 V3ClassifierAdapter 的独立调用路径推断实际 UI 分类流程。
- 规则分类≤4，预览池4，渲染按source≤3；模型阶段逐素材执行，macOS同进程模型请求共享锁。
- 现有smoke阶段累计计时 compose42.405s/encode27.363s/write.677s，约93%在构图+编码；累计job时间不能当wall time。确认前模型分类不在generation classification计时里。
- UI→新窗口ping→广播ready→probe链成立；probe重读模型SHA256并持模型锁。收益尚未测量。
- 确认页重复some/find/includes与全量滚动几何扫描可影响大批次交互。优先小改索引/合并，不直接上虚拟列表。

## 最终审核修正
- PNG _encode_png 提前生成四份量化候选，为明确可消除浪费。
- Logo/noLogo底图重复成立；VIP参数未必一致，复用必须全参数匹配。
- 透明alpha复用仅适用于无模板提前返回的路径。
- 当前阶段求和不是wall，前确认模型计时缺失。
