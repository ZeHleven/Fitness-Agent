# 训练计划调整 Proposal 生命周期与安全不变量

状态：`internal_canary_passed`，运行时默认关闭，2026-08-26。本文既保留 Agent 发起训练计划
调整提案的设计与安全不变量，也记录已经落地的数据库约束、Controller 接线、认证 API、小程序
交互和内部真实模型门禁。Planner/Executor 仍未获得计划写工具或 execute authority。

## 决策

第一项可确认动作只覆盖 `plan_adjustment_v1`。模型负责基于只读证据表达调整建议，Controller
负责判断本轮是否允许形成 proposal，服务端负责把建议解析、校验并固化为不可变候选计划；只有
当前登录用户通过专用确认 API 明确授权后，确定性执行器才可以修改业务状态。

模型输出不是授权。Planner、Executor、Replanner 与 Finalizer 都不得直接调用计划写入函数，
也不得把自然语言中的“确认”“照做”或工具名当成执行凭证。首版只接受小程序确认控件调用的
认证 API，不接受聊天消息直接触发执行。

首版采用本地数据库内的短事务，不为确定性计划写入另建模型工作流或分布式步骤队列。若未来
执行包含外部系统或长事务，再另立异步执行契约。

## 当前基线与范围

当前有三类容易混淆的能力：

1. `terminal_action=proposal` 本身仍只是待确认语义，不是修改授权。只有 proposal flag 开启、
   typed draft 和服务端证据门禁通过时，Controller 才会创建 `AgentProposal` 并在响应中返回引用；
   flag 关闭或门禁拒绝时保持 legacy 只读行为。
2. `agent_proposals` 表已经具备所有权、不可变 payload、fingerprint、状态版本、过期、幂等决策和
   原子应用约束；运行时支持创建、所有权隔离读取、确认、拒绝、并发保护和结果重放。
3. 个性化计划已有独立的 preview/confirm API；训练完成接口还有既有自适应调整逻辑。两者不是
   Agent proposal 的权限来源，不能绕过本文的确认、并发和审计不变量。是否将训练完成后的自动
   调整迁移到 Proposal 生命周期，必须另立产品决策。

本批只定义“基于当前活动计划生成完整候选计划，用户确认后创建新活动计划版本”的闭环。
不允许对现有 `WorkoutPlan` 或 `PlannedExercise` 做不可回放的原地修改。

## 非目标

- 不开放通用 JSON Patch、任意字段路径、SQL 或 ORM 主键写入。
- 不支持自动确认、批量确认、管理员代确认或跨用户 proposal。
- 不支持训练记录删除、训练完成、组次记录、饮食写入或外部设备同步。
- 不把 `proposal` 扩展到 RAG、MCP、freshness、缓存或跨 run observation 复用。
- 不让 Registry read-enforce 的通过结论自动继承到 proposal 或 execute authority。
- 不把模型生成的自然语言直接保存为可执行 payload。

## 信任边界

```text
模型与只读 observation
  不可信建议
        │
        ▼
Finalizer 选择 adjustment_proposal
        │
        ▼
Controller outcome/证据/权限门禁
        │
        ▼
服务端 Proposal Builder
  解析候选计划 · 重新查询 · 严格校验 · 生成 before/after
        │
        ▼
AgentProposal(pending_confirmation)
        │
        ▼
小程序展示完整 diff 与风险说明
        │
        ▼
认证用户 confirm API
        │
        ▼
确定性执行器
  行锁 · 基线校验 · 健康校验 · 幂等事务
        │
        ▼
新 WorkoutPlan + PlannedExercise 版本
```

信任级别从低到高依次为：模型建议、服务端验证后的 proposal、用户确认、事务提交结果。任何上游
文本都不能跳过下一层验证。

## 创建 Proposal 的门禁

只有同时满足以下条件，Controller 才可以请求 Proposal Builder：

- Agent Run 属于当前认证用户且仍由当前 worker attempt 持有；
- Finalizer 的 `selected_outcome=adjustment_proposal`，且 Controller 已确定映射为
  `terminal_action=proposal`；
