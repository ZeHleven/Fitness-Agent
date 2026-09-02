# 云托管部署包

Release ZIP 是可重复生成的部署产物，不提交到 Git。构建当前版本：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.5.28
```

输出文件为 `deploy/cloudbase/fitness-agent-backend-0.5.28.zip`。

`0.5.28` 修复全天饮食方案的结构化生成契约：优先使用严格函数调用，并在供应商能力不兼容时回退到包含明确 Schema 与示例的 JSON Mode。两次生成共享统一的服务端校验与精确修复反馈，失败时保留脱敏诊断且继续保证零 Artifact、零 Proposal、零饮食写入。

本热修不新增数据库迁移，数据库版本仍为 Alembic `0024`；从 `0.5.27` 升级不需要重新开启启动迁移。若从更早版本直接升级，仍需先将数据库升级到 `0024`，确认 `/ready` 正常后关闭启动迁移。打包脚本会拒绝包含 `.env`、测试目录、缓存或 Python 字节码的产物，并校验迁移、Proposal schema/service、Planner 与 Trace 核心文件齐全。

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
