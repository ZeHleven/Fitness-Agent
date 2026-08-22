# Tool Calling v2：Registry Shadow 设计

状态：shadow 旁路已实现、默认关闭，2026-08-22。Registry 保持非权威，本文不启用生产 shadow 流量。

## 目标

Shadow 阶段要证明 Registry v2 能在不影响用户请求的前提下，独立推导出与 v1 相同的工具边界和观察语义。它不是第二套 Agent，也不是灰度接管：

```text
v1 决策与执行 ─────────────────────────────→ 用户结果
      │
      └─ 脱敏事实副本 → Registry comparator → shadow report/metrics
                                      └─────╳ 不回写 v1 控制流
```

必须保持的硬不变量：

- 模型调用数、工具调用数、工具参数、回复、卡片、终止动作和预算与关闭 shadow 时完全一致；
- 不创建第二个工具实例，不访问数据库，不调用外部 API；
- Registry 结果永不加入白名单、候选工具或 Planner Prompt；
- comparator 失败只产生脱敏的 shadow error，不得使 Agent run 失败；
- shadow 不启用 freshness 复用，不改变条件替代或并行执行；
- 功能开关关闭时不构造报告，开销接近零。

## 比较接点

首版 shadow 包含六类纯本地检查：

| 检查 | v1 事实接点 | Registry 独立推导 | 明确不比较 |
| --- | --- | --- | --- |
| `route_allowlist` | `route_tools` 返回后 | 根据主意图、展开意图、风险/澄清门控和最多 4 工具规则推导有序白名单 | 用户原文、模型意图 Prompt |
| `constructed_tools` | `build_read_tools` 返回后 | 工具 ID、顺序和 LangChain 名称 | 工具执行、数据库结果 |
| `argument_schema` | `build_tool_catalog` 后 | schema 引用、默认值、额外字段策略及结构指纹 | 本次真实参数值 |
| `parallel_policy` | 计划边界校验时 | 工具是否只读、无副作用、parallel safe，条件对是否被预取 | 修改或拒绝计划 |
| `conditional_evidence` | 主观察完成及 fallback 结束后 | 根据主状态推导是否需要固定替代，比较实际 fallback 工具 ID | 触发额外 fallback |
| `observation_semantics` | observation 状态确定后 | 仅把结果分类为 `success_found/success_missing/success_empty/error` | 原始结果、摘要字段值 |

这些检查按 v1 已发生的生命周期追加；尚未到达的接点标记为 `skipped`，不能为了完成 shadow 报告而执行额外工作。

## 路由推导边界

Registry comparator 接收结构化 `IntentResolution` 中已经存在的字段，而不是重新理解用户消息：

```text
primary_intent
expanded_intents
clarification_required
risk_level
```

按顺序查询 Registry 的 `supported_intents`，稳定去重并截断到 4 个工具。`clarification_required=true` 或 `risk_level=high` 时预期空白名单。若 Registry 多出任何工具，差异码必须为 `permission_expansion`，即使该工具同时存在于 v1 的全局只读集合中也不能忽略。

## Canonical Fingerprint

Shadow 只对允许字段构造排序 JSON，再计算 SHA-256：

- 路由：有序工具 ID；
- 工具身份：`tool_id + langchain_name`；
- 参数契约：字段名、类型、默认值、必填、上下界和 `additionalProperties`；
- 并行与条件策略：工具 ID、布尔策略、方向和触发枚举；
- 观察语义：工具 ID、运行状态和分类枚举。

禁止进入指纹输入：用户 ID、消息、Prompt、真实 arguments、原始 observation、回复、健康值、计划内容、训练数值和数据库资源 ID。指纹不能用作业务缓存键。

## Shadow Report 契约

静态模型位于 `backend/app/schemas/agent_tool_registry.py`：

```text
ToolRegistryShadowReport
  registry_version
  mode = shadow
  status = not_sampled | match | mismatch | partial | error
  sample_bucket = 0..9999
  checks[]
  total_latency_ms

ToolRegistryShadowCheck
  check_type
  status = match | mismatch | skipped | error
  mismatch_codes[]
  legacy_fingerprint / registry_fingerprint
  legacy_tool_ids[] / registry_tool_ids[]
  latency_ms
  skip_reason / error_category
```

报告不包含 run ID；如果未来持久化，它只作为所属 `AgentExecutionTrace` 的可选字段存在。工具 ID 是公开服务端标识，可以持久化；参数值和结果值不允许进入模型。

稳定差异码包括：

- `permission_expansion`
- `registered_tool_missing`
- `unexpected_tool`
- `tool_order_mismatch`
- `langchain_name_mismatch`
- `argument_schema_mismatch`
- `default_argument_mismatch`
- `parallel_policy_mismatch`
- `conditional_evidence_mismatch`
- `observation_semantics_mismatch`
- `shadow_internal_error`

