FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# 使用清华源安装 uv（国内构建更稳定）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple uv==0.11.8

WORKDIR /app

# 依赖清单单独一层，充分利用 Docker 构建缓存
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 应用代码与静态前端（app/static 为预构建 Next.js 导出）
COPY app ./app
COPY langgraph.json ./

# SQLite 目录；运行时由持久卷覆盖
RUN mkdir -p /app/db

# personal_chief.py 中 DB 路径为相对路径 ../db/personal_chief.db，
# 因此进程必须在 /app/app 下启动，且仓库根目录在 PYTHONPATH 中。
WORKDIR /app/app
ENV PYTHONPATH=/app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD /app/.venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)" || exit 1

CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
