# 轻量 Plan-and-Execute 首版

状态：运行时首版，2026-08-20。

## 架构边界

全局以 Plan-and-Execute 为主，ReAct 只作为单个步骤内由 Planner 显式授权的局部策略：

```text
Execution Mode Gate
  ├─ direct → 原有单轮 Agent
  ├─ clarify / safe_stop → 确定性终止
  └─ planned
       → Micro Planner
       → Step Executor
            ├─ direct
            ├─ parallel_read → 2–3 independent primary reads
            │                    └─ optional server-owned fallback batch
            └─ bounded_react
       → observation
       → complete / replan / clarify / safe_stop
       → Finalizer
```

Planner、Executor 与 Finalizer 可以复用同一个底层模型，但使用独立 Prompt、严格结构化输出和角色级边界。Controller 不决定业务路径，只执行状态转换、工具白名单、策略授权、资源预算、Planner/Executor deadline 和 Finalizer 终止动作契约。

## Micro Planner

Planner 仅在 `execution_mode=planned` 时运行。输入是服务端完成指代消解后并截断的目标、有限语义子任务、压缩允许工具契约和硬预算；输出只包含 1 至 3 个线性高层步骤，可信 `goal` 由服务端附加。若第二项证据是否需要完全取决于第一项观察，则优先用一个 `bounded_react` 步骤表达，不为形式完整强拆步骤：

```text
objective
candidate_tools
execution_strategy = direct | parallel_read | bounded_react
completion_policy = executor_decides | after_successful_observation | after_all_observations
planned_actions = [] | [{tool_id, arguments}, ...]
success_signal
```

一般步骤不包含固定工具参数，不要求把全部候选工具都调用，也不生成综合回答步骤。唯一例外是 `parallel_read`：Planner 必须显式列出 2 至 3 个参数完整、相互独立的只读动作；其 `candidate_tools` 与动作工具按顺序完全一致。Controller 会再次校验步骤数量、全局白名单、并行安全集合、参数 schema、条件替代关系与预算，不能只依赖 Prompt 自律。

`parallel_read` 只用于“用户资料、当前计划、实际进度”等被同一语义目标同时要求、且彼此不依赖观察结果的主证据。主工具与条件替代工具硬性禁止放入同一 Planner 并行批次；但主工具可以与其他独立主证据并行，观察返回后由 Controller 按服务端证据组决定是否调用替代工具。单独的条件步骤仍可使用 bounded ReAct。

Planner few-shot 明确展示：当用户资料、当前计划和四周训练进度三项独立且全部必需时，应生成一个包含三个 `planned_actions` 的 `parallel_read`，不能把第三项拆成后续步骤。工具目录不再携带展示标题、examples 等非决策字段，旧 observation 也只保留最近事件和受限结果预览，以控制输入体积和结构化尾延迟。

## Step Executor

Executor 每次只看到当前步骤、已有真实 observation、当前步骤工具目录和剩余预算，输出以下决策之一：

- `call_tool`
- `complete_step`
- `request_replan`
- `clarify`
- `safe_stop`

`direct` 最多执行一次工具调用。`bounded_react` 可以在首次 observation 后再选择一次工具。Executor 无权从 `direct` 自行升级策略，也无权选择步骤候选集或全局白名单之外的工具。

`parallel_read` 不先调用 Executor。Controller 同时发出 Planner 已确定的动作；生产运行时为每个动作创建独立短生命周期数据库会话。全部成功后直接完成步骤，因此这一成功路径只有 Planner 与 Finalizer 两次运行时模型调用。主证据命中固定条件替代契约时，Controller 在预算内直接发起替代批次并完整记录 action、observation、`tool_batch` timing 与逐工具审计；替代成功仍保持零 Executor。其他失败或替代失败再且仅再调用一次 Executor，该次只能基于已有证据完成或请求重规划，不能自行追加工具调用。

