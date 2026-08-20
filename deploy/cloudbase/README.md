# 云托管部署包

Release ZIP 是可重复生成的部署产物，不提交到 Git。构建当前版本：

```powershell
.\scripts\package_cloudbase_backend.ps1 -Version 0.4.0
```

输出文件为 `deploy/cloudbase/fitness-agent-backend-0.4.0.zip`。

`0.4.0` 增加完整对话理解层：查询重写、指代审计、意图扩展、语义任务拆解、持久化澄清状态和填槽恢复。工具仍由意图生成的最多 4 个白名单候选限制，具体调用顺序由 Agent 决定。

该版本包含 Alembic `0015`。首次部署必须设 `RUN_DB_MIGRATIONS_ON_STARTUP=true`；确认 `/ready` 正常且数据库版本为 `0015` 后，后续常态版本可设为 `false`。打包脚本会拒绝包含 `.env`、测试目录、缓存或 Python 字节码的产物。

生产密钥只配置在腾讯云托管环境变量中。不要把 `.env.production`、AppSecret、数据库连接密码或模型 API Key 放进部署包或 Git 历史。
