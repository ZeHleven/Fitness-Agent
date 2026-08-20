# 阶段 0 与阶段 1 实施状态

更新：2026-08-19。

## 阶段 0：基线冻结

- 已将总体架构、状态与记忆、意图路由、上下文与安全、工具契约、模型运行时、数据库 API 和评测体系固化在本目录。
- 已确认当前工作树包含用户此前的大量未提交改动；本轮未重置或覆盖这些改动。
- 微信小程序 `pnpm.cmd typecheck` 通过。
- 微信小程序 `pnpm.cmd build:weapp` 通过，Taro 4.2.1 / Webpack 编译成功。
- 已启动本地 Docker Desktop 和项目 PostgreSQL 容器，并确认隔离的 `fitness_test` 测试库存在；未连接或清理 Neon 生产库。
- 后端完整 `pytest`：137 项全部通过；97 条警告均来自 `python-jose` 内部使用即将弃用的 `datetime.utcnow()`。

## 阶段 1：共享领域服务

- 新增 `backend/app/services/workout_queries.py`，集中训练计划详情、训练详情、历史、进度、训练指标和个人纪录读取逻辑。
- `workouts` 路由改为调用共享服务，HTTP 路径和响应模型保持不变。
- 自动调整拆为：
  - `build_adaptive_adjustment_proposals`：只查询并计算提案，不修改计划实体。
  - `apply_adjustment_proposals`：显式应用提案，不自行提交事务。
  - `apply_adaptive_adjustments`：兼容现有训练完成闭环的组合入口。
- 增加训练查询纯函数和调整提案序列化测试。
- `backend/app` 与 `backend/tests` 已通过 Python 语法编译检查。
- FastAPI 应用可成功导入并注册 34 条路径；阶段 1 纯领域函数 smoke 检查通过。
- 阶段 1 定向测试：14 项全部通过，其中包含“提案生成不修改计划实体”的边界测试。

## 完整回归命令

Docker Desktop 恢复后，在项目根目录运行：

```powershell
docker compose up -d postgres redis
docker compose run --rm fastapi pytest -q
cd miniapp
pnpm.cmd typecheck
pnpm.cmd build:weapp
```

阶段 0 与阶段 1 的验证门禁已经满足。下一阶段可进入 Agent 数据库迁移和模型运行时骨架，但仍应保持写工具默认隐藏。
