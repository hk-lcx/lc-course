# AI 私人厨师项目 · 面试准备手册

> 基于 2026-10-30 当前代码整理，覆盖：面试问答、模块实现与技术、真实踩坑与解决、简历项目简介。
> 项目路径：`D:\PyCharm 2026.1\lc-cource`
> 线上形态：阿里云 ECS（广州）单机 Docker 部署，登录后使用。

---

## 0. 30 秒项目速览

**一句话：** 一个多模态「AI 私人厨师」Web 应用——用户拍照或输入食材，选择八大菜系之一，Agent 联网检索菜谱、按营养与难度排序，并以流式方式返回结构化推荐；带账号登录、会话记忆和图片直传。

**技术栈：**

| 层次 | 技术 |
|---|---|
| 后端 | Python 3.13、FastAPI、Uvicorn、Pydantic |
| Agent | LangChain 1.x、LangGraph、`create_agent`、Tavily Search |
| 模型 | 阿里云百炼 `qwen3.8-omni-flash`（DashScope OpenAI 兼容接口，多模态） |
| 记忆 | LangGraph `SqliteSaver`（SQLite checkpoint） |
| 鉴权 | 自研 scrypt 密码哈希 + PyJWT（HS256） |
| 存储 | SQLite（`personal_chief.db` 会话、`auth.db` 账号）、阿里云 OSS |
| 前端 | 预构建 Next.js 静态导出 + 手工注入的 `login.html` / `auth-gate.js` |
| 部署 | Docker 26 + Docker Compose、uv、CentOS 7、阿里云 ECS |
| 观测 | stdout 日志、Docker `HEALTHCHECK`、可选 LangSmith |

**关键数字：** 8 大菜系、Tavily 每次检索 5 条、JWT 默认 168 小时、scrypt N=2¹⁴、预签名有效期 1 小时、移动端断点 900px、服务端口 8001、服务器 1 核 1G + 2G swap。

---

## 1. 面试问题与答案

### 1.1 项目与架构

**Q1：这个项目解决了什么问题？**

- 用户面对“冰箱里有什么、今天吃什么”的决策问题。
- 传统菜谱网站要求用户主动搜索、筛选；本项目让用户**拍照/口述食材**，由 Agent 识别食材、联网找菜谱、评估营养与难度并排序，直接给出可执行的推荐。
- 加入账号体系和菜系选择后，推荐结果更个性化、可持续多轮对话。

**Q2：为什么用 Agent，而不是普通 Chain 或固定流程？**

- 工具调用的**时机和参数**必须由模型根据输入动态决定（搜索关键词、是否需要搜索、是否结合图片）。
- 用户输入形态差异大（图片 / 文字 / 口语化清单），固定流程难以覆盖。
- `create_agent` 本质是 `StateGraph` 的 ReAct 循环：`模型 → 工具 → 模型`，直到模型不再产生 tool call，天然支持多轮工具调用。

**Q3：为什么选 LangGraph，而不是自己写循环？**

- 现成的**持久化**（checkpointer）、**流式**（`stream_mode="messages"`）、**可视化调试**（LangGraph Studio / `langgraph.json`）。
- 后续可扩展人工审批、子图、多 Agent 协作，而不需要重写调度层。
- 代价是抽象较重、调试需要理解其状态模型。

**Q4：完整请求链路是什么？**

```
浏览器
  → login.html（账号密码）
  → /api/v1/auth/login  → 签发 JWT，存 localStorage
  → 进入应用（auth-gate.js 校验 token）
  → /api/v1/oss/presign?filename=...  → 返回 OSS 预签名 PUT URL
  → 浏览器直接 PUT 图片到 OSS
  → /api/v1/chat/stream（Bearer token + message + image_url + thread_id + cuisine）
  → FastAPI → search_recipes()
  → LangGraph Agent（qwen3.8-omni-flash + Tavily）
  → SqliteSaver 按 thread_id 读写记忆
  → 逐 token 返回（text/event-stream）
  → 前端边收边渲染
```

**Q5：为什么用 FastAPI 同时托管前端？**

- 前端是预构建的 Next.js 静态导出，没有 Node 运行时需求。
- 用 `StaticFiles` 挂到 `/`，前后端同源：没有跨域问题、部署只需要一个容器、一个端口。
- 缺点：静态挂载会抢占所有路径，SPA fallback 需要特别注意（见 Q35）。

### 1.2 Agent 与模型

**Q6：Agent 的工作流程是什么？**

system prompt 规定四步：

1. **识别食材**：有图先识图，整理“当前可用食材清单”，评估新鲜度和数量。
2. **联网检索**：优先调用 `web_search`（Tavily，`max_results=5`），用食材清单做关键词。
3. **评估排序**：从营养价值和制作难度两个维度打分排序。
4. **结构化输出**：食谱信息、得分、推荐理由、参考图片。

并额外约束：用户指定菜系时，只推荐该菜系的菜；搜不到才允许模型自由发挥。

**Q7：`create_agent` 底层是什么？**

- 构建一个 LangGraph `StateGraph`，核心是 `model` 节点和 `tools` 节点组成的循环。
- 模型输出 `tool_calls` 时进入工具节点，执行结果再回填消息列表，直到模型不再请求工具。
- `checkpointer` 在每个超步后保存状态，因此同一 `thread_id` 可以续接上下文。

**Q8：多模态消息怎么组织？**

- 无图：`HumanMessage(content=text)`。
- 有图：`HumanMessage(content=[{"type":"image","url":...}, {"type":"text","text":...}])`。
- 模型侧由 LangChain 转成 OpenAI 兼容的 `image_url` 内容块，DashScope 负责拉取图片 URL 并理解图像。

