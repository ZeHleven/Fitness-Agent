# 云托管部署包

Release ZIP 是可重复生成的部署产物，不提交到 Git。构建当前版本：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.5.24
```

输出文件为 `deploy/cloudbase/fitness-agent-backend-0.5.24.zip`。

`0.5.24` 增加档案、健康、体重和饮食管理入口，支持活动训练计划的完整手动编辑与删除 Proposal，并将 Agent 的受控读写扩展到训练计划、档案、健康、体重和饮食领域。所有 Agent 写入仍先生成 Proposal，确认后才由领域服务执行；健康红旗、所有权隔离、版本与指纹校验、幂等决策和原子计划切换继续生效。

该版本新增 Alembic `0022`，并包含此前的 `0021`。首次部署必须先设 `RUN_DB_MIGRATIONS_ON_STARTUP=true`；确认 `/ready` 正常且数据库版本为 `0022` 后，再将其恢复为 `false`。打包脚本会拒绝包含 `.env`、测试目录、缓存或 Python 字节码的产物，并校验迁移、Proposal schema/service、Planner 与 Trace 核心文件齐全。

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
