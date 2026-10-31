# AI 私人厨师

拍一张冰箱食材的照片，或者直接打一句「我有鸡蛋、西红柿和半块豆腐」，AI 会识别食材、联网检索真实菜谱，再按营养价值和制作难度打分排序，流式输出一份可以直接照着做的方案。

这是一个 LangChain 课程项目（`lc-cource`），已从 notebook 原型产品化为可部署的服务。

## 功能

- **食材识别**：支持图片（浏览器直传对象存储）和文字两种输入
- **菜谱检索**：通过 Tavily 联网搜索真实存在的做法，避免模型凭空编造
- **多维打分**：按营养价值、制作难度两个维度评分排序
- **流式输出**：边生成边返回，首字延迟低
- **八大菜系**：川、鲁、粤、苏、闽、浙、湘、徽，可随时切换
- **账号体系**：注册/登录，会话按用户隔离，历史记录互不可见
- **会话记忆**：SQLite checkpointer 保存多轮上下文

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + Uvicorn |
| Agent | LangChain 1.x / LangGraph |
| 模型 | 通义千问 `qwen3.8-omni-flash`（DashScope OpenAI 兼容接口） |
| 工具 | Tavily Search |
| 存储 | SQLite（LangGraph checkpointer + 账号库） |
| 前端 | 预构建的 Next.js 静态导出（`app/static`） |
| 依赖 | Python ≥ 3.13，`uv` 管理 |
| 部署 | 单容器 Docker Compose |

## 目录结构

```
app/
  main.py              # FastAPI 入口，挂载路由与静态前端
  agents/              # Agent 构建、流式对话、历史记录
  api/v1/              # chat / auth / oss 路由
  auth/                # 账号存储（scrypt 哈希）与 JWT
  common/              # 日志、菜系列表
  models/              # Pydantic 请求模型
  static/              # 预构建前端（无源码，请勿改动）
db/                    # SQLite 数据文件（不提交）
deploy/                # 可选 Nginx 反代配置
notebooks/             # 课程练习，与应用无关
Dockerfile
docker-compose.yml
DEPLOY.md              # 完整部署手册
```

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

`requirements.txt` 是历史遗留的 `pip freeze`，请勿使用。

### 2. 配置环境变量

```bash
cp .env.example .env
```

填入 `AUTH_SECRET`、`DASHSCOPE_API_KEY`、`TAVILY_API_KEY`；需要图片上传再填 `OSS_*`。
`AUTH_SECRET` 缺失时应用会在导入阶段直接报错，这是有意为之。

### 3. 启动

进程的工作目录必须是 `app/`（checkpointer 用相对路径打开 `../db/personal_chief.db`）。

```bash
cd app
PYTHONPATH=".." ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Windows 下解释器换成 `../.venv/Scripts/python.exe`。

浏览器打开 http://127.0.0.1:8001 即可使用。

### 4. 冒烟检查

```bash
curl http://127.0.0.1:8001/health
curl -X POST http://127.0.0.1:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo1234"}'
```

## Docker 部署

```bash
cp .env.example .env      # 填好密钥
mkdir -p db
docker compose up -d --build
docker compose logs -f --tail=100
```

默认监听 `0.0.0.0:8001`，`./db` 挂载到容器内 `/app/db`，重建镜像不丢会话。
阿里云 ECS 部署、安全组、OSS CORS、Nginx/HTTPS、备份等详细步骤见 [DEPLOY.md](DEPLOY.md)。

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `AUTH_SECRET` | ✅ | JWT 签名密钥，更换后所有已签发令牌失效 |
| `AUTH_TOKEN_TTL_HOURS` | | 令牌有效期，默认 168 |
| `DASHSCOPE_API_KEY` | ✅ | 通义千问 API Key |
| `DASHSCOPE_BASE_URL` | ✅ | DashScope 兼容接口地址 |
| `TAVILY_API_KEY` | ✅ | 联网搜索 |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_BUCKET` | 图片上传需要 | 阿里云 OSS |
| `OSS_ENDPOINT` | | 默认 `oss-cn-beijing.aliyuncs.com` |
| `LANGSMITH_*` | | 可选，追踪调试 |

## 已知限制

- **并发低**：`search_recipes` 是 `async def` 但内部调用同步的 `agent.stream`，一个请求会占住事件循环；SQLite 也是单连接。适合演示，不适合多人同时使用。
- **注册开放**：能访问到服务的人都可以自助注册，公开部署请留意。
- **单实例**：SQLite 方案无法水平扩容。
- **前端不可重建**：`app/static` 是编译产物，仓库里没有源码；登录界面和菜系选择是用 `auth-gate.js` 在运行时注入的。

## 免责声明

本项目仅用于学习与演示。模型输出、菜谱来源均需自行判断，不构成任何饮食或医疗建议。