**Q9：为什么选 `qwen3.8-omni-flash`？**

- 项目核心是“拍食材照片”，必须是**视觉可用**的多模态模型。
- DashScope 提供 OpenAI 兼容接口，`init_chat_model(model_provider="openai", base_url=...)` 即可接入，迁移成本低。
- `flash` 档位在延迟和成本上更适合对话式场景。
- 备选：DeepSeek `deepseek-chat` 成本更低，但不支持图片，会破坏核心流程（项目里保留了这个分支的尝试）。

**Q10：怎么保证“只推荐指定菜系”？**

三层约束：

1. 前端：登录后强制弹出八大菜系选择，存 `localStorage`。
2. 请求：`auth-gate.js` 在 `/chat/stream` 的 body 里补 `cuisine` 字段。
3. 后端：`ChatRequest.cuisine` 校验（非法返回 400）；`search_recipes()` 把 `【本次菜系】xx，请只推荐 xx 的菜谱` 拼进用户消息；system prompt 再次强调。

> 面试点：这是“产品约束 + 请求约束 + 提示词约束”的组合，而不是只靠 prompt。

**Q11：模型调用失败怎么办？**

- `search_recipes()` 捕获所有异常，记录日志，向流里 yield 一句兜底文案（“信息检索失败，试试看手动输入食物列表？”）。
- 因为 HTTP 200 和响应头在流开始前已经发出，**无法再改状态码**；这是流式接口的固有限制。
- 更规范的做法：流内发送结构化错误帧（`event: error`），前端区分展示；非流式接口则返回 5xx。

**Q12：怎么降低幻觉？**

- 强制优先 `web_search`，搜不到才自由生成。
- 要求输出参考图片/链接，便于用户核对。
- 菜系白名单约束，减少跑题。
- 不足：没有引用校验、没有事实核查、打分由模型生成而非公式；改进方向是结构化输出（JSON Schema）+ 规则校验 + 评测集。

**Q13：“营养和难度打分”具体怎么算？**

- 现状：由 LLM 按 prompt 输出，**不是确定性算法**。
- 面试时要诚实说明：可复现性差、不同次结果可能不同。
- 改进：让模型输出固定字段（如 `nutrition_score`、`difficulty_score`、`reason`），用 Pydantic 校验后用代码排序；或引入食材营养数据库做规则计算。

### 1.3 流式输出

**Q14：流式是怎么实现的？**

- `agent.stream(..., stream_mode="messages")` 逐 token 产出 `AIMessageChunk`。
- 过滤空内容后 `yield chunk.content`。
- FastAPI 用 `StreamingResponse(..., media_type="text/event-stream")` 边生成边发送。
- 响应头加了 `Cache-Control: no-cache, no-transform` 和 `X-Accel-Buffering: no`，防止浏览器/反向代理攒包。

**Q15：为什么不用 `EventSource`？**

- 响应头虽然是 `text/event-stream`，但 body 是**裸文本分片**，没有标准 SSE 的 `data: ...\n\n` 帧。
- `EventSource` 只认标准帧，所以前端只能用 `fetch` + `ReadableStream` 手动解码。
- 若要标准化：给每个 chunk 包 `data: ...\n\n`，处理跨 chunk 截断的中文/换行，或用 `sse-starlette`。

**Q16：为什么用同步的 `agent.stream` 还能逐片输出？**

- `search_recipes` 是 async 生成器，但内部 `agent.stream` 是同步迭代器；同步 I/O 会占住事件循环。
- 代码在每个 chunk 后 `await asyncio.sleep(0)`，主动让出事件循环，让 Starlette 有机会把已产生的分片写回客户端。
- 这解决了“一次性返回”的问题，但**没有解决并发阻塞**：单个请求在两次 chunk 之间仍然占用事件循环。
- 根因：当前 `SqliteSaver` 只有同步接口，直接 `agent.astream` 不可用。
- 彻底方案：换 `AsyncSqliteSaver` 或 Postgres checkpointer，改 `async for`；或把同步生成器交给线程池迭代。

**Q17：流式下怎么处理错误和中途断开？**

- 现状：捕获异常后 yield 兜底文案；客户端断开时生成器停止。
- 风险：错误信息混在正常正文里；没有心跳，长时间无 token 时中间层可能超时。
- 改进：错误帧 + 心跳（`:\n\n` 注释帧）+ 断连检测（`request.is_disconnected()`）。

**Q18：为什么会“憋一大段才返回”？怎么排查？**

- 常见原因：Nginx `proxy_buffering on`、压缩、CDN 缓冲、或服务端同步阻塞。
- 项目处理：`proxy_buffering off; proxy_cache off`；应用层加 `X-Accel-Buffering: no`；每片后 `await asyncio.sleep(0)`。

### 1.4 会话与记忆

**Q19：多轮对话的记忆怎么实现？**

- `thread_id` 是会话主键。
- `SqliteSaver` 在每步执行后把 LangGraph 状态（含 `messages`）写入 `db/personal_chief.db` 的 `checkpoints` / `writes` 表。
- 下次同一个 `thread_id` 自动恢复上下文。
- 清空会话调用 `checkpointer.delete_thread(thread_id)`。

**Q20：多用户会不会串会话？**

- 会的，如果直接用前端传来的 `thread_id`。
- 项目在 API 层统一改写为 `{user_id}:{thread_id}`，再进入 checkpointer；这样即使用户 A 和用户 B 用了相同的 `thread_id`，也互不可见。
- 同时所有 chat / messages / clear 接口都加了 `Depends(get_current_user)`。

**Q21：SQLite 单连接在并发下安全吗？**

