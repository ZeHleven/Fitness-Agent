# 本地运行循练 RepLoop

本指南用于运行自己的开发环境。想体验作者提供的小程序，请先按 [README 中的说明](../README.md#申请体验)联系作者加入体验成员。

## 准备环境

- Git。
- Docker 与 Docker Compose，用于运行 PostgreSQL、Redis 和 FastAPI。
- Node.js，以及与 `miniapp/package.json` 中 `packageManager` 一致的 pnpm 版本。
- 微信开发者工具。
- 使用 AI 功能时，需要自己的 DeepSeek API Key；使用微信登录时，需要自己的小程序 AppID 与 AppSecret。

以下命令使用 PowerShell，适用于新克隆的仓库。如果已经有本地配置，请编辑原文件，不要用模板覆盖。

## 1. 获取源码并配置后端

```powershell
git clone https://github.com/ZeHleven/RepLoop.git
cd RepLoop
Copy-Item .env.example .env
```

编辑根目录 `.env`：

| 配置 | 说明 |
| --- | --- |
| `DATABASE_URL`、`REDIS_URL` | 本地 Docker 开发可使用模板中的容器地址 |
| `SECRET_KEY` | 替换为自己生成的随机密钥 |
| `DEEPSEEK_API_KEY` | 启用 AI 对话与相关 AI 接口时填写 |
| `WECHAT_APP_ID`、`WECHAT_APP_SECRET` | 使用微信登录时填写；AppSecret 只放在后端 |
| `RAG_ENABLED` | 初次运行保留 `false` |

未配置模型密钥时，可以先调试普通业务接口；需要模型的 AI 接口会提示服务未配置。邮箱注册和登录可用于本地开发，不依赖微信身份接入。

模板中的各领域 Proposal 功能开关默认关闭。如果需要体验 Agent 发起修改的流程，请阅读[部署包说明中的开关说明](../deploy/cloudbase/README.md)，按需逐项启用并验证。启用后仍需用户确认才能执行。

## 2. 启动后端

```powershell
docker compose up -d --build
docker compose ps -a
```

Compose 会先等待数据库与 Redis 就绪，再由 `bootstrap` 完成迁移、导入动作和食物数据，最后启动 API。首次构建需要下载镜像与依赖。

- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 存活检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- 就绪检查：[http://127.0.0.1:8000/ready](http://127.0.0.1:8000/ready)
- 本地 Caddy 代理：[http://127.0.0.1:8080](http://127.0.0.1:8080)

如果 API 尚未启动，先查看初始化与服务日志：

```powershell
docker compose logs bootstrap fastapi
```

## 3. 启动微信小程序开发构建

在新的终端中进入仓库的 `miniapp` 目录：

```powershell
cd miniapp
Copy-Item project.config.example.json project.config.json
```

创建 `miniapp/.env.development.local`，填入本地 API 地址：

```dotenv
TARO_APP_API_BASE_URL="http://127.0.0.1:8000/api/v1"
```

```powershell
pnpm install --frozen-lockfile
pnpm dev:weapp
```

在微信开发者工具中导入 `miniapp` 文件夹，编译产物目录由 `project.config.json` 指向 `dist/`。访问本机 HTTP 接口时，仅在本地开发设置中关闭合法域名与 HTTPS 校验。

模板中的 `touristappid` 用于本地预览。需要微信登录或真机调试时，按[小程序说明](../miniapp/README.md)配置自己的 AppID 与网络入口；手机上的 `127.0.0.1` 指向手机本身，不能直接访问电脑后端。

## 4. 修改与验证

小程序的主要入口：

| 需求 | 代码位置 |
| --- | --- |
| 训练过程、逐组记录、组间休息 | `miniapp/src/pages/workout-active/` |
| 个性化计划预览与修改 | `miniapp/src/pages/plan-builder/`、`plan-editor/` |
| 历史记录与进度 | `miniapp/src/pages/history/` |
| 饮食记录与汇总 | `miniapp/src/pages/nutrition/` |
| Agent 对话界面 | `miniapp/src/pages/agent/` |
| 个人资料与体重 | `miniapp/src/pages/me/`、`weight/` |

修改小程序后，在 `miniapp` 目录运行：

```powershell
pnpm typecheck
pnpm test:build-contract
pnpm build:weapp
```

同时在微信开发者工具中走一遍受影响的流程。涉及后端行为时，按改动范围运行 `backend/tests/` 中的相关测试；涉及 Agent 的设计与契约，请同时阅读 [Agent 文档](agent/README.md)。

## 部署自己的版本

自行部署需要使用自己的微信小程序身份、数据库与模型服务配置，并自行承担相应服务费用和维护工作。后端部署产物的生成方式见[云托管部署包说明](../deploy/cloudbase/README.md)。

`.env`、AppSecret、模型 API Key、数据库密码和真实用户数据不应提交到 Git。公开分享自己的版本时，请使用配置模板和示例数据。

