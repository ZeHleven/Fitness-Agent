# Tool Calling v2：Registry 只读 Enforce 切换契约

状态：切换契约已定义、运行时尚未接线、默认关闭，2026-08-24。

## 决策

内部 shadow 与故障注入门禁已经支持进入“只读受控 enforce”的实现阶段，但不支持写工具、
跨 run 观察复用或生产放量。第一批只覆盖当前 7 个已注册、已启用、无副作用的认证用户读取工具。

本批只定义配置、数据模型、权限交集和回滚契约，不改变 Planner Prompt、路由、工具构建、
执行结果或用户回复。`AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED` 在运行时接线完成并通过独立
评测前必须保持 `false`。

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

## 切换状态

```text
flag=false
  legacy v1 是唯一 authority
  shadow 可独立开关并继续观测

flag=true（后续实现）
  effective read tools = legacy allowlist ∩ registry read cohort
  Registry 可以收窄，不能扩大 v1 权限
  callable 实现和数据库访问仍复用 v1 read runtime

registry internal error
  当前 run 回退到已验证的 legacy read runtime
  记录低基数 fallback 事件并停止继续放量
  关闭 flag 后恢复纯 legacy authority
```

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

1. `test: define registry read authority selector cases`
2. `feat: add registry read authority selector`
3. `feat: enable optional registry read enforcement`
4. `test: verify registry read enforcement parity and rollback`
5. 内部 100% enforce reads 真实模型观测；通过后再讨论更大范围。