- checkpointer 用 `sqlite3.connect(..., check_same_thread=False)`，因为连接在 import 时创建、被多线程请求共用。
- auth 库额外用 `threading.RLock` 串行化访问，并开启 WAL。
- 局限：单连接不是线程安全的银弹；高并发应换连接池/Postgres；多 worker 会有文件锁竞争。

**Q22：`GET /chat/messages` 为什么返回空？**

- 代码里写的是 `isinstance(msg.content, HumanMessage)`，判断的是**内容对象**而不是消息对象。
- `msg.content` 是字符串，永远不是 `HumanMessage`，所以结果恒为空。
- 修复：改成 `isinstance(msg, HumanMessage)` / `isinstance(msg, AIMessage)`。
- 面试价值：能主动指出自己项目的 bug + 根因 + 修法，是加分项。

**Q23：长对话上下文怎么控制？**

- 现状：没有裁剪，checkpoint 会随轮次增长，成本和延迟上升。
- 可选方案：`trim_messages` 保留最近 N 轮/Token、定期摘要旧消息、按菜系/会话拆分线程。

### 1.5 鉴权与账号

**Q24：密码怎么存？**

- 不存明文，用标准库 `hashlib.scrypt`。
- 参数：N=2¹⁴、r=8、p=1、salt 16 字节、dklen 32。
- 存储格式：`scrypt$N$r$p$salt_b64$hash_b64`，把参数一起存，方便以后升级强度。
- 校验用 `hmac.compare_digest`，避免时序攻击。
- 面试对比：bcrypt/argon2 生态更常见；这里用 scrypt 是为了**不引入额外依赖**，同时保留“内存硬”特性。

**Q25：JWT 怎么签发和校验？**

- HS256，密钥来自 `AUTH_SECRET`，缺失时**启动即报错**（fail-fast）。
- claims：`sub`（用户 id）、`username`、`ver`（token 版本）、`iat`、`exp`。
- 默认有效期 168 小时，可用 `AUTH_TOKEN_TTL_HOURS` 覆盖。
- 校验：FastAPI `HTTPBearer(auto_error=False)` + `Depends(get_current_user)`；解析 token 后再查一次用户表，确认用户存在且 `ver` 匹配。

**Q26：退出登录怎么让所有 token 失效？**

- 用户表有 `token_version`。
- `POST /auth/logout` 执行 `token_version = token_version + 1`。
- 之后所有旧 token 的 `ver` 都对不上，校验失败；所有设备一起退出。
- 代价：每次请求都要查一次用户表，JWT 不是完全“无状态”，但换来了可撤销能力。

**Q27：token 存在哪里？有什么风险？**

- 存在 `localStorage`，由 `auth-gate.js` 给所有 `/api/v1/` 请求加 `Authorization: Bearer`。
- 风险：XSS 可以读走 token。
- 更稳的方案：HttpOnly + Secure Cookie + CSRF 防护；或短时效 access token + refresh token。
- 当前没有 CSRF 风险，因为 token 不走 Cookie，浏览器不会自动携带。

**Q28：注册为什么是开放的？怎么限制？**

- 当前任何人都能注册，注册成功直接返回 token。
- 公网部署时这会被陌生人消耗模型/搜索额度。
- 可选限制：邀请码、邮箱验证、注册限流（Nginx `limit_req` / 应用层令牌桶）、关闭注册接口、管理员审核。

**Q29：CORS 配置有问题吗？**

- `allow_origins=["*"]` 同时 `allow_credentials=True` 是**浏览器规范不允许**的组合。
- 本项目前后端同源部署，实际不依赖 CORS；但配置仍然过宽。
- 生产应改成明确来源白名单，并把 `allow_credentials` 设为 `False`（Bearer token 不需要它）。

### 1.6 前端改造（预构建静态站）

**Q30：没有前端源码，怎么加登录页和用户信息？**

- 前端是 Next.js 导出产物，无法重新编译。
- 手工新增 `login.html`（原生 JS 表单）和 `auth-gate.js`（IIFE）。
- `index.html` 的 `<head>` 注入一个 `<script src="/auth-gate.js">`，同步执行，未登录立即跳转，避免页面闪一下。
- `auth-gate.js` 在运行时：
  - 拦截 `window.fetch`，给 `/api/v1/` 请求加 `Authorization`；
  - 401 时清本地凭证并回登录页；
  - 克隆原 header 的品牌和新会话按钮，拼出左侧栏；
  - 注入 CSS，把聊天区和底部输入框右移；
  - 900px 以下侧栏变成顶部横条。

**Q31：前端把 API 地址写死成 `localhost:8001` 怎么办？**

- 静态 chunk 里原本是 `http://localhost:8001`，公网部署后手机/其他电脑会请求自己的 localhost，必然失败。
- 处理：把编译产物里的基地址替换为 `window.location.origin`，同源部署后自动适配 localhost、IP、域名。
- 这是“不能重编译”情况下的一种运行时补丁思路。

**Q32：菜系选择怎么传到后端？**

- 登录成功后写 `localStorage.cuisine_pending = "1"`。
- `auth-gate.js` 在 DOM 就绪后弹出菜系模态框，选择写入 `localStorage.cuisine`。
- 因为原前端不会发送 `cuisine`，脚本在拦截 `fetch` 时解析 `/chat/stream` 的 JSON body，补上 `cuisine` 字段。
- 后端仍做白名单校验，前端绕过也不会生效。

**Q33：移动端怎么适配？**

- 页面本身有 `viewport`，但预构建页面没有针对窄屏的断点。
- `auth-gate.js` 注入 CSS：`@media (min-width:900px)` 用左侧栏；`@media (max-width:899px)` 用顶部横条，并动态计算聊天区 `padding-top` 避开横条。
- 局限：原页面布局仍可能偏桌面，属于“尽量可用”而非完整响应式重构。

