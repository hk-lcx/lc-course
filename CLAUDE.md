# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A Chinese-language AI "private chef" demo (`lc-cource`, LangChain course project). The user uploads a photo of ingredients or types a list; an agent identifies ingredients, searches the web for recipes with Tavily, ranks them by nutrition and difficulty, and streams a structured recommendation back.

Stack: FastAPI backend + a **pre-built** Next.js static export served by the same process, LangChain 1.x agents on LangGraph, Qwen (`qwen3.8-omni-flash`) through DashScope's OpenAI-compatible endpoint, Tavily web search, and a SQLite checkpointer for conversation memory. Python >= 3.13, dependencies managed by `uv` (`pyproject.toml` + `uv.lock`).

## Commands

Dependencies are managed with uv, not pip:

```bash
uv sync                                   # create/refresh .venv from uv.lock
uv add <package>                          # add a dependency
```

`pyproject.toml` pins the Tsinghua PyPI mirror as the default index (`[[tool.uv.index]]`), so syncs work without a VPN but can lag behind upstream for brand-new releases.

`requirements.txt` is a stale UTF-16 `pip freeze` from another machine (contains `/home/...` and `C:/...` paths) — do not install from it; use `uv sync`.

Run the server (development):

```bash
# from the repo root
cd app
PYTHONPATH=".." ../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

On Windows the venv interpreter is `.venv/Scripts/python.exe` (activate with `.venv/Scripts/activate`). In PyCharm, the `app/main.py` run configuration is already set up with working directory `app/` and the project root on `PYTHONPATH`; `python -m app.main` also works from `app/` (`uvicorn.run(..., reload=True)`).

Alternative: `langgraph dev` at the repo root (see `langgraph.json`) exposes the graph as `chief_agent` in LangGraph Studio. It is subject to the same working-directory caveat below.

Smoke checks once running:

```bash
curl http://127.0.0.1:8001/                                               # static frontend
curl "http://127.0.0.1:8001/api/v1/chat/messages?thread_id=smoke-test"    # API
curl -X POST http://127.0.0.1:8001/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"你好","thread_id":"smoke-test"}'                        # streams via paid LLM + Tavily
```

There are no tests, no linter, and no CI configured.

## Architecture

**Entrypoint and layering.** `app/main.py` builds the FastAPI app, calls `setup_logging()`, mounts routers under `/api/v1`, mounts the frontend from `app/static`, and defines a catch-all `GET /{path:path}` intended as SPA fallback. Routers:

- `app/api/v1/auth.py` — `POST /auth/register`, `POST /auth/login`, `GET /auth/me`.
- `app/api/v1/chat.py` — `POST /chat/stream` (chat), `GET`/`DELETE /chat/messages` (history, clear).
- `app/api/v1/oss.py` — `GET /oss/presign?filename=...` returns a presigned Alibaba OSS PUT URL and the public `accessUrl`.
- `app/models/schemas.py` — the only Pydantic models (`ChatRequest`: `message`, optional `image_url`, required `thread_id`, optional `cuisine`; plus auth request/response models).
- `app/common/logger.py` — shared `logger = logging.getLogger("personal_chief")`; `setup_logging()` (stdout handler) is called only from `app/main.py`.
- `GET /health` is an unauthenticated lightweight probe used by the Docker `HEALTHCHECK`.

CORS is wide open (`allow_origins=["*"]`, credentials allowed). `StaticFiles(html=True)` is mounted at `/`, which matches every path, so it shadows the catch-all declared after it: unknown paths (including deep links) get StaticFiles' 404 rather than `index.html`. Register new routes before the mount to ensure they win.

**Agent.** `app/agents/personal_chief.py` is the core. A **single module-level `agent` is constructed at import time** (there is no factory): model via `init_chat_model(model="qwen3.8-omni-flash", model_provider="openai", base_url=$DASHSCOPE_BASE_URL, api_key=$DASHSCOPE_API_KEY)`, one `TavilySearch` tool, a Chinese `system_prompt` describing the 4-step flow (identify → web search → score → structured report), and a `SqliteSaver` checkpointer. Because everything is import-time, importing the module (and therefore starting the server) requires all API keys in `.env` present and the SQLite path to be valid.

**Chat contract.** `search_recipes()` builds a `HumanMessage` — multimodal (`image` + `text` blocks) when `image_url` is non-empty, plain text otherwise — and streams `agent.stream(..., stream_mode="messages")`, yielding only non-empty `AIMessageChunk.content` strings. It uses the **sync** `agent.stream` because `SqliteSaver` has no async methods (`astream` raises); to keep the output actually incremental it does `await asyncio.sleep(0)` after every chunk — without that yield the event loop stays blocked and (notably on Windows) all chunks reach the client only when generation finishes. The HTTP endpoint wraps this in `StreamingResponse(media_type="text/event-stream")`, but the chunks are **raw token text, not `data:` framed SSE**; the frontend consumes the raw stream. `thread_id` is the LangGraph conversation key; `clear_message()` calls `checkpointer.delete_thread()`. Errors are swallowed and turned into a fallback message string.

**Image upload flow.** The frontend calls `oss/presign` → PUTs the image to OSS directly → passes the resulting `accessUrl` as `image_url` to `chat/stream`. OSS region is hard-coded to `cn-beijing` in `oss.py`; bucket/keys come from env. Presigned keys are scoped per user (`u{user_id}/{basename}`), so the returned `accessUrl` differs from the old flat `{filename}` layout.

**Auth.** Accounts are username + password only.
- `app/auth/store.py` — own SQLite file `db/auth.db` (path derived from the module file, so it works regardless of CWD; unlike the checkpointer). One shared connection guarded by an `RLock`, WAL mode, `users(id, username UNIQUE, password_hash, created_at)`.
- `app/auth/security.py` — password hashing via stdlib `hashlib.scrypt` (no extra dependency) and HS256 JWT via PyJWT. **Requires `AUTH_SECRET`**, raises at import if missing; `AUTH_TOKEN_TTL_HOURS` optional (default 168). Exposes `get_current_user`, the FastAPI dependency guarding chat and OSS.
- `app/api/v1/auth.py` — register returns 201 + a token (auto-login), duplicate username 409, wrong credentials 401; username 3-32 chars without whitespace, password 6-128. `POST /auth/logout` bumps `users.token_version`, invalidating every token issued earlier for that account (all devices).
- Chat endpoints rewrite the client `thread_id` to `{user_id}:{thread_id}` before touching the checkpointer, so accounts cannot read each other's history even if they reuse a `thread_id`.
- Registration is open — anyone who can reach the app can create an account (note this when deploying publicly).

**Cuisine selection.** The eight cuisines live in `app/common/cuisines.py`. Every login sets `localStorage.cuisine_pending = "1"`; `auth-gate.js` then shows a blocking modal before the chat is usable and stores the choice in `localStorage.cuisine`. Because the built frontend doesn't send a cuisine field, the same script rewrites `/api/v1/chat/stream` request bodies to add `cuisine`; the sidebar displays the current choice with a 切换 link (hidden below 900px). The backend rejects unknown cuisines with 400 and `search_recipes()` prepends `【本次菜系】…请只推荐…` to the human message (text block in multimodal mode), while the system prompt tells the agent not to recommend other cuisines.

**Checkpoint storage.** `db/personal_chief.db` (tables `checkpoints`, `writes`). `.langgraph_api/` holds LangGraph CLI's own local state.

**Frontend.** `app/static/` is a built Next.js site (`_next/`, `index.html`) — there is no frontend source in this repo. It calls the chat and OSS routes; the compiled chunks' API base was patched from `http://localhost:8001` to `window.location.origin`, so the same export works on localhost and behind the public IP. Because it cannot be rebuilt, the login UI is bolted on by hand: `login.html` (vanilla JS login/register form) and `auth-gate.js` (redirects to login when no token, monkey-patches `window.fetch` to add `Authorization: Bearer` on `/api/v1/` calls, redirects on 401, and replaces the header with a fixed left sidebar — cloned brand, current user + 退出当前账号, and a cloned 新建会话 button that forwards clicks to the hidden original React button — while injected CSS shifts the chat pane and bottom input bar to the right; below 900px the sidebar collapses into a top bar). The original header stays in the DOM but is hidden. `index.html` contains exactly one injected `<script src="/auth-gate.js">` in `<head>` — do not regenerate or reformat the export, or these patches are lost. It also hard-codes the tab title `AI私人厨师` and the SVG favicon `app/static/favicon.svg` (chef-hat logo) in **both** the static head and the streamed Next metadata JSON — editing only `<title>` is overwritten at hydration.