- 本轮意图明确要求评估或调整计划，普通查询不能产生 proposal；
- `plan.get_active` 成功返回活动计划，且 proposal 指向该计划；
- 与调整理由相关的证据已成功取得：完成率使用 progress/history，偏好使用 profile，健康限制
  使用 screening；缺失或失败证据不能伪装成确定事实；
- proposal feature flag 开启，proposal 类型和候选操作位于显式 cohort；
- 本轮没有健康红旗、`safe_stop`、未解决澄清或 deadline 后的证据不足终态；
- 服务端能够把模型建议解析为严格候选计划，并重新通过业务、安全和参数校验。

门禁失败时最多保留只读文本建议或返回 `insufficient_evidence`，不得创建“看似可确认但无法安全
执行”的记录。

## 模型草案与服务端 Payload

proposal flag 开启时，Finalizer 使用 `ProposalFinalizationDecision`；
`outcome=adjustment_proposal` 必须附带通过 `PlanAdjustmentProposalDraft` 校验的 typed
`proposal_draft`，其他 outcome 禁止携带 draft。模型最多表达调整类型、计划中的日序/动作位置、
期望值、理由和安全提醒；不能提供 `user_id`、ORM 主键、状态、版本、确认信息或任意字段路径。
Planner 在 Controller 边界前把强类型 draft 转成紧凑 JSON，既保持现有运行时接口，也不放宽
服务端 Builder 的二次验证。

Proposal Builder 必须使用日序、动作顺序等服务端可验证定位重新解析当前计划，重新查询所有业务
实体，并生成规范化完整候选计划。无法唯一定位、值超界、候选动作不安全或模型草案与当前计划不
一致时，拒绝创建 proposal。自然语言 reply 只用于展示，永远不参与执行。

## Proposal Payload v1

`payload_data` 必须先通过版本化 Pydantic schema，再允许持久化。首版保存完整候选计划，而不是
开放式 patch：

```text
schema_version = 1.0.0
proposal_type = plan_adjustment_v1

target
  resource_type = workout_plan
  base_plan_id
  base_plan_fingerprint

before
  规范化活动计划快照

after
  完整候选计划快照

changes[]
  change_type
  stable_display_key
  before
  after
  reason
  safety_priority

evidence[]
  tool_id
  result_fingerprint
  observed_at

rationale[]
safety_notes[]
```

`after` 中可执行字段必须显式列举并复用或收窄现有计划 schema 的边界；不能携带 `user_id`、
`is_active`、数据库时间、任意表名或任意字段路径。动作、计划与用户归属均由服务端重新查询。

proposal 只保存形成 diff 和执行所需的最小数据。不复制原始 Prompt、完整多轮消息、健康 observation
或模型原始输出；健康证据只保存工具 ID、结果指纹和最小安全说明。

## 最小持久化状态机

首版执行只涉及本地数据库，因此不引入长期 `confirmed/applying` 中间状态。确认和计划版本切换在
同一短事务中完成：

```text
pending_confirmation
  ├─ confirm + 校验和提交成功 ──→ applied
  ├─ reject ────────────────────→ rejected
  ├─ expires_at 到期 ───────────→ expired
  ├─ 基线计划或健康上下文变化 ──→ stale
  └─ 确定性非重试错误 ─────────→ failed
```

`applied`、`rejected`、`expired`、`stale` 和 `failed` 都是终态。用户要求修改 proposal 时必须创建新
proposal，不能修改 pending payload。若未来出现外部副作用或长事务，再增加独立 execution attempt
与 `confirmed/applying` 状态，不能提前为假设场景扩展本状态机。

状态转换必须使用 `expected_version` 做 compare-and-swap；非法转换返回稳定 `409`，不能静默成功。
`pending_confirmation` 默认有效 24 小时，配置上限为 72 小时；确认时以服务端时间重新判断，客户端
显示时间不构成有效性依据。

## 创建幂等

