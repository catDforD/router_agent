# 本地开发环境搭建

本指南用于在本地启动 Router 后端、PostgreSQL 以及本地 Artifact Store。

## 前置条件

- 使用 `uv` 管理 Python 环境
- Docker 与 Docker Compose，或本地 PostgreSQL
- 从仓库根目录打开的 shell

## 环境配置

创建本地环境文件：

```bash
cp .env.example .env
```

安装脚本会自动加载 `.env`。如果要在当前 shell 中手动运行命令，请这样加载：

```bash
set -a
source .env
set +a
```

默认的本地数据库 URL 为：

```text
postgresql+psycopg://router:router@localhost:5432/router
```

本地 artifact 内容会写入：

```text
data/artifacts/
```

`.env` 和 `data/` 都已被 Git 忽略。

## 方案 A：Docker Compose PostgreSQL

启动 PostgreSQL：

```bash
docker compose up -d postgres
```

检查就绪状态：

```bash
docker compose ps
docker compose exec postgres pg_isready -U router -d router
```

如果你的网络无法拉取 Docker Hub 镜像，请使用下面的 WSL/手动安装方式。

## 方案 B：Linux PostgreSQL

安装并启动 PostgreSQL：

```bash
sudo apt update
sudo apt install -y postgresql postgresql-client
sudo service postgresql start
```

创建或重置本地用户和数据库：

```bash
sudo -u postgres psql <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'router') THEN
    CREATE ROLE router LOGIN PASSWORD 'router';
  ELSE
    ALTER ROLE router WITH LOGIN PASSWORD 'router';
  END IF;
END
$$;
SQL

sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = 'router'" | grep -q 1 || sudo -u postgres createdb -O router router
sudo -u postgres psql -c "ALTER DATABASE router OWNER TO router;"
```

验证项目连接串：

```bash
psql 'postgresql://router:router@localhost:5432/router' -c 'select current_user, current_database();'
```

预期用户和数据库：

```text
router | router
```

## 执行迁移并创建 Artifacts

在 PostgreSQL 可访问后运行初始化辅助脚本：

```bash
bash scripts/dev_setup_db.sh
```

也可以手动执行这些步骤：

```bash
mkdir -p data/artifacts
uv sync
uv run alembic upgrade head
uv run python scripts/dev_create_artifacts.py
```

artifact 脚本会打印生成的 artifact ID 和示例 curl 命令。

## 一键启动本地开发栈

完成依赖安装和数据库准备后，可以从仓库根目录使用本地启动器：

```bash
uv run main.py
```

默认行为：

- 读取 `.env` 中的本地配置。
- 确保 `ARTIFACT_ROOT` 目录存在。
- 使用 `.env` 中的 `DATABASE_URL`，并等待数据库端口可用。
- 执行 `alembic upgrade head`。
- 启动后端 API：`http://127.0.0.1:8000`。
- 启动前端 Vite dev server：`http://127.0.0.1:5173`。
- 当 `MCP_MODE=real` 或 hybrid 配置中有 worker 使用 real 模式时，启动本地 PLC worker MCP server。
- 在终端中给每个子进程日志加前缀，并输出进程表和访问地址。

启动成功后终端会输出类似：

```text
Processes
  Name         PID      Port/Service   Status       Command
  backend      12345    8000           running      ...
  frontend     12346    5173           running      ...

Access URLs
  Frontend        http://127.0.0.1:5173
  Backend         http://127.0.0.1:8000
  Health          http://127.0.0.1:8000/api/health
  OpenAPI         http://127.0.0.1:8000/docs
  Task API        http://127.0.0.1:8000/api/tasks
  Task SSE        http://127.0.0.1:8000/api/tasks/<task_id>/events
```

启动后打开：

```text
http://127.0.0.1:5173
```

提交一个 PLC 任务即可验证完整前后端链路，例如：

```text
帮我写一个电机启停控制逻辑，包含启动、停止和运行指示。
```

前端中间区域应能看到 Main Agent 公开消息、工具调用、工具结果和最终报告入口；右侧可查看 worker、gate 和 artifact。

常用选项：

```bash
uv run main.py --install-frontend-deps
uv run main.py --with-postgres
uv run main.py --no-postgres
uv run main.py --no-migrate
uv run main.py --with-worker
uv run main.py --no-worker
uv run main.py --dry-run
```

说明：

- 默认使用本机已有 PostgreSQL；只有加 `--with-postgres` 才会启动 Docker Compose PostgreSQL。
- `--no-postgres` 保留为兼容选项，含义是明确使用本地数据库。
- 如果前端依赖尚未安装，启动器会提示运行 `cd frontend && npm install`，或可使用 `--install-frontend-deps` 让启动器先执行安装。
- 启动器会在启动子进程前检查 8000、5173、9000 等托管端口；如果已有服务占用，请先停止它，或用 `--no-frontend` / `--no-backend` / `--no-worker` 复用外部服务。
- `Ctrl-C` 会停止启动器管理的后端、前端和 worker 子进程。
- Docker Compose PostgreSQL 默认保留运行时数据和 volume；需要停止服务可使用 `docker compose stop postgres`。

## 启动 API