**Q34：静态站和 SPA fallback 有什么坑？**

- `StaticFiles(html=True)` 挂载在 `/`，会匹配所有路径；在它之后声明的 `@app.get("/{path:path}")` 基本不会命中。
- 结果：首页正常，但 `/some/deep/link` 会 404，而不是回 `index.html`。
- 正确做法：catch-all 放在 mount 之前，或用中间件处理，或改用显式路由白名单。

### 1.7 图片上传与 OSS

**Q35：为什么用预签名直传？**

- 后端只用 AK/SK 给 OSS 请求签名，**长期密钥不下发**。
- 图片流量不经过后端，节省带宽和内存，大文件更友好。
- 预签名有效期 1 小时，限制 key、content-type。

**Q36：预签名有哪些安全风险？**

- 用户可控 `filename`：可能覆盖同名文件、构造异常 key → 已用 `os.path.basename` + 去反斜杠，并按用户前缀 `u{id}/{name}` 隔离。
- content-type 只按扩展名猜，不校验真实文件头；也没有大小限制。
- `accessUrl` 假设 Bucket 公共读；私有桶应改成签名 GET。
- 建议：UUID 文件名、后缀白名单、上传回调校验、OSS 生命周期规则。

**Q37：前端直传需要什么配置？**

- Bucket CORS：允许来源（如 `http://IP:8001`）、方法 `PUT/GET/HEAD`、Headers `*`、暴露 `ETag`。
- 否则浏览器会拦截跨域 PUT。

### 1.8 部署与运维

**Q38：Docker 镜像是怎么构建的？**

- 基础镜像 `python:3.13-slim`；先安装 `uv==0.11.8`。
- 先 COPY `pyproject.toml` + `uv.lock`，`uv sync --frozen --no-dev`，利用层缓存。
- 再 COPY `app/` 和 `langgraph.json`。
- `WORKDIR /app/app`，`PYTHONPATH=/app`，保证相对路径 `../db` 和 `app.*` 导入都正确。
- 暴露 8001，`HEALTHCHECK` 请求 `/health`。

**Q39：服务器只有 1 核 1G，怎么跑起来？**

- 加 2G swap，避免构建/运行 OOM。
- 构建阶段只下载 wheel，不编译大型 C 扩展。
- 运行期单进程 Uvicorn；SQLite 持久化到宿主机目录。
- 这是演示级容量，不适合高并发。

**Q40：服务器拉不到 Docker Hub 怎么办？**

- 配置 `/etc/docker/daemon.json` 的 `registry-mirrors`（如 daocloud、1ms）。
- 去掉 Dockerfile 顶部的 `# syntax=docker/dockerfile:1`，避免额外拉取前端镜像。
- 先 `docker pull python:3.13-slim` 验证镜像源，再构建。

**Q41：数据怎么持久化？**

- `docker-compose.yml` 把宿主机 `./db` 挂到容器 `/app/db`。
- `personal_chief.db`（会话）和 `auth.db`（账号）都在宿主机，容器重建不丢。
- 定期备份 `db/` 目录；发布前打镜像 tag 作为回滚点。

**Q42：健康检查怎么做？**

- `GET /health` 不需要登录，返回 `{"status":"ok"}`。
- Docker `HEALTHCHECK` 每 30 秒请求一次；`start-period=30s` 给应用初始化时间。
- 健康检查不触发模型调用，成本为零。

**Q43：公网部署的安全风险？**

- 注册开放 → 额度被刷；建议预算告警/邀请码/限流。
- 无应用层配额，只有可选的 Nginx 限流。
- 裸 IP + HTTP：无 TLS，建议域名 + HTTPS。
- `.env` 中的 `AUTH_SECRET` 一旦泄露可伪造任意用户 token；必须随机长字符串并妥善保管。
- OSS Bucket 公共读会让上传图片可被公开访问。

### 1.9 工程化与优化

**Q44：当前最大的性能瓶颈是什么？**

- `search_recipes` 是 async 生成器，但内部同步 `agent.stream` + SQLite checkpointer，单请求会长时间占用事件循环。
- 并发请求会排队；SQLite 单连接进一步限制吞吐。
- 演示够用，生产需要异步化 + 外部存储。

**Q45：要支持高并发，怎么改？**

1. 换 `AsyncSqliteSaver`/Postgres checkpointer，`agent.astream` 全链路异步。
2. 模型/搜索调用加超时、重试、降级。
3. Uvicorn 多 worker + 外部数据库；会话不落本地文件。
4. 加应用层限流、配额、缓存（相同食材的检索结果）。
5. 前端流式加心跳和断线重连。

**Q46：怎么评估 Agent 效果？**

- 现状：没有评测集。
- 建议：构造“食材清单/图片 → 期望菜系与菜谱类型”的小样本集；用 LLM-as-judge 评估相关性、格式、营养合理性；记录延迟、Token 成本、工具调用次数。
- 回归指标：结构化字段完整率、菜系违规率、搜索失败率。

**Q47：如果重做，会怎么设计？**

- 前端用有源码的框架，不再依赖静态导出补丁。
- 认证用 HttpOnly Cookie + refresh token，或接第三方登录。
- checkpointer 用 Postgres/Redis，会话与账号统一存储。
- Agent 输出结构化 JSON（Pydantic 校验），评分与排序交给代码。
- 部署用 Nginx + HTTPS + 限流，CI/CD 自动构建。

**Q48：项目里有哪些“取巧”但值得讲的工程手段？**