- 首版每个 Agent Run 最多创建一个 `plan_adjustment_v1` proposal；
- 唯一键应覆盖 `run_id + proposal_type`，payload fingerprint 作为审计字段；
- worker 重试、租约接管或 Finalizer 重放必须返回同一 proposal；
- proposal payload 一旦进入 `pending_confirmation` 就不可变；
- 同一用户产生新 proposal 时，旧 pending proposal 可以显式转为 `stale`，但不得被覆盖或删除。

## 确认与拒绝 API

目标接口：

```text
GET  /api/v1/agent/proposals/{proposal_id}
POST /api/v1/agent/proposals/{proposal_id}/confirm
POST /api/v1/agent/proposals/{proposal_id}/reject
```

confirm/reject 请求至少携带：

```text
expected_version
client_request_id
```

API 必须：

- 从 JWT 注入 `user_id`，不接受请求体中的用户 ID；
- 对不存在和非本人 proposal 统一返回 404；
- 对重复的同一 `client_request_id` 返回相同结果；
- 对不同请求重复确认已 applied proposal 返回原 applied 结果，不再次创建计划；
- 对已拒绝、过期、stale 或 failed proposal 返回稳定冲突错误；
- confirm 成功后只根据数据库提交结果返回 `applied=true` 和新计划 ID；
- reject 永远不修改计划业务表。

仅在聊天中回复“确认”“可以”“照做”不构成首版授权。小程序必须展示 proposal ID 对应的完整
before/after、过期时间和确认按钮，再调用专用 API。

## 确认时的重新验证

确认不是对旧模型输出盖章。执行器必须在事务中重新执行：

1. 锁定 proposal、当前活动计划和相关计划动作；
2. 检查 proposal 所有权、状态、版本、过期时间和幂等键；
3. 重新计算活动计划规范化 fingerprint，与 `base_plan_fingerprint` 比较；
4. 确认 `base_plan_id` 仍是当前活动计划；
5. 重新查询候选动作是否存在、启用且属于允许范围；
6. 使用最新用户资料和健康筛查重新执行安全兼容性校验；
7. 重新验证训练日数量、动作唯一性、组数、次数、休息和重量等边界；
8. 任一事实变化或校验失败时不写业务表，并把 proposal 标记为 `stale` 或返回稳定错误。

模型提供的理由不能覆盖这些确定性验证。

## 原子执行与可恢复性

确认成功必须在一个数据库事务内：

1. 创建新的 `WorkoutPlan` 和完整 `PlannedExercise` 集合；
2. 将原活动计划设为非活动；
3. 将新计划设为活动；
4. 将 proposal 设为 `applied`，记录 `confirmed_at`、`applied_at`、结果计划 ID 和新计划 fingerprint；
5. 写入不含敏感原值的执行审计。

任一步失败必须整体回滚，不能出现半份计划、两个活动计划或 proposal 声称 applied 但业务数据未
提交。数据库应增加“每个用户最多一个活动计划”的约束或等价事务保护。

首版通过创建新计划版本而不是原地修改保留回退基础。回退旧计划仍必须由显式用户操作或单独
契约触发，执行失败不得自动猜测并切换计划。

## 安全不变量

### 权限

- 模型永远不能提供或覆盖 `user_id`、目标计划归属或确认身份；
- proposal、基础计划、新计划和确认用户必须属于同一用户；
- Planner/Executor 工具目录不得包含 execute 能力；
- Registry proposal authority 只能收窄 legacy proposal cohort，不能扩大权限；
- flag 关闭时不得创建 `AgentProposal` 记录，也不得改变当前文本 proposal、只读工具调用、Trace
  或预算。

### 用户授权

- proposal 创建不是计划修改；
- 只有专用认证 confirm API 是首版执行授权；
- 用户看到的 diff 必须与最终执行 payload fingerprint 一致；
- proposal 到期、被拒绝或基线变化后不能确认；
- 任何默认勾选、静默确认或模型代确认都被禁止。

### 健康安全

- 命中健康红旗的 Run 不得形成可执行 proposal；
- 涉及动作替换或负荷变化时必须重新执行最新健康兼容性校验；
- 模型不能把医疗诊断写入 proposal，也不能用“用户要求”绕过安全边界；
- 健康上下文变化导致验证结果不同，应标记 proposal `stale`，要求重新评估。

### 数据完整性

