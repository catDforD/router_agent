# Router Agent

Router Agent 是一个面向 PLC 工程任务的 Agent 工作台。用户从前端提交任务后，后端 Main Agent 会规划步骤、调用 PLC worker、运行质量门禁、写入最终报告，并通过 SSE 把公开执行过程推送到前端。

当前主链路使用 OpenAI 兼容 Chat Completions tool calling，不依赖 OpenAI Responses API，也不使用 `response_format`。

## 项目结构

```text
backend/                 FastAPI 后端、Runtime、Main Agent、MCP adapter
frontend/                Vite + React 前端工作台
schema/                  Router v1 JSON Schema 和 TypeScript contract
docs/                    设计、启动、前端 API 和测试说明
scripts/                 本地开发和 MCP worker 辅助脚本
openspec/                需求变更和规格记录
```

## 前置条件

- Python 环境管理：`uv`
- Node.js / npm
- PostgreSQL，或 Docker Compose

## 环境配置

复制环境文件：

```bash
cp .env.example .env
```

常用配置项：

```text
DATABASE_URL=postgresql+psycopg://router:router@localhost:5432/router
ARTIFACT_ROOT=./data/artifacts

MAIN_AGENT_PROVIDER=openai_compatible
MAIN_AGENT_API_KEY=...
MAIN_AGENT_BASE_URL=...
MAIN_AGENT_MODEL=...
MAIN_AGENT_STREAM=false

MCP_MODE=mock
MOCK_SCENARIO=dev_test_pass
```

如果要跑真实 MCP worker，把 `.env` 改为：

```text
MCP_MODE=real
PLC_WORKER_MCP_URL=http://localhost:9000/mcp

DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=...
DEEPSEEK_MODEL=...
```

不要提交 `.env`、密钥或 `data/` 内容。

## 初始化数据库

使用 Docker Compose PostgreSQL：

```bash
docker compose up -d postgres
bash scripts/dev_setup_db.sh
```

如果本机已经有 PostgreSQL，只需保证 `.env` 中的 `DATABASE_URL` 可连接，然后运行：

```bash
bash scripts/dev_setup_db.sh
```

## 启动开发环境

推荐使用一键启动器：

```bash
set -a
source .env
set +a

uv run main.py --install-frontend-deps
```

启动后访问：

```text
Frontend  http://127.0.0.1:5173
Backend   http://127.0.0.1:8000
OpenAPI   http://127.0.0.1:8000/docs
```

如果不希望启动器管理 Docker PostgreSQL：

```bash
uv run main.py --no-postgres
```

## 手动启动

```bash
# terminal 1: backend
set -a; source .env; set +a
PYTHONPATH=backend uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

```bash
# terminal 2: frontend
cd frontend
npm install
npm run dev
```

真实 MCP worker 模式还需要：

```bash
# terminal 3: PLC worker MCP server
set -a; source .env; set +a
PYTHONPATH=backend uv run python scripts/start_plc_worker_mcp_server.py
```

## 真实联调

打开前端：

```text
http://127.0.0.1:5173
```

提交任务示例：

```text
帮我写一个电机启停控制逻辑，包含启动、停止和运行指示。
```

前端应看到：

- Main Agent 公开消息
- `update_plan`
- `call_plc_dev` / `call_plc_test` 等工具步骤
- `run_quality_gate`
- `write_final_report`
- `finish_task`
- 最终报告和 artifacts

## 测试

后端核心测试：

```bash
uv run pytest backend/app/tests/unit/test_agent_tools.py backend/app/tests/unit/test_main_agent_service.py -q
```

mock 场景 E2E：

```bash
uv run pytest backend/app/tests/e2e/test_router_mock_scenarios.py -q
```

评测集：

```bash
uv run pytest backend/app/tests/eval/test_eval_tasks.py -q
```

前端构建检查：

```bash
cd frontend
npm run build
```

## 常用文档

- [本地开发环境](docs/local-dev.md)
- [前端 API 使用指南](docs/frontend-api.md)
- [总体架构](docs/architecture.md)
- [后端测试指南](docs/backend_test.md)
