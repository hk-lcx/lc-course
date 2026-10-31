# 部署指南（阿里云 + Docker）

目标：把本项目部署到阿里云 ECS / 轻量应用服务器，通过 `http://<公网IP>:8001` 公开访问。
架构：单容器（FastAPI + 预构建前端）+ 宿主机目录挂载 SQLite + 可选 Nginx 反代/HTTPS。

> ⚠️ **公开访问的风险（你已选择公开，请务必读）**
> 1. 聊天与上传接口已要求登录，但**注册是开放的**：任何拿到 URL 的人都能注册账号并调用 `/api/v1/chat/stream`，直接消耗你的 DashScope / DeepSeek / Tavily 额度。请在这些平台设置**用量上限或预算告警**；若不希望陌生人注册，可先关闭注册接口或加邀请码。
> 2. `GET /api/v1/oss/presign` 需要登录，但每个用户都能拿到自己前缀下的上传签名；若 Bucket 是公共读，上传的文件也可被公开访问。建议给 Bucket 单独设预算/生命周期规则，或后续改成私有桶 + 签名 GET。
> 3. `.env` 里是真实密钥，**只会通过 SSH 上传到服务器，不要提交到 Git、不要打进镜像**（`.dockerignore` 已排除）。`AUTH_SECRET` 一旦泄露，任何人都能伪造登录凭证，务必使用随机长字符串。

---

## 0. 服务器准备（阿里云控制台）

| 项目 | 建议 |
|---|---|
| 实例 | ECS 或「轻量应用服务器」，2 核 2G 起（构建镜像需要约 2G 内存，1G 可能 OOM） |
| 系统 | Ubuntu 22.04 / 24.04 LTS |
| 磁盘 | 30G+ |
| 地域 | 国内地域访问快；但**域名 + 大陆服务器需要 ICP 备案**。不备案也能用 `IP:8001` 直接访问。想免备案可选题香港/新加坡地域 |
| 安全组/防火墙 | 放行 **TCP 22**、**TCP 8001**（若后续用 Nginx 则放行 80/443，8001 不必对公网开放） |

系统内放行端口（Ubuntu 默认 ufw 可能未启用，启用时记得放行 SSH）：

```bash
ufw allow 22/tcp
ufw allow 8001/tcp
ufw enable
```

---

## 1. 安装 Docker（Ubuntu）

```bash
# 官方脚本 + 阿里云镜像源
curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
systemctl enable --now docker
docker compose version
```

如果脚本下载失败，改用 apt：

```bash
apt-get update
apt-get install -y docker.io docker-compose-v2
systemctl enable --now docker
```

**国内拉取镜像慢/失败时**，配置镜像加速器（第三方源可能变动，失败就换阿里云容器镜像服务的专属加速地址）：

```bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": ["https://docker.m.daocloud.io"]
}
EOF
systemctl restart docker
```

---

## 2. 上传项目（在 Windows 的 Git Bash 中执行）

```bash
cd "/d/PyCharm 2026.1/lc-cource"

# 打包（排除虚拟环境、notebook、IDE 等无关目录，保留 .env 和 db/personal_chief.db）
tar --exclude='./.venv' \
    --exclude='./notebooks' \
    --exclude='./.idea' \
    --exclude='./.ipynb_checkpoints' \
    --exclude='./.langgraph_api' \
    --exclude='./__pycache__' \
    --exclude='./app/**/__pycache__' \
    --exclude='./db/*-wal' \
    --exclude='./db/*-shm' \
    -czf /tmp/personal-chief.tar.gz .

scp /tmp/personal-chief.tar.gz root@<服务器IP>:/opt/
```

> 用密码登录会提示输入密码；更推荐在阿里云控制台给实例绑定 SSH 密钥。

登录服务器解压：

```bash
ssh root@<服务器IP>
mkdir -p /opt/personal-chief
tar -xzf /opt/personal-chief.tar.gz -C /opt/personal-chief
cd /opt/personal-chief
ls -la          # 应能看到 Dockerfile / docker-compose.yml / .env / app / db
```

如果不想上传本地 `.env`，可在服务器上手动创建（键名见下），但**打包时不要漏掉**：

```
DEEPSEEK_API_KEY=
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=
TAVILY_API_KEY=
LANGSMITH_API_KEY=
LANGSMITH_TRACING=
LANGSMITH_PROJECT=
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_BUCKET=
# 必填：JWT 签名密钥，用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成
AUTH_SECRET=
# 可选：登录有效期（小时），默认 168（7 天）
AUTH_TOKEN_TTL_HOURS=168
```

> `AUTH_SECRET` 缺失时服务会在启动阶段直接报错。用户表存放在 `db/auth.db`（与 checkpointer 分开），首次访问时自动创建，已被 `docker-compose.yml` 的 `./db` 挂载覆盖。

---

## 3. 构建并启动

```bash
cd /opt/personal-chief
docker compose up -d --build
docker compose logs -f          # Ctrl+C 退出日志跟踪，不影响容器
```

