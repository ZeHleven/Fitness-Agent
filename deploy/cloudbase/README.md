# 云托管部署包

Release ZIP 是可重复生成的部署产物，不提交到 Git。构建当前版本：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.5.2
```

输出文件为 `deploy/cloudbase/fitness-agent-backend-0.5.2.zip`。

`0.5.2` 在现有多步运行时上加入非权威的 Tool Registry v2 shadow：六类本地 comparator、稳定采样、可选 Trace、纯指标投影器、fail-open 结构化日志适配器和观测汇总门禁。所有 shadow 开关默认关闭，Registry 不参与白名单、Planner 或工具执行决策，写工具仍未开放。

该版本包含 Alembic `0016` 至 `0019`。从旧版本升级时必须设 `RUN_DB_MIGRATIONS_ON_STARTUP=true`；确认 `/ready` 正常且数据库版本为 `0019` 后，后续常态版本可设为 `false`。打包脚本会拒绝包含 `.env`、测试目录、缓存或 Python 字节码的产物，并校验迁移与 Planner/Trace 核心文件齐全。

生产密钥只配置在腾讯云托管环境变量中。不要把 `.env.production`、AppSecret、数据库连接密码或模型 API Key 放进部署包或 Git 历史。
