# 云托管部署包

Release ZIP 是可重复生成的部署产物，不提交到 Git。构建当前版本：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.5.8
```

输出文件为 `deploy/cloudbase/fitness-agent-backend-0.5.8.zip`。

`0.5.8` 在已通过内部门禁的 Registry read enforcement 基线上加入训练计划调整 Proposal 闭环：Controller 可选创建、所有权隔离读取、确认/拒绝 API、幂等决策、并发保护和原子计划版本切换。`AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED` 默认关闭；关闭时不创建 Proposal，历史只读与 Registry 行为保持不变。

该版本包含 Alembic `0016` 至 `0020`。从 `0.5.7` 升级时必须先设 `RUN_DB_MIGRATIONS_ON_STARTUP=true`；确认 `/ready` 正常且数据库版本为 `0020` 后，后续常态版本可设为 `false`。打包脚本会拒绝包含 `.env`、测试目录、缓存或 Python 字节码的产物，并校验迁移、Proposal schema/service、Planner 与 Trace 核心文件齐全。

内部联调首次部署建议保持 Proposal flag 关闭，完成迁移与健康检查后再单独更新为：

```dotenv
AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED=true
```

该开关只允许 Controller 创建 Proposal 和认证用户通过专用 API 决策，不会向 Planner/Executor 暴露计划写工具。

生产密钥只配置在腾讯云托管环境变量中。不要把 `.env.production`、AppSecret、数据库连接密码或模型 API Key 放进部署包或 Git 历史。
