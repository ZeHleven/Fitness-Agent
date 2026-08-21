# 模型与 Agent 运行时

## 运行时选择

- 使用 LangChain `create_agent` 作为第一版循环执行器。
- 自有的 IntentResolver、ToolRouter、ContextAssembler 和安全策略位于 LangChain 外层，避免框架锁定。
- 模型通过 OpenAI 兼容客户端接入；供应商、基础地址、模型名、超时和重试均由环境配置提供。

## 模型分工

- 意图解析：结构化、小上下文、低温度；失败时回退到规则分类和安全的通用问答。
- Agent 主模型：负责是否调用白名单工具、组织多步查询和生成最终答复。
- 摘要/记忆候选：异步或请求结束后执行，不阻塞主要回复。

第一版允许这些职责使用同一 DeepSeek V4 配置，但接口和观测指标分开，后续可独立替换更快或更强的模型。

## 结构化输出与降级

- 意图、路由、工具参数、卡片和提案均进行模式校验。
- `AGENT_RULES_FIRST_ENABLED=true` 时，高置信、无需上下文消解的单意图和已审计复合语义直接使用规则结果；含指代、置信度不足或未覆盖的表达仍进入意图模型。rules-first 是理解层短路，不决定具体工具执行路径。
- 结构化解析失败只允许一次受控修复；再次失败进入确定性降级。
- 意图模型 provider 超时允许一次有限重试，总尝试数硬限制为 2；其他 provider 错误不做无意义重试。两次尝试共享 `AGENT_INTENT_TOTAL_TIMEOUT_SECONDS` 总预算，首轮按 `AGENT_INTENT_TIMEOUT_SECONDS` 执行并为第二轮保留 `AGENT_INTENT_RETRY_MIN_REMAINING_SECONDS`；剩余预算不足时不重试。默认是单次 6 秒、总计 10 秒、最小重试窗口 2 秒。
- 规划运行时不只依赖模型客户端超时：Controller 对 Planner/Replanner 使用 `AGENT_PLANNER_TIMEOUT_SECONDS`（默认 30 秒），对每次 Executor 决策使用 `AGENT_EXECUTOR_TIMEOUT_SECONDS`（默认 20 秒）。Planner/Replanner 结构化输出还受 `AGENT_PLANNING_MAX_TOKENS`（默认 1200）约束。初始 Planner 超时启用 `deadline_fallback_v1`：本轮白名单中参数已知、彼此独立且安全的 2 至 3 个主证据会合并为一个 `parallel_read`；聚合进度失败时的训练历史只保留为条件替代，不预取。其他 Planner 错误和 Replanner 超时仍失败收口。Executor 超时不重试、不继续后续步骤，保留已有观察并交给 Finalizer 透明降级。
- 结构化错误只暴露异常类型、校验类型和字段路径，例如 `literal_error@references.0.source`；不记录模型原始值、完整响应或用户私有内容。Planner、Executor、Replanner 和 Finalizer 还会附带安全的失败阶段。
- 模型不可用时，一般问答返回明确说明；关键训练数据仍可由只读服务和模板响应提供。
- 工具超时不自动转成写操作，也不宣称数据已更新。
- execution trace 分阶段记录 Intent（含 rules/model 来源与每次尝试）、Planner、Executor、Replanner、Finalizer 和 direct Agent 的墙钟耗时、状态与安全错误类别；不记录 Prompt、模型原始输出、密钥或用户业务值。阶段 timing 不改变模型调用预算计数。
- Planner 可显式生成 `parallel_read`：仅限 2 至 3 个参数已确定、彼此独立的白名单只读动作。Prompt 包含“三项独立且全部必需时合并为单个三动作批次”的正例，以及观察依赖路径不得并行的反例。Controller 完成安全集合、参数、预算和条件替代关系校验后并发执行；全成功自动完成且不调用 Executor，部分失败才唤醒一次 Executor 判断收口或重规划。生产数据库调用为每个动作使用独立短会话。
- Planner 只接收截断后的已消解请求、有限语义目标、压缩工具契约和硬预算；工具目录移除展示标题、examples 等非决策字段，观察只保留最近事件与受限预览。模型只输出 `steps`，`goal` 由服务端可信状态附加，降低上下文、输出 token 和结构化漂移。
- 真实模型离线夹具 runner 支持 `--repeat` 重复采样，并输出按用例和按阶段的 P50/P95；单次样本只能用于冒烟，不能代表尾延迟。
- Finalizer 模型不再直接自由填写 `terminal_action`，而是从本轮允许集合中选择 `informational_answer`、`no_change_needed`、`insufficient_evidence` 或 `adjustment_proposal`。Controller 将前三者映射为 `answer`，只把 `adjustment_proposal` 映射为 `proposal`，并再次校验语义结果与终止动作一致。
