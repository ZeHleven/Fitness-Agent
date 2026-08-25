# Tool Calling v2：Registry 只读 Enforce 切换契约

状态：只读 enforce 已在默认关闭开关后接线；固定行为一致性、回退测试及内部 `0.5.7`
真实模型 30 Run 门禁均已通过。该结论只关闭内部只读 enforce 验证，不授权生产放量或写工具，
2026-08-25。

## 决策

内部 shadow 与故障注入门禁已经支持进入“只读受控 enforce”的实现阶段，但不支持写工具、
跨 run 观察复用或生产放量。第一批只覆盖当前 7 个已注册、已启用、无副作用的认证用户读取工具。

运行时先保留 v1 路由供 shadow 比较，再把 Registry 本轮候选与 v1 allowlist、显式只读 cohort
取交集。有效结果统一进入 Trace、direct、planned、并行与条件替代的既有下游路径；不改变
Planner Prompt、工具实现或数据库访问。`AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED` 只在
受控内部观测 revision 中开启；生产及未单独批准的环境继续保持 `false`。

## 已通过的内部门禁

### Registry shadow

对部署包 `0.5.2` 的内部 100% shadow 窗口重新执行严格汇总：

| 指标 | 结果 | 门禁 |
| --- | ---: | ---: |
| sampled runs | 31 | 至少 30 |
| 完整 match / 合法 partial | 21 / 10 | partial 允许存在 skipped |
| check events | 186 | 每个 run 6 个 |
| 六类已执行 check 匹配率 | 全部 100% | 100% |
| mismatch / error | 0 / 0 | 必须为 0 |
| permission expansion | 0 | 必须为 0 |
| invalid/projector drop/adapter drop | 0 / 0 / 0 | 必须为 0 |
| comparator P50 / P95 / max | 1 / 2 / 2 ms | P95 不高于 5 ms |

严格观测门禁通过。10 个 `partial` 来自 direct、澄清或安全停止没有到达全部生命周期接点，
不是 Registry 差异。

### 故障注入与业务稳定性

部署 `0.5.3`（CloudBase deployment `010`）的内部复测结果：

- 低完成率调整：5/5 完成并产生 `adjustment_proposal`，每轮 3 次只读工具调用、2 次模型调用、
  0 次重规划；整轮 P50/P95 约 26.3/33.8 秒。
- 进度工具超时：5/5 completed、deterministic 和 hard-gate；每轮固定调用计划、失败的进度聚合
  和成功的历史替代，共 15 次工具调用；Executor/Replanner 均为 0，整轮 P50/P95 约
  21.9/25.9 秒。
- 固定行为一致性测试已覆盖 comparator 内部错误、指标 projector/adapter 错误、条件替代与
  工具失败；旁路错误 fail-open，不改变回复、终止动作、工具调用或预算。

这些结果只证明内部只读 enforce 的进入条件，不替代未来生产 7 天或生产 SLO 观测。

### 0.5.6 内部观测诊断

部署 `0.5.6`（CloudBase deployment `015`）的 30 Run 窗口中，Registry authority 30/30 为
`enforce`，无拒绝、权限扩张、工具数漂移或 legacy 回退；27 个可取得 Run trace 的 shadow
检查也无 mismatch/error。严格整轮门禁仍未通过：同步兼容端点 `/agent/chat` 有 3 次在 Run
已经创建并完成 authority 选择后返回 503，响应没有 `run_id`，观测器无法继续读取已落库的失败
trace；另有 1 次进度备用场景在首次工具前命中 20 秒 Executor deadline。

该窗口暴露的是请求生命周期与计划形状缺口，不是 Registry authority 差异。后续观测脚本改用
生产小程序相同的 durable `/agent/runs` 创建与轮询路径，报告 schema 升为 `1.1`；即使 worker
失败也保留 `run_id`、终态、error code 和阶段 trace。运行观测前必须确认
`AGENT_ASYNC_WORKER_ENABLED=true`。Controller 同时把“活动计划 + 聚合进度 + 条件历史”合法
模型计划保守归一为两动作 `parallel_read`，进度失败后才调用历史；Planner、Executor 或
Replanner deadline 后若 Finalizer 再失败，则固定安全收口而不再升级为同步 503。上述修复仍需
部署新内部版本并重跑 3+3 canary 与 30 Run，不能用本地测试结果替代真实模型门禁。