```bash
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

该本地 API 的前端集成细节记录在
[`docs/frontend-api.md`](frontend-api.md) 中，包括任务创建、SSE 事件、
artifact 读取、最终报告以及 trace 摘要。

## 启动前端

手动启动前端时，先安装依赖：

```bash
cd frontend
npm install
npm run dev
```

Vite 开发服务器默认监听：

```text
http://127.0.0.1:5173
```

开发服务器会把 `/api/*` 请求代理到 `http://127.0.0.1:8000`，因此本地浏览器调用不需要额外配置 CORS。

## 可选：Main Agent OpenAI 兼容 Provider

Main Agent 使用独立的 OpenAI 兼容 Chat Completions 配置，不复用 PLC worker simulation 的 `DEEPSEEK_*` 设置：

```text
MAIN_AGENT_PROVIDER=openai_compatible
MAIN_AGENT_API_KEY=...
MAIN_AGENT_BASE_URL=https://provider.example/v1
MAIN_AGENT_MODEL=provider-model
MAIN_AGENT_MAX_TURNS=20
MAIN_AGENT_TIMEOUT_SECONDS=120
MAIN_AGENT_STREAM=true
```

Provider 需要支持 Chat Completions `messages`、`tools`、`tool_calls`。Main Agent 请求不会发送 `response_format`。

## 可选：由 LLM 支持的 PLC MCP Server

Router 默认仍使用 mock worker 路径。若要通过 LLM 模拟的 PLC worker 验证真实 MCP 传输边界，请在 `.env` 中配置 PLC worker MCP server 和 DeepSeek worker 设置：

```text
MCP_MODE=real
PLC_WORKER_MCP_URL=http://localhost:9000/mcp
PLC_WORKER_TIMEOUT_SECONDS=300
PLC_WORKER_ARTIFACT_MAX_CHARS=12000

DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

在一个终端中启动 worker MCP server：

```bash
uv run python scripts/start_plc_worker_mcp_server.py
```

在另一个终端中检查工具发现：

```bash
uv run python scripts/dev_list_plc_mcp_tools.py
```

预期工具：

```text
plc_dev.run
plc_test.run
plc_formal.run
plc_repair.run
```

如需渐进式发布，请使用混合路由：

```text
MCP_MODE=hybrid
PLC_DEV_MODE=real
PLC_TEST_MODE=mock
PLC_FORMAL_MODE=mock
PLC_REPAIR_MODE=mock
```

需要显式传入 `--live` 才会执行 live worker smoke 调用：

```bash
uv run python scripts/dev_call_real_mcp_worker.py --worker plc-dev --live
uv run python scripts/dev_call_real_mcp_worker.py --worker plc-test --live
uv run python scripts/dev_call_real_mcp_worker.py --worker plc-formal --live
uv run python scripts/dev_call_real_mcp_worker.py --worker plc-repair --live
```

由 LLM 支持的 `plc-test` 和 `plc-formal` worker 是用于集成测试的模拟产物。它们不能替代真实的 PLC 测试执行或形式化验证；当真实 subagent 可用时，应在相同 MCP tools 后方替换这些实现。

## 验证 Artifact API

列出 seeded task 的 artifacts：

```bash
curl http://127.0.0.1:8000/api/tasks/task-001/artifacts
```

将 `<artifact_id>` 替换为 `scripts/dev_create_artifacts.py` 打印出的 ID，以读取具体 artifact：

```bash
curl http://127.0.0.1:8000/api/artifacts/<artifact_id>
```

预期行为：

- `GET /api/tasks/task-001/artifacts` 返回 artifact 元数据，不包含嵌入内容。
- `GET /api/artifacts/<artifact_id>` 返回元数据和 UTF-8 文本内容。
- 未知 artifact ID 返回 `404`。

## 检查 PostgreSQL

```bash
psql 'postgresql://router:router@localhost:5432/router' -c "select id, task_id, type, version, uri, content_hash from artifacts order by created_at, version;"
```

## 检查本地 Artifact 文件

```bash
find data/artifacts -maxdepth 5 -type f -print
```

## 重置本地状态

重置 Docker Compose 数据库：

```bash
docker compose down -v
docker compose up -d postgres
uv run alembic upgrade head
uv run python scripts/dev_create_artifacts.py
```

重置本地 artifact 文件：

```bash
rm -rf data/artifacts
mkdir -p data/artifacts
```

重启 WSL PostgreSQL 服务：

```bash
sudo service postgresql restart
pg_isready
```

## 故障排查

### 端口 5432 已被占用

检查正在监听的进程：

```bash
ss -ltnp | grep 5432 || true
```

停止冲突的本地 PostgreSQL 服务，或同时修改 `DATABASE_URL` 和 Docker Compose 的端口映射。

### Docker Desktop 无法从 WSL 访问

为你的发行版启用 Docker Desktop WSL 集成，或使用 WSL/手动 PostgreSQL 安装方式。

### Docker Hub 拉取失败

使用 WSL/手动 PostgreSQL 安装方式。后端只需要一个能通过 `DATABASE_URL` 访问的 PostgreSQL server；Docker 不是必需项。

### `router` 密码认证失败

重新运行本文档中的 WSL 用户和数据库创建命令，然后验证：

```bash
psql 'postgresql://router:router@localhost:5432/router' -c 'select current_user, current_database();'
```
