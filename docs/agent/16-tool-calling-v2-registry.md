# Tool Calling v2：现有工具审计与 Registry 契约

状态：`design_only`，2026-08-22。本文和对应静态模型不接入 Planner、Controller、Prompt、工具构建或生产执行路径。

## 目标与边界

Tool Calling v1 已具备动态白名单、严格输入参数、只读执行、并行批次、条件替代、预算、去重和审计。v2 第一阶段只建立一个可机器校验的 Registry 基线，用于回答：

- 当前到底有哪些真实可用工具；
- 每个工具允许解决什么问题、禁止解决什么问题；
- 输入、输出、权限、新鲜度、敏感度和审计契约是什么；
- 哪些字段描述当前事实，哪些只是尚未启用的 v2 策略；
- 后续迁移是否与 v1 路由和运行时发生漂移。

本阶段明确不做：新增工具、改变路由、复用 observation、缓存结果、统一输出 envelope、开放写权限、引入 RAG/MCP，或用 Registry 替换现有运行时常量。

## 实际可用工具审计

旧版设计文档列出了未来候选工具，但截至本次审计，模型运行时只构建以下 7 个认证用户只读工具：

| 工具 | 严格输入 | 当前成功/无数据语义 | 数据来源 | 敏感度 | v2 run 内新鲜度草案 |
| --- | --- | --- | --- | --- | ---: |
| `profile.get_summary` | 无参数 | `found=true/false` | `user_profiles` | 个人 | 300 秒 |
| `health.get_screening_summary` | 无参数 | `found=true/false` | `user_profiles` | 健康敏感 | 300 秒 |
| `plan.get_active` | 无参数 | `found=true/false`，有数据时返回完整计划 | `workout_plans`、`plan_exercises` | 个人 | 60 秒 |
| `workout.get_next` | 无参数 | `found=false + reason`，或下一训练日与动作 | 活动计划、系统日期 | 个人 | 60 秒 |
| `workout.get_active_session` | 无参数 | `found=true/false` | `workout_sessions`、`workout_sets` | 个人 | 15 秒 |
| `workout.list_history` | `limit=5`，范围 1–20 | `count=0` 是成功空结果 | 训练场次与训练组 | 个人 | 60 秒 |
| `workout.get_progress` | `weeks=8`，范围 1–52 | 零次数/组数/容量是成功聚合 | 训练场次与训练组 | 个人 | 300 秒 |

新鲜度和失效事件是 v2 的保守目标契约，当前运行时不会读取或执行这些字段。首个激活范围只允许同一 run 内复用，不允许跨轮或跨用户复用。

## 已确认的 v1 强项

- `user_id` 从认证服务端上下文闭包注入，从不接受模型参数。
- 所有输入模型 `extra=forbid`，历史条数和进度周数有上下界。
- 全部 7 个工具无副作用，并处于并行安全集合。
- 意图路由只产生固定动态白名单，未知或写工具 fail closed。
- action、observation、结果摘要、结果指纹和逐调用审计已持久化。
- `get_progress --on_error--> list_history` 与 `get_active_session --on_not_found--> get_next` 已由 Controller 确定执行。

## 审计缺口

| 缺口 | 当前事实 | v2 影响 |
| --- | --- | --- |
| 输出 schema | 7 个工具都直接返回异构 `dict`，没有严格 Pydantic 输出模型 | Controller 仍需理解 `found`、`count` 和隐式零聚合等多种语义 |
| Observation envelope | 工具成功值没有统一的 `status/data/observed_at/data_version/fresh_until` 外层 | 暂时不能安全做通用复用和过期判断 |
| 新鲜度与失效 | 当前没有工具级 freshness 或 invalidation 元数据 | 多轮调用只能靠预算和动作指纹去重 |
| 错误码 | 工具异常由 Controller 归一为异常类名，工具没有稳定领域错误码 | 只能区分通用 error/retryable，不能表达业务冲突 |
| 元数据来源 | 名称、路由、描述、并行安全和替代关系分散在多个模块 | 新增工具容易产生契约漂移 |
| 输出体积 | `plan.get_active` 与 `list_history` 返回完整详情 | 后续需要结果大小预算和摘要/详情分层 |
| 敏感度 | 运行时有脱敏摘要，但工具本身没有声明式敏感度标签 | 难以按工具自动选择日志、缓存与留存策略 |