- 用 `fetch` monkey patch 给预构建前端补鉴权。
- 用 `window.location.origin` 替换硬编码 API 地址。
- 用 `asyncio.sleep(0)` 让同步流式迭代器“看起来”是增量的。
- 用 `token_version` 实现 JWT 全设备退出。
- 用 `os.path.basename` + 用户前缀防路径穿越/覆盖。
- 用 Docker 层缓存把 `uv sync` 与代码分离。

---

## 2. 各模块实现细节与技术

### 2.1 目录与模块总览

| 模块 | 文件 | 职责 | 关键技术 |
|---|---|---|---|
| 应用入口 | `app/main.py` | FastAPI 实例、路由挂载、CORS、静态资源、`/health` | FastAPI、StaticFiles |
| 账号 API | `app/api/v1/auth.py` | 注册/登录/当前用户/退出 | Pydantic、HTTPException、Depends |
| 账号存储 | `app/auth/store.py` | 用户表 CRUD、token 版本 | SQLite、WAL、RLock |
| 账号安全 | `app/auth/security.py` | scrypt 哈希、JWT 签发校验、鉴权依赖 | hashlib.scrypt、PyJWT、HTTPBearer |
| 对话 API | `app/api/v1/chat.py` | 流式对话、历史、清空、菜系校验、用户线程隔离 | StreamingResponse、Depends |
| Agent | `app/agents/personal_chief.py` | 模型/工具/记忆/提示词/流式生成 | LangChain、LangGraph、Tavily、SqliteSaver |
| 上传 API | `app/api/v1/oss.py` | OSS 预签名、上传路径隔离 | 阿里云 OSS SDK、URL 编码 |
| 菜系 | `app/common/cuisines.py` | 八大菜系白名单 | 纯 Python 常量 |
| 数据模型 | `app/models/schemas.py` | 请求/响应模型 | Pydantic |
| 日志 | `app/common/logger.py` | 统一 logger 与格式 | logging |
| 登录页 | `app/static/login.html` | 账号密码表单、登录/注册切换 | 原生 JS、fetch |
| 前端门禁 | `app/static/auth-gate.js` | 鉴权、菜系弹窗、侧边栏、移动端布局 | fetch 拦截、DOM 克隆、注入 CSS |
| 部署 | `Dockerfile`、`docker-compose.yml`、`deploy/nginx.conf` | 镜像、编排、反代 | Docker、Compose、Nginx |

### 2.2 认证模块

**流程：**

1. 注册：校验用户名 3–32 字符、无空白；密码 6–128；`scrypt` 哈希后插入 `users`；重复用户名返回 409；成功 201 + token。
2. 登录：按用户名查库，`verify_password` 校验；失败统一 401（不区分账号不存在/密码错误，避免枚举）。
3. 鉴权：`get_current_user` 解析 Bearer token，校验签名/过期，再查用户表和 `token_version`。
4. 退出：`token_version += 1`，旧 token 全部失效。

**技术细节：**

- scrypt：N=16384、r=8、p=1、salt 16B、dklen 32；参数随哈希一起存储。
- JWT：HS256；claims `sub/username/ver/iat/exp`；TTL 默认 168h。
- `AUTH_SECRET` 缺失时 import 阶段抛错，属于 fail-fast 设计。
- `auth.db` 路径基于 `__file__` 推导，不受 CWD 影响；WAL 模式；单连接 + RLock。
- 用户名唯一约束由数据库保证，`IntegrityError` 转 409。

**面试可讲：** 为什么不用 bcrypt、JWT 如何撤销、为什么登录失败不提示“账号不存在”、localStorage 的 XSS 风险。

### 2.3 Agent 模块

**初始化（import 时完成）：**

```python
web_search = TavilySearch(max_results=5, topic="general")
model = init_chat_model(
    model="qwen3.8-omni-flash",
    model_provider="openai",
    base_url=os.getenv("DASHSCOPE_BASE_URL"),
    api_key=os.getenv("DASHSCOPE_API_KEY"),
)
connection = sqlite3.connect("../db/personal_chief.db", check_same_thread=False)
checkpointer = SqliteSaver(connection)
checkpointer.setup()
agent = create_agent(model, [web_search], checkpointer=checkpointer, system_prompt=...)
```

**流式函数 `search_recipes(prompt, image, thread_id, cuisine)`：**

1. 有菜系时，把 `【本次菜系】xx` 拼到用户文本前。
2. 有图时构造多模态 `HumanMessage`，否则纯文本。
3. `agent.stream(..., stream_mode="messages")` 逐片产出 `AIMessageChunk`。
4. 只 yield 非空 `content`，每片后 `await asyncio.sleep(0)`。
5. 异常捕获后 yield 兜底文案。

**技术点：** ReAct 循环、工具调用、多模态内容块、checkpoint 记忆、同步/异步混用的坑。

### 2.4 会话与多用户隔离

- 前端传 `thread_id`，后端拼成 `{user_id}:{thread_id}`，再交给 checkpointer。
- `GET /chat/messages` 和 `DELETE /chat/messages` 同样做前缀，防止越权。
- checkpointer 路径是相对 `../db/personal_chief.db`，依赖 CWD=`app/`。
- 清空会话是 `delete_thread`，不是删用户表。
- `get_message` 目前有 `isinstance` 判断错误，返回空列表（已知缺陷）。

### 2.5 对话 API 与流式响应

- `POST /api/v1/chat/stream`：校验菜系 → 拼用户线程 id → `StreamingResponse`。
- `media_type="text/event-stream"`，但 body 是裸文本分片。
- 响应头 `Cache-Control: no-cache, no-transform`、`X-Accel-Buffering: no`。
- 前端用 `fetch` + `ReadableStream` 读取，不是 `EventSource`。
- 反向代理需关闭 `proxy_buffering`。

### 2.6 菜系模块