为避免相同计划适配请求偶发拆成“两项并行 + 一项 direct”并唤醒 Executor，Controller 对该固定只读路由提供保守的计划形状归一化。只有模型计划本身已经覆盖资料、计划和聚合进度，且没有显式历史动作或下降/趋势语义时，才合并为单个三动作 `parallel_read`；条件历史不预取。缺少任一主证据、显式要求历史分析或使用其他白名单时保持模型原计划。归一化只稳定执行形状，不决定最终是否调整。

当前服务端证据组为：`workout.get_progress --on_error--> workout.list_history` 和 `workout.get_active_session --on_not_found--> workout.get_next`。两端都必须已在动态白名单中；触发条件、方向和默认参数都不是模型输出。主证据成功且未触发条件时，替代工具被逻辑关闭；主证据触发条件且替代成功时，整组视为覆盖。若这些证据组与其他成功观察已覆盖全部动态白名单，Controller 才把后续步骤标为 `skipped`，记录 `termination_reason=agent_completed_evidence_covered`。替代失败、预算不足或仍有独立未读工具时不会提前停止。该判断不注入评测 fact ID，也不做开放式语义推断。

Planner 可以为 direct 步骤选择 `after_successful_observation`。工具成功返回后由 Controller 自动完成步骤，不再额外询问 Executor 是否完成；工具错误仍保持步骤运行并允许 Executor 决定降级或重规划。bounded ReAct 必须使用 `executor_decides`，避免条件分支被过早关闭。

成功返回的 `found=false`、`count=0` 或空列表是有效反事实证据，不视为工具故障。证据组会区分业务反事实与工具错误：活动训练只有 `found=false` 才查询下一练，读取错误不会触发；聚合进度只有工具错误才读取历史。固定证据组之外的替代判断仍由 bounded ReAct 处理。

## 动态重规划

Executor 发现原步骤不再适用时只能请求重规划。Controller 最多接受一次请求，将已完成步骤和真实 observation 交给 Replanner，并只替换当前及后续步骤：

- 已完成事实不可删除或改写；
- 新计划仍受总步骤上限约束；
- `plan.version` 递增；
- 工具 action 保存发生时的 `plan_version`；
- 已调用的相同工具与相同规范化参数不能再次调用。

Replanner 与初始 Planner 共用独立 deadline，但超时后的处理不同。初始 Planner 尚无工具观察，可以生成受限的 `deadline_fallback_v1`；Replanner 超时时已经存在真实观察或工具错误，Controller 不再猜测替代计划，也不允许新增工具调用，而是把当前步骤标为失败、后续步骤标为跳过，并交给 Finalizer 仅基于已有证据透明收口。trace 记录 `replanner_deadline_exceeded`，本次重规划尝试仍计入模型和重规划预算。若 Finalizer 随后也失败，运行时返回固定的只读安全说明，明确没有修改计划或训练记录，避免把可恢复的局部故障升级为整轮 run 失败。

首版不保存完整计划版本历史，不实现 DAG、跨 run 步骤队列或分布式步骤恢复。只读工具在 worker attempt 中断后可以由新 attempt 重新决策；所有实际持久化事件仍受租约所有权和唯一索引保护。

## 硬预算

默认配置：

| 配置 | 默认值 |
| --- | ---: |
| `AGENT_MAX_PLAN_STEPS` | 3 |
| `AGENT_MAX_TOOL_CALLS` | 4 |
| `AGENT_MAX_REPLANS` | 1 |
| `AGENT_MAX_MODEL_CALLS` | 12 |
| `AGENT_MAX_STEP_DECISIONS` | 4 |
| `AGENT_DIRECT_STEP_MAX_TOOL_CALLS` | 1 |
| `AGENT_REACT_STEP_MAX_TOOL_CALLS` | 2 |
| `AGENT_PLANNER_TIMEOUT_SECONDS` | 15 |
| `AGENT_REPLANNER_TIMEOUT_SECONDS` | 30 |
| `AGENT_EXECUTOR_TIMEOUT_SECONDS` | 20 |
| `AGENT_PLANNING_MAX_TOKENS` | 1200 |

相同工具与规范化参数重复、越过步骤候选集、越过全局意图白名单或超过预算的动作都会被 Controller 拒绝，并把拒绝原因反馈给 Executor 在剩余决策预算内收口。

