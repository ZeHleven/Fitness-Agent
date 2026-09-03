# 云托管部署包

Release ZIP 是可重复生成的部署产物，不提交到 Git。构建当前版本：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.5.30
```

输出文件为 `deploy/cloudbase/fitness-agent-backend-0.5.30.zip`。

`0.5.30` 将 Agent 路由收敛为受约束的 `SemanticRouteV2`：模型只输出领域、动作和证据需求，兼容用的历史意图字段由服务端投影且不能授权工具。普通请求不再由规则抢跑，模型失败时安全停止；Proposal 决策和健康红旗仍保留确定性优先级。全天饮食优化器会复验并采用超时前已找到的整数可行解，不再把“未证明最优”等同于服务故障。

本版本新增 Alembic `0025`，仅把新 Agent Run 的理解版本默认值更新为 `v6`，不重写历史记录。首次部署 `0.5.30` 时临时启用启动迁移，确认数据库为 `0025` 且 `/ready` 正常后关闭。打包脚本会拒绝包含 `.env`、测试目录、缓存或 Python 字节码的产物，并校验迁移、Proposal schema/service、Planner、Trace 与营养优化器核心文件齐全。

正式打包前还必须在 GitHub `main` 分支手动运行 `Daily Meal Live Model Release Gate`。该工作流先验证全领域真实模型语义路由，再对同一个完整 candidate SHA 顺序执行两轮全天饮食端到端评测；每轮都要求原句 10/10、20 条同义表达至少 19/20、优化器不可用次数为零，并验证未确认写入和意外 Proposal 均为零。评测报告只包含脱敏状态和统计，不记录个人资料、食品候选或模型原文。

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