- 后端：`CUISINES` 元组（川、鲁、粤、苏、浙、闽、湘、徽）；`ChatRequest.cuisine` 可选；非法值 400。
- Agent：菜系写进用户消息 + system prompt 双重约束。
- 前端：登录后弹模态框；`localStorage.cuisine`；`auth-gate.js` 改写 `/chat/stream` 请求体；侧栏展示与切换。

### 2.7 图片上传与 OSS

- `GET /api/v1/oss/presign?filename=...`，需要登录。
- 文件名用 `os.path.basename(filename.replace("\\", "/"))` 清洗，防止 `../`。
- key 为 `u{user_id}/{safe_name}`，用户间隔离。
- content-type 按扩展名映射（jpg/jpeg/png/gif/webp），未知类型给 `application/octet-stream`。
- 预签名 `PutObjectRequest` 有效期 3600 秒。
- `accessUrl` 用 `quote(key, safe="/")` 编码。
- 前端直传，需要 Bucket CORS；公共读 Bucket 才能直接展示。

### 2.8 前端改造模块

- `login.html`：原生 JS 登录/注册切换、密码确认、错误展示、`next` 参数校验（只允许站内路径，防开放重定向）。
- `auth-gate.js`（IIFE）：
  - 无 token → `/login.html?next=...`
  - 拦截 `fetch`，注入 Bearer
  - 401（非 auth 接口）→ 清 session + 跳登录
  - 拦截 `/chat/stream`，补 `cuisine`
  - 克隆 header 品牌/新建会话按钮，构建侧栏
  - 注入响应式 CSS（900px 断点）
  - 退出调用 `/auth/logout` 后清本地
- `index.html` 仅注入一个 `<script src="/auth-gate.js">`。
- 编译 chunk 的 API 基地址已改为 `window.location.origin`。

### 2.9 配置、日志与健康检查

- `.env` 由 `load_dotenv()` 加载；`AUTH_SECRET` 必填。
- 日志统一 `logging.getLogger("personal_chief")`，输出到 stdout。
- `/health` 公开、轻量、无模型调用。
- Docker `HEALTHCHECK` 轮询 `/health`。

### 2.10 部署模块

- `Dockerfile`：`python:3.13-slim` + `uv==0.11.8` + `uv sync --frozen --no-dev`；`WORKDIR /app/app`；`PYTHONPATH=/app`。
- `docker-compose.yml`：`env_file: .env`、`8001:8001`、`./db:/app/db`、`restart: unless-stopped`。
- `.dockerignore` 排除 `.env`、`db/`、`notebooks/`、`*.md`，避免密钥/数据进镜像。
- 服务器：CentOS 7、Docker 26.1.4、Compose 2.27.1、1 核 1G + 2G swap、EIP、安全组放行 8001。
- Docker Hub 不可达，使用镜像加速；发布前备份 `db/` 并打旧镜像 tag 以便回滚。

---

## 3. 项目中遇到的问题与解决

> 下面都是这个项目真实遇到过的问题，面试时可以按“现象 → 原因 → 解决 → 收获”讲。

### 3.1 从项目根目录启动，SQLite 打不开

- **现象：** `sqlite3.OperationalError: unable to open database file`。
- **原因：** `personal_chief.py` 里写的是相对路径 `../db/personal_chief.db`，从仓库根目录启动时解析到不存在的上级目录。
- **解决：** 进程 CWD 固定为 `app/`，并把仓库根目录加入 `PYTHONPATH`；Dockerfile 里用 `WORKDIR /app/app` + `PYTHONPATH=/app` 固化。
- **收获：** 相对路径是部署事故高发点，能用绝对路径/配置项就用；至少要在文档和启动脚本里写死 CWD。

### 3.2 Docker Hub 拉不到基础镜像

- **现象：** 构建报 `dial tcp ...: i/o timeout`，卡在 `docker.io/docker/dockerfile:1`。
- **原因：** 服务器网络无法直连 `registry-1.docker.io`；Dockerfile 顶部的 `# syntax` 还会额外拉前端镜像。
- **解决：** 配置 `registry-mirrors`（daocloud / 1ms）；删除 `# syntax` 行；先单独 `docker pull python:3.13-slim` 验证。
- **收获：** 国内/受限网络部署，先验证镜像源再构建，能省大量排查时间。

### 3.3 1G 内存构建容易 OOM

- **现象：** 小内存 ECS 上构建/运行有 OOM 风险。
- **解决：** 加 2G swap；构建阶段只用 wheel；单进程运行；镜像分层缓存减少重复安装。
- **收获：** 容器内存限制不等于宿主机内存；swap 是低配机器的最低成本兜底。

### 3.4 CentOS 7 已 EOL，软件源不可用

- **现象：** 老系统 `yum` 源失效，装 Docker 困难。
- **解决：** 该实例使用阿里云内网/镜像源仍可用；安装 Docker CE 26.1.4 + Compose 2.27.1。
- **收获：** EOL 系统要么切 Vault 源/镜像源，要么换系统；长期方案是升级到受支持的操作系统。

### 3.5 公网访问超时，本机却正常

- **现象：** 服务器内 `curl 127.0.0.1:8001` 返回 200，外网连接超时。
- **原因：** 阿里云安全组没有放行 8001。
- **解决：** 控制台入方向加 `TCP 8001`，授权对象 `0.0.0.0/0`。
- **收获：** 分层排查：应用 → 容器端口 → 主机防火墙 → 云安全组 → 网络。

### 3.6 `.env` 的 CRLF 导致环境变量异常