### 0.5.7 内部 30 Run 最终结论

部署 `0.5.7`（CloudBase deployment `016`）后，观测器沿用与小程序一致的 durable Run 创建与
轮询生命周期，在 100% shadow、100% enforce reads 的受控内部配置下完成最终 30 Run：

| 指标 | 结果 | 门禁结论 |
| --- | ---: | --- |
| attempted / completed / run ID | 30 / 30 / 30 | 全部 Run 可追踪并进入终态 |
| business gate / business failures | passed / 0 | 业务门禁通过 |
| shadow match / 合法 partial | 20 / 10 | partial 仅来自未到达全部接点的 direct、澄清或安全停止 |
| shadow mismatch / error | 0 / 0 | 无 Registry 行为差异 |
| Executor deadline | 0 | `0.5.6` 的执行前超时未复现 |
| Planner 调用 / deadline fallback | 20 / 14 | 安全降级完成，作为独立性能遗留 |

authority 结论采用连续两个窗口的组合证据：`0.5.6` 已记录 30/30 `authority_mode=enforce`，且无
拒绝、权限扩张、工具数漂移或 legacy 回退；`0.5.7` 在相同受控开关配置下补齐 durable 请求
生命周期，30/30 业务完成且已执行 shadow check 全部匹配。没有证据表明 Registry enforce 导致
完成率下降、权限变化或调用预算漂移，因此内部只读 enforce 门禁通过，本阶段可以收口。

该结论不继承到生产流量、写工具、proposal/execute、freshness、跨 run 复用或缓存；这些能力仍
必须另立契约、门禁与回滚方案。

### 两个非阻塞遗留

1. **窗口外 `403` 请求来源**：最终窗口后观察到 11 次 `403`，涉及 10 个唯一 Run ID。现场鉴权
   探针确认缺少或不可解析的 Bearer 在 `HTTPBearer` 层返回 `403`，无效或过期 Bearer 返回
   `401`；因此这不是 Registry 拒绝或 token 过期，也没有形成越权。主观测器始终复用认证 header
   且同步退出，后端没有自调用该 Run 查询端点；现有 CloudBase 访问日志缺少请求 ID、客户端类别
   和安全的鉴权状态字段，尚不能唯一归因迟发客户端。该项转入独立的请求来源与鉴权可观测性任务，
   不阻塞内部只读 enforce 收口。
2. **Planner deadline fallback 比例**：20 次 Planner 调用中 14 次在 15,001 至 15,003 ms 命中
   15 秒 Controller deadline；所有 Run 随后通过受限 fallback 完成，工具批次不是主要耗时，且
   Registry shadow/authority 未出现差异。这是既有 Planner 延迟与分段遥测缺口，不是 Registry
   回归。后续应在独立性能任务中记录请求次数、响应头等待、生成/解析与取消阶段，再决定是否调整
   prompt、重试或 deadline；不得用本批 30 个场景直接调参。

## 切换状态

```text
flag=false
  legacy v1 是唯一 authority
  shadow 可独立开关并继续观测

flag=true
  effective read tools = legacy allowlist ∩ registry read cohort
  Registry 可以收窄，不能扩大 v1 权限
  callable 实现和数据库访问仍复用 v1 read runtime

registry internal error
  当前 run 回退到已验证的 legacy read runtime
  记录低基数 fallback 事件并停止继续放量
  关闭 flag 后恢复纯 legacy authority
```

每个开启 enforce 的 Run 都写入 `agent_tool_registry_read_authority` 结构化日志，只包含 run ID、
authority mode、稳定原因码和工具数量，不包含用户、Prompt、参数或结果。投影或 selector 异常
只记录 `registry_internal_error`，当前 Run 使用 legacy allowlist。