旧文档要求“严格 Pydantic 输入/输出模型”，本次审计确认只有输入侧已经落实。Registry 将当前输出如实标记为 `legacy_mapping`、`strict_output_schema=false`，不能把目标状态写成已实现状态。

## Registry v2 数据模型

Registry 根对象包含：

```text
registry_version
status = design_only | shadow | active
tools[]
conditional_evidence[]
```

每个 `ToolRegistryEntry` 包含：

```text
tool_id / contract_version / langchain_name / title
mode / availability / side_effects / risk_level / data_sensitivity
supported_intents / use_cases / exclusions / data_sources
parallel_safe
arguments
observation
freshness
audit
```

关键子契约：

- `arguments`：Pydantic schema 引用、默认参数、是否允许额外字段、身份来源。
- `observation`：当前输出形态、严格输出 schema 状态、无数据语义、异常行为。
- `freshness`：目标复用范围、最大年龄和失效事件；`design_only` 时不执行。
- `audit`：规范化参数、摘要与指纹、身份是否落日志、敏感结果字段。
- `conditional_evidence`：主工具、替代工具、固定触发条件、默认参数和禁止推测式并行。

静态实现位于：

- `backend/app/schemas/agent_tool_registry.py`
- `backend/app/services/agent_tool_registry.py`

Registry 自身拒绝重复工具 ID、重复 LangChain 名称、重复证据组、未知工具引用，以及把有副作用工具标为并行安全。

## 七工具权限结论

本阶段全部工具保持：

```text
mode = read
availability = active
side_effects = none
risk_level = low
identity_source = server_context
argument_storage = normalized
result_storage = summary_and_fingerprint
```

`health.get_screening_summary` 的数据敏感度为 `health_sensitive`；其他用户资料、计划和训练数据为 `personal`。风险等级描述副作用和执行权限，不代表健康数据不敏感。

## 条件证据契约

| 证据组 | 主证据 | 触发 | 替代证据 | 预取 |
| --- | --- | --- | --- | --- |
| `progress_or_history` | `workout.get_progress` | `on_error` | `workout.list_history` | 禁止 |
| `active_session_or_next_workout` | `workout.get_active_session` | `on_not_found` | `workout.get_next` | 禁止 |

两端仍必须已经进入本轮动态白名单。Registry 不增加权限，也不改变现有 Controller 逻辑。

## 激活路线

1. **design_only（当前）**：Registry 只被契约测试导入，CI 校验它与 v1 工具、路由、参数 schema 和条件证据组完全一致。
2. **shadow**：运行时继续使用 v1，但旁路比较 Registry 推导结果；任何差异只记录指标，不影响调用。
3. **registry catalog**：在功能开关下由 Registry 生成模型目录和并行/权限校验，v1 仍可回退。
4. **normalized observation**：逐工具增加严格输出模型，再统一 envelope；不能一次性包裹后假装内部数据已经强类型。
5. **per-run evidence state**：只复用满足 freshness、参数指纹和失效版本的同 run 观察。

进入 shadow 前必须补齐至少 30 个 Tool Calling 专项用例，并保持越权调用为 0、参数合法率 100%、条件替代门禁 100%。

## 零运行时变化证明

- 新 Registry 状态固定为 `design_only`。
- `agent_tools.py`、`agent_intent.py`、Planner 和 Controller 不导入 Registry。
- 现有 `READ_TOOL_IDS`、工具构建、路由、白名单、Prompt、预算和执行流程不改。
- 新测试只验证静态契约与 v1 事实一致，不改变测试夹具行为。

只有后续独立提交显式进入 shadow 阶段时，才允许运行时代码读取 Registry。