## 采样与配置设计

后续实现才允许增加以下配置，默认全部关闭：

```text
AGENT_TOOL_REGISTRY_SHADOW_ENABLED=false
AGENT_TOOL_REGISTRY_SHADOW_SAMPLE_RATE=0.0
AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE=false
AGENT_TOOL_REGISTRY_SHADOW_EMIT_METRICS=false
```

采样使用 `SHA-256(run_id) % 10000` 的稳定桶，与 `sample_rate` 比较。相同 run 在 worker 重试后必须保持相同采样结果；不得使用进程随机数。run ID 只参与内存计算，不进入报告。

## 持久化与协议

`AgentExecutionTrace` 已增加可选的 `tool_registry_shadow` 字段，解析器同时接受 trace `1.0` 和 `1.1`；JSONB 列无需数据库迁移。只有采样且 `PERSIST_TRACE=true` 时写入报告；否则只执行 run-local 检查，不持久化报告。指标发射不依赖 Trace 持久化。

不得把 shadow 检查伪装成现有 `stage_timings`：它不是模型或业务执行阶段。单独字段可以避免改变模型调用和阶段延迟统计语义。

所有持久化仍经过现有 run 所有权检查。旧 worker attempt 无权覆盖新 attempt 的 shadow 报告。

## 指标（可选适配器已接线，默认关闭）

聚合指标只使用低基数标签：

```text
agent_tool_registry_shadow_runs_total{status}
agent_tool_registry_shadow_checks_total{check_type,status}
agent_tool_registry_shadow_mismatches_total{check_type,code}
agent_tool_registry_shadow_errors_total{check_type,error_category}
agent_tool_registry_shadow_latency_ms
```

纯函数 `project_registry_shadow_metrics(report)` 按上述契约把通过校验的 shadow
report 投影为受模型约束的指标样本。运行时通过可替换的同步适配器发射；当前默认适配器
写入结构化 JSON 日志，后续接 Prometheus、StatsD 或 OpenTelemetry 时不改变 projector。
投影和适配器异常都会 fail-open，丢弃本批或剩余指标，不影响 Trace、Agent 回复或重试。

工具 ID 不作为长期监控标签，避免未来工具数量增长造成高基数；具体 ID 只保存在受所有权保护的采样 trace 中。

## 错误与回滚

- comparator 的每个接点独立捕获异常，转成 `shadow_internal_error` 和安全 `error_category`；
- shadow report 构建失败时丢弃报告，不影响 v1 trace 和最终回复；
- 禁止 shadow 触发重试、Replanner 或 fallback；
- 唯一即时回滚操作是关闭 `AGENT_TOOL_REGISTRY_SHADOW_ENABLED`，不需要迁移或清理业务数据。

## 激活门禁

进入 shadow 实现后依次执行：

1. 本地与 CI：100% 采样，固定工具与多步评测全量运行；
2. 测试/预发：100% 采样并持久化报告；
3. 生产：1% → 5% → 25% → 100%，每档单独观察；
4. 只有 shadow 通过后，才能另开提交讨论 Registry 接管目录或边界校验。

升级到 Registry authority 前必须满足：

- `permission_expansion=0`；
- 路由、工具身份、参数 schema、并行和条件证据匹配率均为 100%；
- 固定评测中的观察语义匹配率为 100%；
- shadow error 为 0；
- shadow 开关前后模型调用、工具调用、终止动作和回复夹具完全一致；
- comparator P95 本地开销不高于 5ms；
- 至少完成 7 天观测并达到预先设定的最小采样数。

## 实现提交顺序

1. `test: define registry shadow comparator cases`：已由 `834a907` 完成；纯函数测试夹具不接运行时。
2. `feat: add registry shadow comparator`：六类纯比较器已实现并通过固定夹具；仍未接运行时。
3. `feat: record optional registry shadow trace`：已实现配置、稳定采样、六接点旁路和可选 Trace，默认关闭。
4. `test: verify registry shadow behavioral parity`：已完成 direct 配置矩阵、planned 并行读取、条件替代、工具失败替代和 shadow 内部错误的运行级对照；固定夹具验证回复、卡片、模型/工具调用、参数、审计、终止动作和预算不变。
5. 通过 CI 与真实小流量观测后，再决定是否进入 Registry catalog authority。

当前已完成 shadow 数据模型、设计、固定 comparator 夹具、六类纯比较器、稳定采样、可选 Trace 接线、运行级行为一致性夹具、纯指标投影器和 fail-open 指标适配器；生产开关仍关闭。真实小流量的配置矩阵、日志汇总、硬门禁和回滚步骤见[观测手册](18-tool-registry-shadow-observation.md)。