并行主批次还拒绝非只读工具、重复工具、参数 schema 不合法，以及语义上具有先后条件的替代工具对。Controller 条件替代同样受全局白名单、工具可用性、剩余预算和相同工具参数去重约束。每个主调用和替代调用都独立计入全局工具预算；并行只降低墙钟等待与 Executor 调用数，不放宽调用数量。

## 持久化与发布控制

- `AGENT_PLANNED_EXECUTION_ENABLED` 是规划路径总开关；关闭时不影响 `direct` 路径。
- Planner 返回后立即保存真实计划。
- action 请求在工具执行前保存，observation 与脱敏审计在工具结束后保存。
- 每次保存前检查当前 worker attempt 所有权。
- `(run_id, call_id)` 唯一索引防止工具审计重复。
- 最终助手消息仍保持每 run 唯一。

当前所有工具仍为只读。Finalizer 可以把调整建议标记为 `terminal_action=proposal`，但这只表示待确认文本提案，不会创建或执行写操作。

Finalizer 模型实际输出的是证据语义 outcome，而不是任意 terminal action。普通问答不能产生未经请求的 proposal；需要评估调整的请求通常仍由模型根据观察在“建议调整、无需调整、证据不足”之间动态判断。为消除同一完整反事实证据的终态漂移，Controller 只在两个保守边界收窄集合：明确低完成率只允许调整提案；偏好与计划频率一致、至少两周完成率不低于 80%，且没有额外或失败证据时只允许无需调整。其余情况仍动态判断，Controller 最终把语义 outcome 确定映射到 `answer|proposal`。

初始 Planner 超过 15 秒 deadline 时，不再无限等待或直接切换成通用工作流。Controller 只生成一次 `deadline_fallback_v1` 微计划：白名单内参数已知、相互独立的 2 至 3 个主证据直接合并为一个显式 `parallel_read`；聚合进度与训练历史这类替代关系不并行预取。主进度位于并行批次时由 Controller 在失败后调用历史；单独条件步骤仍可由 bounded ReAct 处理。该路径是超时降级，不参与正常 Planner 决策，也不能扩展工具权限。Replanner 仍使用独立的 30 秒 deadline，避免初始规划的延迟策略无意改变已有观察后的恢复边界。

## 验证

自动化测试覆盖：

- 相同请求在相反 observation 下选择不同工具路径；
- bounded ReAct 在主工具失败后使用相关替代工具；
- Planner 显式 `parallel_read` 的三工具批次确实并发，全部成功时 Executor 调用数为 0、运行时模型调用数为 2；
- 主证据成功时关闭不再需要的替代工具并跳过冗余步骤；主进度失败后由 Controller 直调历史且成功路径保持零 Executor；
- 活动训练仅在 `found=false` 时直调下一练，读取错误不会误触发替代；替代失败时仍唤醒一次 Executor；
- 证据组与其他成功观察覆盖全部动态白名单时跳过后续冗余步骤；存在独立未读工具时继续原执行路径；
- Planner deadline 降级对三项独立主证据仍生成一个三动作 `parallel_read`，不会退化为三个串行 Executor 步骤；
- 并行批次部分失败时保留全部 observation，只唤醒一次 Executor，且剩余步骤工具预算为 0；
- API 闭环使用独立数据库会话完成并行读取，并逐调用持久化唯一 action、observation 和工具审计；
- 观察依赖的工具对不能被误放入并行批次；
- completion policy 只在成功 direct observation 后自动完成，异常路径不会被误收口；
- 活动训练反事实以一个 bounded ReAct 步骤分别收敛到 1 次或 2 次工具调用；
- 计划适配只在“是否太激进/是否适合我”等明确个体适配语义下加入资料候选；
- direct 步骤不能越权增加第二次调用；
- 相同工具与参数不能重复；
- 一次动态重规划及计划版本记录；
- Replanner deadline 后停止新增工具调用，保留已有观察并通过 Finalizer 或固定安全说明完成 run；
- 第二次重规划请求被硬预算拒绝；
- 实时工具审计、助手消息和 worker 所有权保持唯一；
- 首批多步评测集及完整后端回归。
