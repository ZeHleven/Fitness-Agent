# Tool Registry Shadow：真实小流量观测手册

状态：观测工具与门禁已准备，生产开关仍关闭，2026-08-22。

## 目标与边界

本阶段只验证 Registry v2 shadow 是否稳定复现 v1 的工具边界和观察语义。它不授权
Registry 接管目录，不改变模型 Prompt、工具白名单或执行结果，也不直接开启生产流量。

结构化指标是 best-effort 观测数据，不是计费或业务事实来源。指标不含 run ID、用户 ID、
工具 ID、指纹、消息或模型内容；需要定位单个差异时使用同一采样窗口内的可选 shadow Trace。

## 配置矩阵

| 阶段 | `ENABLED` | `SAMPLE_RATE` | `PERSIST_TRACE` | `EMIT_METRICS` |
| --- | ---: | ---: | ---: | ---: |
| 默认/完全关闭 | `false` | `0.0` | `false` | `false` |
| 测试或预发受控流量 | `true` | `1.0` | `true` | `true` |
| 生产第一档 | `true` | `0.01` | `true` | `true` |
| 只回滚指标适配器 | `true` | 保持原值 | 保持原值 | `false` |
| 完全回滚 shadow | `false` | `0.0` | `false` | `false` |

完整环境变量名：

```text
AGENT_TOOL_REGISTRY_SHADOW_ENABLED
AGENT_TOOL_REGISTRY_SHADOW_SAMPLE_RATE
AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE
AGENT_TOOL_REGISTRY_SHADOW_EMIT_METRICS
```

环境变量在新容器或新 revision 启动时读取。不得只修改变量却继续观察旧实例。

## 受控预发窗口

先使用内部账号完成至少 30 个真实模型 Run，建议六类场景各 5 次：

1. 简单下一练 direct；
2. 三证据低完成率调整建议；
3. 三证据高完成率反事实；
4. 进度失败后读取历史替代证据；
5. 健康限制与下一练冲突；
6. 健康红旗安全停止或信息不足澄清。

受控窗口使用 100% shadow 采样。direct、澄清和安全停止没有到达全部生命周期接点，报告为
`partial` 且部分 check 为 `skipped` 是合法结果；门禁只要求所有已执行检查均无 mismatch/error，
并要求整个窗口覆盖六种 check。

## 日志导出与汇总

默认适配器向 Uvicorn 生产控制台写入：

```text
agent_tool_registry_shadow_metric {json}
```

导出观测窗口内的完整后端容器日志，不能只导出成功指标行；以下两个 fail-open 警告也必须
包含在输入中：

```text
Tool Registry shadow metric projection dropped
Tool Registry shadow metric adapter dropped remaining samples
```

CloudBase 可以在云托管日志界面按部署 revision 和时间窗口导出 UTF-8 文本或 JSONL。
汇总器同时接受普通日志行，以及 `message`、`msg`、`text` 或 `log` 字段中的日志消息：

```powershell
python backend/scripts/summarize_registry_shadow_metrics.py `
  .\tmp\registry-shadow-preprod.log `
  --min-sampled-runs 30 `
  --max-p95-latency-ms 5 `
  --strict
```

生产 1% 窗口将最小样本改为 100：

```powershell
python backend/scripts/summarize_registry_shadow_metrics.py `
  .\tmp\registry-shadow-prod-1pct.log `
  --min-sampled-runs 100 `
  --max-p95-latency-ms 5 `
  --strict
```

混合流量不要设置 `--min-run-match-rate`。只有明确全部进入完整 planned 生命周期的受控批次，
才可额外使用 `--min-run-match-rate 1.0`。

## 硬门禁

汇总器的严格模式要求：

- `permission_expansion=0`；
- mismatch check 和 mismatch code 均为 0；
- error check 和安全 error category 均为 0；
- projector drop、adapter drop、无效指标事件均为 0；
- 每个 sampled run 对应 1 个 latency 和 6 个 check 事件；
- 窗口覆盖六种 check；
- comparator 总延迟 P95 不高于 5ms；
- 样本数达到当前窗口预先设定的下限。

`partial` 本身不是失败。任何硬门禁失败都停止放量，先检查采样 Trace；不得通过放宽差异、
错误或权限门禁来让观测“通过”。

## 业务基线对照

Shadow 指标只回答 Registry 是否与 v1 一致，不能代替 Agent 业务健康度。部署前后使用相同长度
窗口，对比 `agent_runs` 的完成率和延迟：

```sql
SELECT
  status,
  count(*) AS run_count,
  percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
FROM agent_runs
WHERE started_at >= :window_start
  AND started_at < :window_end
GROUP BY status
ORDER BY status;
```

同时抽查模型调用预算、工具调用数、终止动作、回复和卡片没有因 shadow 开关改变。出现 Agent
失败率上升、明显尾延迟回归或用户行为差异时，即使 Registry 指标全绿也必须回滚。

## 生产第一档与退出条件

预发硬门禁通过后才进入生产 1%。第一档至少同时满足：

- 连续观察 7 天；
- 至少 100 个 sampled reports；流量不足时延长窗口，不为凑样本直接扩大比例；
- 每日和累计严格门禁均通过；
- 业务完成率、P95 和工具/模型预算无可归因回归；
- 至少抽查 direct、planned、fallback、clarify/safe-stop 四类 Trace。

适配器或日志链路单独异常时先关闭 `EMIT_METRICS`；comparator、权限或行为出现异常时关闭
`SHADOW_ENABLED`。只有 1% 窗口满足退出条件后，才能另开变更讨论 5% 档位；Registry
catalog authority 仍需要独立决策与回滚方案。

内部 100% shadow 与故障注入结果已经形成独立的只读研发切换决策，见
[Registry 只读 Enforce 切换契约](19-tool-registry-read-enforce-transition.md)。该决策不跳过本节的
生产第一档要求。
