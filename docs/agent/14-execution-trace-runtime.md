# Execution Mode 与执行轨迹协议

状态：运行时协议 `1.0`，2026-08-21。

## 目标

为每个真实 Agent run 持久化可机器评分的执行记录：

```text
execution_mode
plan
actions
observations
stage_timings
terminal_action
termination_reason
budget_usage
finalization_contract
```

协议记录 Agent 实际做过什么，不把评测用例或期望工具顺序注入运行时。API 返回的 `execution_trace` 可以直接交给多步评测适配器评分。

## 分阶段延迟

`stage_timings` 以追加事件记录关键模型阶段的墙钟耗时：

- `intent`：rules-first、上下文规则或每次意图模型尝试；模型失败后回退规则会额外记录一个零成本 rules 成功事件，避免重复计算已经发生的模型等待时间。
- `planner`、`executor`、`replanner`、`finalizer`：轻量 Plan-and-Execute 中每次独立模型调用。
- `tool_batch`：Controller 执行一次 `parallel_read` 批次的墙钟耗时；来源为 `controller`，不计作模型调用。
- `direct_agent`：direct 模式下整个 LangChain 模型/工具循环的总耗时。

每条事件包含 `stage`、`attempt`、`source=model|rules|controller`、`status`、`latency_ms` 和安全的 `error_category`，不保存 Prompt、模型原始输出或用户业务值。Finalizer 成功返回时还记录可选的 `input_chars`、`output_chars`、`input_tokens`、`output_tokens` 与归一化 `finish_reason`，用于区分输入规模、生成规模和供应商尾延迟；这些都只是计数或枚举，不包含实际训练资料。阶段事件是观测字段，不改变 `budget_usage.model_calls` 的原有计数语义。Planner/Executor 等异常也会先通过 run 所有权校验持久化失败 timing，再进入统一失败收口。

Finalizer 在调用模型前会构造最小证据包：步骤只保留 `id/objective/status/summary`，观察移除 `call_id` 等执行标识并合并完全相同的重复项。工具结果原样保留，结构化调用原有的整体输入安全上限不变。本阶段不新增证据截断，也不降低模型输出上限，回答详细度与终止动作契约保持不变。

Planner/Replanner 与 Executor 的 deadline 由 Controller 包裹实际策略调用，因此模型客户端的内部重试不能越过角色级墙钟预算。Planner/Replanner 默认 30 秒且结构化输出最多 1200 tokens，Executor 每次决策默认 20 秒。初始 Planner deadline 会记录失败 timing、`planner_deadline_fallback` 原因和受限降级计划；参数已知、彼此独立的 2 至 3 个主证据合并为一个显式 `parallel_read`，聚合进度的历史替代证据只在失败后使用。Replanner deadline 不生成通用替代计划：Controller 冻结已有 action/observation，当前步骤失败、后续步骤跳过，本次尝试计入模型和重规划预算，并记录 `termination_reason=replanner_deadline_exceeded` 后交由 Finalizer 基于部分证据收口；若 Finalizer 同时失败则返回固定的只读安全说明。Executor deadline 同样把当前步骤标为失败、剩余步骤标为跳过，并记录 `termination_reason=executor_deadline_exceeded`；Finalizer 只能基于 deadline 前已经持久化的观察透明收口。

## Finalization Contract

`finalization_contract` 记录本轮允许的语义结果、模型选择的结果和 Controller 派生的终止动作。普通查询只允许 `informational_answer|insufficient_evidence`；具有个性化调整、冲突或避让语义的请求允许 `adjustment_proposal|no_change_needed|insufficient_evidence`。这只决定 proposal 是否属于本轮能力边界，不预判事实结论。

Finalizer 根据真实观察选择语义结果；Controller 唯一映射：`adjustment_proposal → proposal`，其他结果均为 `answer`。模型选择集合外结果、普通查询擅自产生 proposal，或 outcome 与 terminal action 不一致时，以稳定的 Finalizer 契约错误拒绝，不进入最终消息。

## Execution Mode

首版门控顺序：

1. `risk_level=high`：`safe_stop`。
2. 必须澄清：`clarify`。
3. 同时存在多个候选证据源和多个语义子任务：`planned`。
4. 其他请求：`direct`。

门控同时记录 `mode_reasons`。这只是可替换的启发式基线，不是固定业务流程；它不决定工具顺序或具体结论。

## Plan

当前运行时已经实现逻辑独立的 Micro Planner：

- `direct_v1`：一个直接目标步骤。
- `planning_gate_v1`：门控已选择规划，但 Planner 尚未返回时的零步骤状态。
- `model_micro_plan_v1`：Planner 返回的 1 至 3 个真实高层步骤；一个局部条件分支可压缩成单个 `bounded_react` 步骤。
- `deadline_fallback_v1`：只有初始 Planner 超过独立 deadline 时启用的受限降级计划；仍受白名单、最多 3 步和 direct/parallel read/bounded ReAct 边界约束。
- `clarification_gate_v1`：零步骤，直接澄清。
- `safety_gate_v1`：零步骤，直接安全停止。

每个真实规划步骤包含 `objective`、步骤级 `candidate_tools`、`success_signal`、显式的 `execution_strategy=direct|parallel_read|bounded_react` 和 `completion_policy=executor_decides|after_successful_observation|after_all_observations`。`parallel_read` 还必须包含 2 至 3 个参数完整的 `planned_actions`；其他策略该字段为空。`intent_subtasks_v1` 与 `agent_loop` 仅为已持久化历史 trace 保留兼容解析，新运行时不再生成。