- 不允许自由文本、任意 JSON Patch 或未知字段进入执行器；
- confirm 必须有行锁、版本检查、基线 fingerprint 和幂等键；
- 计划版本切换与 proposal 终态必须同事务提交；
- 重试不能创建第二份计划或重复停用计划；
- 只有数据库提交成功后，API、消息和 Trace 才能声称已应用。

### 隐私与审计

- 不保存原始 Prompt、模型原始输出或完整健康 observation；
- 审计只记录 proposal ID、类型、状态、版本、fingerprint、结果资源 ID、稳定错误码和耗时；
- 日志不得包含计划详情、伤病原值、JWT、用户消息或模型回复；
- 所有创建、确认、拒绝和执行结果都必须能关联 source run，但 run 删除后业务计划仍保持有效。

## Registry 边界

首版未来可注册：

```text
plan.propose_adjustment
  mode = proposal
  side_effects = proposal_only
  invocation_owner = controller
  parallel_safe = false
```

`invocation_owner` 是拟新增契约字段：proposal 持久化由 Controller 在 Finalizer 契约通过后触发，
不作为 Planner/Executor 可自由调用的 LangChain tool。实际计划应用是确认 API 后的内部服务能力，
首版不注册为模型可见的 `plan.apply_adjustment` 或 `plan.confirm`。

proposal Registry authority 必须使用独立 feature flag、cohort、固定夹具和回滚契约，不能复用
`AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED` 推导权限。

## 错误契约

首版至少定义以下稳定错误码：

| 错误码 | 语义 | 是否修改业务数据 |
| --- | --- | --- |
| `proposal_not_pending` | 状态不允许确认或拒绝 | 否 |
| `proposal_version_conflict` | expected version 已变化 | 否 |
| `proposal_expired` | proposal 已到期 | 否 |
| `proposal_base_plan_changed` | 活动计划或 fingerprint 已变化 | 否 |
| `proposal_health_context_changed` | 最新健康校验不再允许 | 否 |
| `proposal_payload_invalid` | payload 未通过严格 schema | 否 |
| `proposal_candidate_unavailable` | 动作不存在、停用或不再兼容 | 否 |
| `proposal_execution_conflict` | 并发确认或活动计划冲突 | 否 |
| `proposal_execution_failed` | 确定性内部执行失败 | 否，事务回滚 |

错误信息只能描述可操作的下一步，不得把内部表名、SQL、Prompt 或敏感事实返回客户端。

## Trace 与指标

Proposal 不能伪装成普通 read action。当前 Trace `1.2` 使用独立、可选的
`proposal_creation` 诊断，只记录创建资格、稳定拒绝原因、持久化状态和稳定持久化拒绝原因；
Assistant/Run 响应在成功创建时另返回最小 proposal 引用：

```text
proposal_id
proposal_type
status
payload_fingerprint
```

低基数指标至少覆盖：创建、确认尝试、应用、拒绝、过期、stale、失败和幂等重放。指标与日志不得
使用 user ID、proposal ID、run ID 或错误详情作为 label。

## 固定测试门禁

运行时接线前至少覆盖：

- 普通回答、无需调整和证据不足均不创建 proposal；
- 合法调整只创建一份不可变 pending proposal；
- flag 关闭时与当前只读运行逐字段一致；
- 模型输出 execute 工具名、用户 ID、任意 patch 或未知字段时被拒绝；
- 非本人读取、确认和拒绝统一返回 404；
- 重复 worker、重复创建和重复确认保持幂等；
- 过期、基线计划变化、健康上下文变化和动作停用均阻止执行；
- 两个并发确认只有一个事务创建新计划；
- 任意事务中点失败后没有半份计划、双活动计划或错误 applied 状态；
- reject 不修改计划；applied 返回的新计划与用户确认的 fingerprint 完全一致；
- 关闭 proposal flag 可以恢复纯只读 Agent，无需修改业务数据或清理历史 proposal。

## 实现顺序

