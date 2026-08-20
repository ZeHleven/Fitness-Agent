# 阶段 2：Agent 运行时与首批只读工具

完成日期：2026-08-19。

## 已实现

### 数据库

Alembic `0011` 新增：

- `agent_conversations`：Agent 会话与摘要状态。
- `agent_messages`：用户和助手消息及卡片元数据。
- `agent_runs`：意图、子任务、工具白名单、模型和运行状态。
- `agent_tool_calls`：脱敏参数、工具结果和错误审计。
- `agent_proposals`：为后续只提案、确认后执行预留的版本化记录。
- `agent_memories`：为后续经确认的长期记忆预留。

旧 `chat_sessions` / `chat_messages` 和 `/api/v1/chat` 保留，阶段 2 不做破坏性替换。

### Agent API

- `POST /api/v1/agent/chat`
- `GET /api/v1/agent/conversations`
- `GET /api/v1/agent/conversations/{conversation_id}/messages`
- `GET /api/v1/agent/runs/{run_id}`

所有查询以 JWT 中的当前用户为边界；客户端不能传入或覆盖工具使用的 `user_id`。

### LangChain 运行时

- 固定使用 `langchain==1.3.15`、`langchain-openai==1.5.2` 和 `openai==3.3.0`。
- 使用 `create_agent` 和 `ChatOpenAI` 的自定义 `base_url` 接入 DeepSeek OpenAI 兼容接口。
- 明确关闭 Responses API，继续使用兼容的 Chat Completions 工具调用。
- 每轮先执行确定性的 `IntentResolver`，再由 `ToolRouter` 生成白名单；只把白名单中的工具实例传给 Agent。
- 会话历史从 Agent 表加载，模型运行、消息、工具调用和失败状态均可审计。
- 未配置密钥时返回安全的 503，并将运行标为失败，不伪造回答。

### 首批只读工具

| 工具 ID | LangChain 名称 | 数据范围 |
| --- | --- | --- |
| `profile.get_summary` | `profile_get_summary` | 基础资料与训练偏好，不含健康字段 |
| `health.get_screening_summary` | `health_get_screening_summary` | 伤病、慢性病和筛查状态 |
| `plan.get_active` | `plan_get_active` | 完整活动训练计划 |
| `workout.get_next` | `workout_get_next` | 下一计划训练日和动作 |
| `workout.get_active_session` | `workout_get_active_session` | 进行中的训练和已记录组 |
| `workout.list_history` | `workout_list_history` | 近期具体训练记录 |
| `workout.get_progress` | `workout_get_progress` | 1 至 52 周聚合进度 |

所有工具均为只读，参数使用 Pydantic 严格校验，描述包含适用场景、排除场景和示例。未知工具和任何写工具在注册器中默认拒绝。

## 验证结果

- 阶段 2 定向测试：13 项通过。
- 后端全量测试：150 项通过。
- 工具调用审计只保留字段名、数量和聚合值，不复制伤病、慢性病或个人资料原值。
- Alembic 从空数据库完整升级至 `0011 (head)`，六张 Agent 表核对通过；临时迁移数据库已删除。
- 本地开发数据库已升级至 `0011`。
- Python 3.13 FastAPI Docker 镜像构建成功；容器启动、种子检查和 `/health` 均正常。
- OpenAPI 已注册 4 条 Agent 路径。

本阶段未调用真实付费模型。下一阶段需使用有效 DeepSeek 密钥完成结构化意图、工具调用和回答质量的真实环境验收。