`after_successful_observation` 只允许用于 `direct`。工具成功返回后，Controller 直接把步骤标为完成，省去一次只输出 `complete_step` 的 Executor 调用；`found=false` 等仍属于成功 observation。工具异常不会自动完成，仍交由 Executor 基于错误观察收口或请求重规划。旧 trace 缺少该字段时按 `executor_decides` 解析。

`after_all_observations` 只允许用于 `parallel_read`。Controller 在启动前一次性校验只读并行安全集合、全局白名单、参数 schema、动作唯一性、条件替代关系和剩余预算。全部工具成功时步骤自动完成，Executor 调用数为 0；任一工具失败时保留整个批次的 observation，只允许一次 Executor 在“基于部分证据完成”与“请求一次重规划”之间决策，不能在原批次后追加工具调用。

## Action 与 Observation

`direct` 模式仍遍历 LangChain 返回的真实消息；`planned` 模式由 Controller 在每次工具调用前后实时追加：

- 每个模型 `tool_call` 生成 `AgentActionTrace`。
- 每个对应 `ToolMessage` 生成 `AgentObservationTrace`。
- action 和 observation 共用全局递增 `sequence`，并通过 `call_id`、`action_sequence` 配对。
- `parallel_read` 批次的 action/observation 还共享 `batch_id`；每个动作仍保留独立 `call_id`，因此工具审计继续逐调用幂等持久化。
- 工具 ID 转回服务端规范 ID，模型不能借 trace 新增工具权限。
- 参数记录规范化结构；用户身份仍由服务端闭包注入，不进入参数。
- Observation 只保存现有工具审计摘要和字段级 `fact_keys`，不复制健康敏感值或完整业务记录。
- Observation 同时保存规范化工具结果的 SHA-256 指纹。评测夹具只有在指纹匹配时才映射 fact ID，避免仅凭“工具调用成功”虚增事实覆盖率；指纹不能还原原始值。

## 持久化与所有权

Alembic `0017` 为 `agent_runs` 增加：

- `execution_mode`：便于查询和聚合。
- `execution_trace`：版本化 JSONB 协议。

数据库检查约束只允许 `direct`、`planned`、`clarify`、`safe_stop` 或历史空值，防止协议外模式进入持久化状态。

意图完成后先持久化 `status=running` 的规划门控状态。Planner 返回后持久化真实计划；每个 action 请求和 observation 结果均在 run 所有权校验下即时持久化。助手最终消息继续在 run 完成事务中提交。旧 worker attempt 无权覆盖新 attempt 的 trace；失败路径也会写入 `terminal_action=failed` 和稳定终止原因。

Alembic `0018` 为 `(run_id, call_id)` 增加部分唯一索引，防止同一真实工具事件产生重复审计记录。

`GET /api/v1/agent/runs/{run_id}` 返回类型化 `execution_trace`。排队中或历史旧 run 可以为 `null`；旧 trace 缺少 `stage_timings` 时按空列表兼容解析。

## 评测适配

`runtime_trace_to_eval_trace` 负责将生产协议映射到评测协议：

- action 映射为工具调用轨迹；
- observation 状态决定调用成功或失败；
- 固定评测夹具把成功观察映射到用例 fact ID；
- 工具 ID、成功状态和结果指纹必须同时匹配，才认为夹具事实已被观察；
- 运行时本身不知道用例的期望事实和答案。

评分单个 API 响应或 trace 文件：

```powershell
python backend/scripts/score_agent_multistep_trace.py `
  --case-id active_session_resume_when_absent `
  --trace-file run-response.json `
  --strict
```

确定性评分覆盖模式、终止动作、工具证据源、越权、重复、事实观察和预算。回答是否实际使用事实仍由人工或独立 Judge 评价。

## 轻量执行边界

- Planner 只在 `planned` 模式调用，生成 1 至 3 个线性步骤，不生成 DAG 或子计划。
- `direct` 步骤最多 1 次工具调用；`bounded_react` 步骤最多 2 次。
- `parallel_read` 只允许 2 至 3 个 Planner 已确定的只读动作；真实数据库调用各自使用独立短生命周期 `AsyncSession`，避免在同一 SQLAlchemy 会话上并发查询。
- direct 的自动收口必须由 Planner 通过 `completion_policy` 显式授权；bounded ReAct 禁止使用自动完成策略。
- 全局最多 4 次工具调用、1 次重规划、12 次模型调用，每步最多 4 次决策尝试。
- Planner/Replanner 默认 30 秒独立 deadline、结构化输出默认最多 1200 tokens；每次 Executor 决策默认 20 秒 deadline。deadline 和 token 配置不扩大模型、工具或写权限。
- Executor 不能自行改变步骤策略或扩大候选工具；改变策略必须请求 Planner 修订。
- 相同工具与规范化参数不能重复调用。
- 重规划只替换尚未完成的步骤，`plan.version` 递增；action 记录其发生时的 `plan_version`。
- 当前只读工具没有携带评测 fact ID；fact ID 只由离线夹具映射。
- `proposal` 当前表示待确认的文本建议，不代表已经创建可执行写提案或修改业务数据。

具体实现见 [轻量 Plan-and-Execute 首版](15-lightweight-plan-execute.md)。
