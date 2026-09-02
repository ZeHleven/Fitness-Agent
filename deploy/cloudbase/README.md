# 云托管部署包

Release ZIP 是可重复生成的部署产物，不提交到 Git。构建当前版本：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.5.29
```

输出文件为 `deploy/cloudbase/fitness-agent-backend-0.5.29.zip`。

`0.5.29` 将全天饮食的精确营养配平从模型移交给服务端 HiGHS 混合整数优化器：模型选择餐次和食品，服务端以整数克数优先求理想区间，必要时在明确展示的均衡偏差范围内返回可审阅方案。食品真实性、饮食限制、医学边界和确认前零写入仍是硬约束。

本版本不新增数据库迁移，数据库版本仍为 Alembic `0024`；从 `0.5.27` 或 `0.5.28` 升级不需要重新开启启动迁移。若从更早版本直接升级，仍需先将数据库升级到 `0024`，确认 `/ready` 正常后关闭启动迁移。打包脚本会拒绝包含 `.env`、测试目录、缓存或 Python 字节码的产物，并校验迁移、Proposal schema/service、Planner、Trace 与营养优化器核心文件齐全。

正式打包前还必须在 GitHub `main` 分支手动运行 `Daily Meal Live Model Release Gate`。该工作流使用受保护的 `agent-live-eval` Environment 和其中的 `DEEPSEEK_API_KEY`，要求原句连续 10 次全部成功、20 条同义表达成功率至少 95%，并验证未确认写入数为零。评测报告只包含脱敏状态和统计，不记录个人资料、食品候选或模型原文。

内部联调首次部署建议保持新增 Proposal 开关关闭，完成迁移与健康检查后再按需逐项开启：

```dotenv
AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED=true
MANUAL_PLAN_PROPOSALS_ENABLED=true
AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED=true
AGENT_PROFILE_PROPOSALS_ENABLED=true
AGENT_WEIGHT_PROPOSALS_ENABLED=true
AGENT_NUTRITION_PROPOSALS_ENABLED=true
```

关闭这些开关不会影响读取和意图记录，也不会阻止用户处理已经存在的 Proposal。建议先开启手动计划 Proposal 并完成回归，再按领域灰度开启 Agent 写入；Agent 不会获得直接写数据库的工具。

生产密钥只配置在腾讯云托管环境变量中。不要把 `.env.production`、AppSecret、数据库连接密码或模型 API Key 放进部署包或 Git 历史。