- **现象：** Windows 创建的 `.env` 是 CRLF，变量值可能带 `\r`。
- **解决：** 上传后 `sed -i 's/\r$//' .env`，统一 LF。
- **收获：** 跨平台部署要注意换行符；Docker Compose 的 `env_file` 对 CRLF 处理不一定符合预期。

### 3.7 模型调用返回 Arrearage（欠费）

- **现象：** DashScope 返回 `400 Arrearage`，应用只显示兜底文案。
- **原因：** 百炼账户欠费/未开通。
- **解决：** 充值/开通后恢复；期间尝试过切换 DeepSeek。
- **收获：** 外部模型依赖要有清晰的错误日志和降级提示，不能只吞异常。

### 3.8 DeepSeek key 无效（401）

- **现象：** 切到 `deepseek-chat` 后返回 `Authentication Fails`。
- **排查：** 检查 key 长度 35、`sk-` 前缀、去掉 `\r` 后仍无效；确认 key 被撤销/错误。
- **解决：** 换回 DashScope 的 `qwen3.8-omni-flash`（同时恢复图片能力）。
- **收获：** 模型切换要考虑多模态能力；密钥有效性要先用 `/user/balance` 之类接口验证。

### 3.9 流式输出变成“一次性返回”

- **现象：** 浏览器要等全部生成完才显示。
- **原因：** async 生成器里跑同步 `agent.stream`，事件循环被占住；中间层还可能缓冲。
- **解决：** 每片后 `await asyncio.sleep(0)`；响应头 `X-Accel-Buffering: no`；Nginx `proxy_buffering off`。
- **收获：** 流式不只是响应类型，整条链路（应用、反代、浏览器）都要配合。

### 3.10 预构建前端把 API 地址写死为 localhost

- **现象：** 本机可用，公网/手机不可用。
- **原因：** 编译 chunk 里是 `http://localhost:8001`。
- **解决：** 替换为 `window.location.origin`，同源自适应。
- **收获：** 静态产物要避免硬编码环境地址；无法重编译时可用运行时补丁。

### 3.11 没有前端源码，怎么加登录

- **难点：** Next.js 导出无法重编译。
- **解决：** 新增 `login.html` + `auth-gate.js`；脚本在 `<head>` 同步执行做门禁，拦截 fetch 加 token，克隆 DOM 造侧栏，注入响应式 CSS。
- **收获：** 理解“静态产物 + 运行时增强”的边界，也理解这种补丁的脆弱性（DOM 结构一变就失效）。

### 3.12 多用户会话串号风险

- **现象：** 如果直接用前端 `thread_id`，不同账号可能读到同一段 checkpoint。
- **解决：** API 层统一拼 `{user_id}:{thread_id}`；所有会话接口都要求登录。
- **收获：** 多租户隔离要在服务端做，不能信任客户端 ID。

### 3.13 历史消息接口始终为空

- **现象：** `GET /chat/messages` 永远返回 `[]`。
- **原因：** `isinstance(msg.content, HumanMessage)` 判断错对象。
- **解决：** 应改为 `isinstance(msg, HumanMessage)`；当前仍是已知缺陷。
- **收获：** 类型判断要看清对象层级；接口要有最基本的手工验证。

### 3.14 静态挂载导致 SPA fallback 失效

- **现象：** 首页正常，深层链接 404。
- **原因：** `StaticFiles` 挂载在 `/` 后，后面的 catch-all 不会命中。
- **解决：** 新路由要在 mount 之前注册；fallback 用中间件或调整挂载顺序。
- **收获：** Web 框架的路由顺序是“先匹配先赢”，静态挂载尤其要注意。

### 3.15 CORS 配置不合规

- **现象：** `allow_origins=["*"]` + `allow_credentials=True` 在浏览器端无效。
- **解决：** 同源部署规避；生产改白名单，并关闭 credentials。
- **收获：** CORS 不是“全开最省事”，凭证模式有严格限制。

### 3.16 OSS 文件名路径穿越/覆盖

- **现象：** 用户可传 `../../x.jpg` 或同名文件互相覆盖。
- **解决：** `basename` 清洗 + 按用户前缀 `u{id}/` 存储；URL 编码。
- **收获：** 任何用户可控的文件名都要先归一化；对象存储同样有路径语义。

### 3.17 开放注册带来的额度风险

- **现象：** 公网任何人都能注册并调用付费模型/搜索。
- **解决：** 目前靠预算告警；可加邀请码、邮箱验证、注册限流、关闭注册。
- **收获：** “能登录”不等于“可控”；公网产品要有配额和风控。

### 3.18 AUTH_SECRET 缺失/轮换

- **现象：** 缺 `AUTH_SECRET` 时应用启动失败；轮换后所有 token 失效。
- **解决：** fail-fast + 文档说明；轮换作为“全站强制下线”的手段。
- **收获：** 密钥管理要显式：必填校验、随机生成、备份、轮换预案。

---

## 4. 简历项目简介

### 4.1 标准版（推荐，可直接粘贴）

**AI 私人厨师｜个人项目｜Python / FastAPI / LangChain / LangGraph / Docker**

- 基于 LangGraph 构建多模态食材推荐 Agent，接入 Qwen 多模态模型与 Tavily 联网检索，支持拍照识别食材、按八大菜系约束检索，并以流式输出结构化菜谱推荐。
- 设计并实现 JWT 账号体系：scrypt 密码哈希、token 版本失效（全设备退出）、注册/登录/鉴权接口；按 `user_id` 隔离会话记忆，避免多账号 thread 冲突。
- 在无前端源码的预构建 Next.js 静态站上，通过注入脚本实现登录门禁、请求自动鉴权、菜系弹窗与响应式侧边栏；使用 OSS 预签名实现图片直传。
- 使用 Docker Compose 单机部署至阿里云 ECS，完成镜像加速、健康检查、数据卷持久化与备份回滚，公网可访问。