唯一主开关：

```text
AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED=false
```

该开关与四个 shadow 开关相互独立。关闭 enforce 后可以保留 shadow 指标用于诊断；完全回滚时
同时关闭 enforce 与 shadow。配置在新容器或新 revision 启动时读取。

## 第一批 authority 范围

显式 cohort 固定为：

1. `profile.get_summary`
2. `health.get_screening_summary`
3. `plan.get_active`
4. `workout.get_next`
5. `workout.get_active_session`
6. `workout.list_history`
7. `workout.get_progress`

新工具即使被加入 Registry，也不得自动进入这一 cohort，必须另改显式列表并重新执行门禁。

纯 selector 接收的是“本轮 Registry 推导出的候选工具事实”，不是完整静态目录；完整目录先按
结构化意图投影成本轮候选，再进入权限交集。这样不会把与当前意图无关的已注册工具误报为
`permission_expansion`。

首批 Registry authority 只覆盖：

- `route_allowlist`
- `constructed_tools`
- `argument_schema`
- `parallel_policy`
- `conditional_evidence`

`observation_semantics` 仍由现有运行时解释，因为 7 个工具当前都返回 `legacy_mapping`，尚无严格
输出模型和统一 envelope。freshness、跨步骤复用、缓存、proposal 与 execute 工具同样不在本批。

## 强制不变量

- 有效工具集合必须取 legacy 与 Registry 的交集，禁止并集或 Registry 单方面扩权。
- cohort 中每项必须同时满足 `availability=active`、`mode=read`、`side_effects=none`。
- `user_id` 继续只从认证服务端上下文注入，模型不能提供或覆盖。
- 参数仍由现有严格 Pydantic schema 校验；Registry 不能放宽 `extra=forbid` 或参数上下界。
- 并行和条件替代只有 v1 与 Registry 同时允许时才允许；证据组两端仍必须在有效白名单内。
- Registry 缺项只能导致能力收窄或证据不足，不能回退为未经授权的模型自由调用。
- 首批不得注册、暴露或执行 `proposal`、`execute`、`proposal_only` 或 `writes_data` 工具。
- enforce 异常不得触发额外模型、工具、Replanner 或数据库调用。

## 进入运行时接线的门禁

实现提交必须继续满足：

- 纯函数 authority selector 对相等、Registry 收窄、Registry 扩张、未知工具、内部错误均有固定夹具；
- 扩张输入的有效结果仍为交集，并记录 `permission_expansion`；
- flag 关闭时 trace `1.0/1.1`、回复、卡片、调用、预算与当前版本逐字段一致；
- flag 开启时 7 个只读工具的 direct、planned、parallel、conditional fallback 全量通过；
- comparator 与 enforce 适配器故障注入不改变业务完成状态；
- 真实内部模型至少再跑 30 个 enforce reads Run，mismatch、fallback 和权限扩张均为 0；
- 任何写能力另立契约、确认门禁和回滚方案，不继承本批结论。

## 快速回退

以下任一条件立即停止放量：权限扩张、Registry mismatch/error、legacy fallback、完成率下降、
终止动作漂移、工具/模型预算上升或 P95 明显回归。

回退步骤：

1. 设置 `AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED=false` 并启动新 revision/容器；
2. 保留 shadow 时确认新 Run 已回到 legacy authority，并用 trace/指标定位原因；
3. 如观测链路也异常，再关闭 shadow 四项配置；
4. 无需数据库迁移、业务数据修复或 Trace 清理。

## 后续实现顺序

1. `test: define registry read authority selector cases`（已完成）
2. `feat: add registry read authority selector`（已完成，纯函数未接运行时）
3. `feat: enable optional registry read enforcement`（已完成，默认关闭）
4. `test: verify registry read enforcement parity and rollback`（已完成）
5. 内部 100% enforce reads 真实模型观测（已由 `0.5.7` 30 Run 完成并通过）；更大范围另立决策。