**Notebooks.** `notebooks/*.ipynb` are course lessons (models, prompt, tools, messages, memory, helloworld); `notebooks/实战.ipynb` is the prototype this app was productized from. They have their own `resources/` SQLite files. They are learning material, not part of the app.

## Deployment

Single-container Docker Compose deployment to Alibaba Cloud ECS; the full runbook (security groups, OSS CORS, Nginx/HTTPS, backups) is `DEPLOY.md` — read it before changing deploy files.

```bash
docker compose up -d --build     # build image and start on :8001
docker compose logs -f --tail=200
docker compose down
```

- `Dockerfile` runs `uv sync --frozen --no-dev`, copies only `app/` + `langgraph.json`, then sets `WORKDIR /app/app` and `PYTHONPATH=/app` — the same CWD-is-load-bearing constraint as local dev. A `HEALTHCHECK` polls `/health`.
- `docker-compose.yml` mounts host `./db` to `/app/db` so both SQLite databases (checkpointer + `auth.db`) survive rebuilds, and injects `.env` via `env_file`.
- `.dockerignore` excludes `.env`, `db/`, `notebooks/`, and `*.md`, so secrets and the database are never baked into the image — deploy must ship `.env` and `db/` separately (see `DEPLOY.md` §2).
- `deploy/nginx.conf` is an optional reverse proxy; it disables `proxy_buffering` (required for streaming) and rate-limits by IP.