### 4.2 精简版（两行）

> 独立开发多模态 AI 菜谱推荐 Web 应用：LangGraph Agent + Qwen 多模态模型 + Tavily 检索，支持拍照识别、八大菜系约束与流式输出；自研 JWT 鉴权与会话隔离，Docker 部署至阿里云 ECS。

> 在无前端源码的预构建静态站上完成登录、鉴权、菜系选择与移动端适配；解决流式阻塞、Docker 镜像源、1G 内存构建、安全组等部署问题。

### 4.3 技术栈行（简历技能栏可拆用）

`Python` `FastAPI` `LangChain` `LangGraph` `Prompt Engineering` `Tavily` `Qwen/DashScope` `JWT` `scrypt` `SQLite` `Docker/Compose` `Nginx` `阿里云 OSS/ECS` `JavaScript`

### 4.4 面试口述版（约 60 秒）

> 这是一个面向“今天吃什么”的 AI 私人厨师项目。用户可以拍食材照片或输入清单，选择八大菜系，Agent 会用多模态模型识别食材，调用 Tavily 联网找菜谱，按营养和难度排序后流式返回。  
> 后端是 FastAPI + LangChain/LangGraph，模型走 DashScope 的 OpenAI 兼容接口，会话用 SQLite checkpointer 记忆，并按用户 ID 隔离线程。  
> 鉴权是我自己实现的：scrypt 存密码、JWT 做登录、token 版本控制退出登录；前端是没有源码的预构建静态站，我通过注入脚本补上了登录门禁、自动携带 token、菜系弹窗和移动端布局。  
> 项目用 Docker Compose 部署在阿里云 ECS 上，期间解决了 Docker Hub 不可达、1G 内存构建、流式被缓冲、安全组放行等问题。

### 4.5 写简历的注意事项

- **不要编造用户量、QPS、准确率**：项目没有这些数据，面试官追问会穿帮。
- 可以写的真实成果：公网可访问、端到端联调通过、支持图文双输入、八大菜系、JWT 鉴权、Docker 单机部署、1C1G 环境可运行。
- 如果写“高并发/分布式”，必须能解释当前实现的局限；否则改成“单机演示部署”。
- 代码里的已知缺陷（历史接口、并发阻塞）不要写成优点，但可以主动作为“反思与改进”讲。

---

## 5. 面试前速查

### 5.1 关键数字

| 项 | 值 |
|---|---|
| 菜系数量 | 8（川鲁粤苏浙闽湘徽） |
| Tavily 检索条数 | 5 |
| JWT 有效期 | 168 小时（7 天） |
| scrypt 参数 | N=2¹⁴, r=8, p=1, salt=16B, dklen=32 |
| 预签名有效期 | 3600 秒 |
| 移动端断点 | 900px |
| 服务端口 | 8001 |
| 部署规格 | 1 核 1G + 2G swap |

### 5.2 关键文件

| 路径 | 作用 |
|---|---|
| `app/main.py` | FastAPI 入口、路由、静态托管、`/health` |
| `app/api/v1/auth.py` | 注册/登录/当前用户/退出 |
| `app/auth/security.py` | scrypt + JWT + 鉴权依赖 |
| `app/auth/store.py` | `db/auth.db` 用户表 |
| `app/api/v1/chat.py` | 对话流式、历史、清空、菜系校验、线程隔离 |
| `app/agents/personal_chief.py` | Agent、模型、工具、记忆、流式生成 |
| `app/api/v1/oss.py` | OSS 预签名 |
| `app/common/cuisines.py` | 菜系白名单 |
| `app/static/login.html` | 登录页 |
| `app/static/auth-gate.js` | 前端鉴权/菜系/布局补丁 |
| `Dockerfile` / `docker-compose.yml` | 部署 |
| `DEPLOY.md` | 完整部署运维手册 |

### 5.3 常用命令

```bash
# 本地开发
cd app && PYTHONPATH=".." ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001

# 注册并拿 token
curl -X POST http://127.0.0.1:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"secret123"}'

# 带 token 查历史
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8001/api/v1/chat/messages?thread_id=test"

# 部署
docker compose up -d --build
docker compose logs -f --tail=200
```

### 5.4 面试官可能追问的“送命题”

| 追问 | 回答要点 |
|---|---|
| 你的 Agent 准确率多少？ | 没有评测集，不能报数字；说明会怎么建评测集 |
| 并发多少？ | 单机演示级，同步流式会阻塞；给出异步化方案 |
| 为什么不用 bcrypt？ | 标准库 scrypt 零依赖、内存硬；bcrypt/argon2 也合理 |
| JWT 放 localStorage 安全吗？ | 有 XSS 风险；HttpOnly Cookie + CSRF 更稳 |
| 注册开放怎么办？ | 邀请码/邮箱验证/限流/关闭注册 |
| SQLite 能上生产吗？ | 单机演示可以；多实例/高并发换 Postgres |
| 图片存 OSS 安全吗？ | 预签名 + 用户前缀；私有桶 + 签名 GET 更好 |
| 怎么防提示词注入？ | 菜系白名单、系统提示约束；搜索结果仍不可信，需引用校验 |
| 历史接口为什么空？ | 主动承认 isinstance 写错，给出修复代码 |
| 重做会怎么改？ | 有源码前端、HttpOnly、Postgres、结构化输出、CI/CD |

---

> 最后提醒：面试时**先讲业务价值，再讲链路，最后主动抛一个坑和改法**。这个项目最亮的不是“用了 LangChain”，而是：在没有前端源码、只有 1G 内存、网络受限的条件下，把多模态 Agent 真正部署上线并补全了账号体系。