1. `test: define plan adjustment proposal contract cases`
2. `feat: add plan adjustment proposal schemas`
3. `feat: add proposal lifecycle persistence constraints`
4. `feat: build validated plan adjustment proposals`
5. `feat: persist optional plan adjustment proposals`
6. `test: verify proposal creation parity and rollback`
7. `feat: add proposal read confirm and reject APIs`
8. `feat: apply confirmed plan adjustment atomically`
9. 小程序展示 before/after、过期状态和显式确认控件
10. 内部固定夹具、并发/故障注入与小型真实模型 canary 通过后，再讨论扩大 proposal 范围

每一步保持独立提交。前六步都不得让模型获得 execute 权限；第七、八步的执行入口只属于认证用户
确认 API。

## `0.5.11 / 022` 内部真实模型门禁

观测时间：2026-08-26。来源提交为 `2dbae10`，CloudBase 部署 `022` 承载 100% 流量。
内部环境开启 Proposal flag、Registry read enforcement 和 100% Registry shadow；`/health` 与
`/ready` 均返回 200，数据库 ready 且 Agent worker 启用。所有样本都使用隔离合成账号、合成计划
和合成训练记录，不读取或修改真实用户数据。

### 创建与只读基线

| 门禁 | 结果 |
| --- | --- |
| 只读 `direct_next_workout` | 1/1 完成；唯一工具为 `workout.get_next`；零重规划；shadow 无 mismatch |
| 低完成率 Proposal Run | 3/3 completed，3/3 `terminal_action=proposal` |
| typed draft 与 Builder | 3/3 eligible；draft schema/build rejection 为 0 |
| Proposal 持久化 | 3/3 `persistence_status=created` |
| Registry 一致性 | 3/3 六项检查 match；mismatch/error 为 0 |
| 业务门禁 | 通过 |

三次低完成率 Run 的服务端 duration 约为 32.9–49.0 秒。三次初始 Planner 都命中 15 秒 deadline
fallback，但 fallback 仍生成固定的三证据只读计划，Finalizer 随后成功形成 typed draft，未影响
Proposal 创建和持久化结果。

### 决策生命周期

| 场景 | 结果 |
| --- | --- |
| reject | 1/1：`pending_confirmation → rejected`，版本 `1 → 2` |
| reject 幂等与计划隔离 | 同一 `client_request_id` 重放结果一致；终态无 allowed action；原计划保持唯一活动计划 |
| confirm | 1/1：`pending_confirmation → applied`，版本 `1 → 2` |
| confirm 幂等与原子应用 | 重放结果一致；只创建一个结果计划；新计划成为唯一活动计划，旧计划失活；结果 ID/fingerprint 与 GET 终态一致 |
| 生命周期业务门禁 | 2/2 通过 |

reject 和 confirm 样本的 Proposal 生成阶段分别约为 91.2 秒和 52.4 秒。该耗时包含真实模型
Planner/Finalizer 和 Run 轮询，不能解释为确定性 decision 事务耗时。

### 结论与非阻塞遗留

`0.5.11 / 022` 通过 Proposal 后端内部观测门禁：本轮 typed Finalizer 修复把此前真实模型样本中的
`proposal_draft_schema_invalid` / `proposal_draft_invalid` 从阻塞问题收敛为 0/3，创建、读取、拒绝、
确认、幂等重放和原子计划切换均取得真实部署证据。该结论只允许收口当前
`plan_adjustment_v1` 范围，不授权新增模型可见写工具、聊天确认、其他 proposal 类型或生产放量。

保留两个非阻塞遗留：

1. 小样本中 Planner deadline fallback 为 3/3，需要作为延迟与模型稳定性指标继续观察，但没有
   破坏业务正确性，不阻塞当前 Proposal v1 收口。
2. Proposal 生成端到端长尾达到约 91 秒，需要在真实产品试用中评估等待体验；优化时必须区分
   模型生成/轮询耗时与确定性 confirm/reject 事务耗时，不能为追求延迟而放宽 typed draft、证据或
   用户确认不变量。

下一门禁是小程序真实交互验收：详情 before/after、二次确认、按钮锁、结果不确定时 GET 核实、
幂等手动重试，以及并发/过期状态提示。通过前不扩大 Proposal cohort。