## Gotchas

- **Working directory is load-bearing.** `personal_chief.py` opens SQLite at the relative path `../db/personal_chief.db`, so the process must run with CWD = `app/` (as the PyCharm run config does). Starting from the repo root fails with `unable to open database file`. Add `PYTHONPATH=<repo root>` when running from `app/` so `app.*` imports resolve.
- Configuration lives in `.env` (loaded via `load_dotenv()`): `AUTH_SECRET` (**required** — the app raises at import without it), optional `AUTH_TOKEN_TTL_HOURS`, `DASHSCOPE_API_KEY`/`DASHSCOPE_BASE_URL`, `TAVILY_API_KEY`, `LANGSMITH_*`, `OSS_ACCESS_KEY_ID`/`OSS_ACCESS_KEY_SECRET`/`OSS_BUCKET` (optional `OSS_ENDPOINT`), plus a now-unused `DEEPSEEK_API_KEY`. `langgraph.json` points its `env` at `.env`. Rotating `AUTH_SECRET` invalidates every issued token.
- `get_message()` in `personal_chief.py` checks `isinstance(msg.content, HumanMessage/AIMessage)` on the content rather than the message object, so `GET /chat/messages` currently returns an empty list even when a thread has history.
- The agent/checkpointer are global singletons over one `sqlite3` connection opened with `check_same_thread=False`; tests or scripts that import the module share that state.
- `search_recipes` is declared `async def` but calls the **synchronous** `agent.stream(...)`, so one chat request blocks the event loop for its whole duration. Fine for demos, not concurrent; this is also why the Nginx config rate-limits.
- The repo-root `main.py` is a PyCharm "new project" sample (`print_hi('PyCharm')`) — not an entrypoint. The app entrypoint is `app/main.py`.
- App code reads `AUTH_SECRET`/`AUTH_TOKEN_TTL_HOURS`, `DASHSCOPE_*`, `TAVILY_API_KEY`, and `OSS_*`; `DEEPSEEK_API_KEY` is a leftover (notebooks / the `langchain-deepseek` dependency), and `LANGSMITH_*` is consumed implicitly by LangChain tracing.
- `curl`-ing chat/OSS routes now needs `Authorization: Bearer <token>`; get one from `POST /api/v1/auth/register` or `/auth/login`. `GET /health` stays public.
- The auth DB path is resolved from `app/auth/store.py`, so unlike the checkpointer it does **not** depend on CWD. Deleting `db/auth.db` removes all accounts; existing JWTs then fail with 401 because `get_current_user` re-reads the user row.
