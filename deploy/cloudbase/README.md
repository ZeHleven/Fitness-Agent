# 云托管部署包

Release ZIP 是可重复生成的部署产物，不提交到 Git。构建当前版本：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.5.21
```

输出文件为 `deploy/cloudbase/fitness-agent-backend-0.5.21.zip`。

`0.5.21` 将 Agent 理解层升级为 v3：业务领域与读写动作分开识别，结构化记录 `change_requests`，并由服务端能力编译器把受支持的训练计划修改转换为待确认 Proposal。首期支持周期、确定性的 4→3 天频率调整，以及动作组数、次数、休息和重量目标；新增、删除、替换和其他领域写入会被识别但不会执行。自然语言确认或拒绝继续复用现有所有权隔离、版本校验、幂等决策和原子计划切换。

该版本新增 Alembic `0021`。从 `0.5.20` 升级时，首次部署必须先设 `RUN_DB_MIGRATIONS_ON_STARTUP=true`；确认 `/ready` 正常且数据库版本为 `0021` 后，后续常态版本可设为 `false`。打包脚本会拒绝包含 `.env`、测试目录、缓存或 Python 字节码的产物，并校验迁移、Proposal schema/service、Planner 与 Trace 核心文件齐全。

内部联调首次部署建议保持 Proposal flag 关闭，完成迁移与健康检查后再单独更新为：

```dotenv
AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED=true
```

该开关关闭时仍会执行并记录 v3 意图识别，但不会创建 Proposal；开启后只允许受支持的训练计划修改进入 Proposal 流程，不会向 Planner/Executor 暴露计划写工具。

生产密钥只配置在腾讯云托管环境变量中。不要把 `.env.production`、AppSecret、数据库连接密码或模型 API Key 放进部署包或 Git 历史。