首次构建约 3–10 分钟（视网络）。看到 `Application startup complete` 即成功。

本机验证：

```bash
curl -sS http://127.0.0.1:8001/api/v1/chat/messages?thread_id=test
curl -sS http://127.0.0.1:8001/ | head -c 200
```

注册 / 登录验证：

```bash
curl -sS -X POST http://127.0.0.1:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"secret123"}'
# 返回 access_token 后：
TOKEN=...
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8001/api/v1/chat/messages?thread_id=test"
```

公网验证（回到你的电脑）：

```bash
curl -sS http://<服务器IP>:8001/health
curl -sS http://<服务器IP>:8001/api/v1/chat/messages?thread_id=test   # 未登录应返回 401
```

浏览器打开 `http://<服务器IP>:8001/`，会先跳转到 `/login.html`，注册或登录后再使用。

---

## 4. 阿里云 OSS 配置（图片上传必需）

前端是浏览器直传 OSS，需要在 Bucket 上配置 **CORS**，否则上传会被浏览器拦截：

- 来源 Origin：`http://<服务器IP>:8001`（有域名时加上 `https://你的域名`）
- 允许 Methods：`PUT`、`GET`、`HEAD`
- 允许 Headers：`*`
- 暴露 Headers：`ETag`

另外代码里的 `accessUrl` 形如 `https://<bucket>.<endpoint>/<filename>`，意味着 Bucket 需要**公共读**才能显示图片；如果 Bucket 是私有，图片会 403（需要改成签名 GET，属于代码改动）。

---

## 5. 可选：Nginx 反向代理 / 域名 / HTTPS

不配也能用 `IP:8001`。需要域名或 HTTPS 时：

```bash
apt-get install -y nginx
cp /opt/personal-chief/deploy/nginx.conf /etc/nginx/conf.d/personal-chief.conf
nginx -t && systemctl reload nginx
```

- 安全组放行 80/443；建议不再对公网开放 8001。
- `deploy/nginx.conf` 已关闭 `proxy_buffering`（否则流式输出会变成一次性返回），并带有一个简单的按 IP 限流（60 次/分钟），防止公网被刷额度。
- **中国大陆服务器绑定域名需要 ICP 备案**，否则 80/443 无法正常对外提供服务；香港/海外地域不需要。
- HTTPS：

```bash
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d your-domain.com
```

---

## 6. 日常运维

```bash
cd /opt/personal-chief

docker compose ps                 # 状态
docker compose logs -f --tail=200 # 日志
docker compose restart            # 重启
docker compose down               # 停止

# 更新代码：重新打包上传并解压后
docker compose up -d --build

# 备份会话数据库
mkdir -p /opt/backup
tar -czf /opt/backup/db-$(date +%F-%H%M).tar.gz -C /opt/personal-chief db
```

---

## 7. 常见问题

| 现象 | 排查 |
|---|---|
| 构建卡在拉取基础镜像 | 配置 Docker 镜像加速器（第 1 节），或换网络 |
| `uv sync --frozen` 失败（lock 过期） | 本地执行 `uv lock` 后重新上传 `uv.lock`；或把 Dockerfile 中改为 `uv sync --no-dev` |
| 容器反复重启 | `docker compose logs --tail=200`；常见原因：`.env` 缺失/键值错误、`db` 目录无写权限 |
| 公网访问不了 | 安全组 8001、系统 ufw、容器是否 `Up`、`curl 127.0.0.1:8001` 是否有响应 |
| 流式输出变成「憋一大段」 | 经过 Nginx 时必须 `proxy_buffering off` |
| 图片上传报 CORS | 配置 Bucket CORS（第 4 节） |
| 图片 403 | Bucket 非公共读，或 `accessUrl` 域名不对 |
| 对话报「信息检索失败」 | 模型/搜索密钥错误或额度不足，`docker compose logs` 里有具体异常 |
| 首次请求很慢 | 导入时初始化模型/工具/数据库，属正常；健康检查会预热容器 |

---

## 8. 已知限制（公开部署前请知悉）

- **并发能力有限**：`search_recipes` 是 async 生成器但内部调用同步 `agent.stream`，会阻塞事件循环；SQLite 也是单连接。少量用户演示没问题，多人同时使用会排队。
- **账号体系较简**：开放注册、无邮箱验证/找回密码/角色权限；`POST /auth/logout` 通过递增 `users.token_version` 作废该账号的所有旧 token（会让该账号在其他设备上也退出），修改 `AUTH_SECRET` 则会作废全部账号的 token。应用层没有配额，只有可选的 Nginx 限流；建议设置各 API 平台的消费上限。
- **单实例**：SQLite 持久卷方案不能水平扩容；要扩容需换 Postgres checkpointer 并改代码。
- **历史接口失效**：`GET /chat/messages` 因 `isinstance` 判断写错返回空列表，不影响聊天本身。
