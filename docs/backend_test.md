# Backend Test Guide

## 后端评测集

默认后端评测集使用固定 PLC 任务集和 mock worker，不需要真实 OpenAI、DeepSeek 或 MCP 服务。

运行：

```bash
uv run pytest backend/app/tests/eval/test_eval_tasks.py -q
```

任务定义：

```text
backend/app/tests/eval/plc_tasks.yaml
```

输出：

```text
eval_report.md
```

可通过环境变量改写报告路径：

```bash
ROUTER_EVAL_REPORT_PATH=/tmp/router_eval_report.md uv run pytest backend/app/tests/eval/test_eval_tasks.py -q
```

默认 live provider 评测不会运行。如需后续启用真实 provider 评测，使用显式环境开关：

```bash
ROUTER_LIVE_EVAL=1 uv run pytest backend/app/tests/eval/test_eval_tasks.py -q
```

## Event Service 和 SSE 真实测试

本节用于真实验证 `GET /api/tasks/{task_id}/events` 是否能通过 SSE 实时输出 Router 事件。适用于 WSL 本地已经安装 PostgreSQL 的开发环境。

### 目标

验证以下行为：

- 后端能连接本地 PostgreSQL。
- Alembic migration 和本地 seed 数据能正常创建 `task-001`。
- SSE 连接能保持打开。
- 运行事件发射脚本后，`curl -N` 能实时看到 `worker.started`、`artifact.created`、`worker.completed`。
- `Last-Event-ID` 或 `after_seq` 能从指定事件序号后继续读取。

### 1. 确认本地 PostgreSQL 可用

在仓库根目录执行：

```bash
cp .env.example .env
sudo service postgresql start
```

确认默认连接串可用：

```bash
psql 'postgresql://router:router@localhost:5432/router' -c 'select current_user, current_database();'
```

期望能看到 `router | router`。如果连接失败，先创建本地用户和数据库：

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

然后再次运行连接检查。

### 2. 初始化后端数据

```bash
bash scripts/dev_setup_db.sh
```

该脚本会：

- 加载 `.env`。
- 执行 `uv sync`。
- 检查 PostgreSQL 连接。
- 执行 `uv run alembic upgrade head`。
- 创建本地 artifact 目录和代表性数据。

成功后会创建可用于测试的 `task-001`。

### 3. 启动 API

打开第一个终端：

```bash
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

可先检查健康接口：

```bash
curl http://127.0.0.1:8000/api/health
```

### 4. 打开 SSE 连接

打开第二个终端：

```bash
curl -N http://127.0.0.1:8000/api/tasks/task-001/events
```

保持该终端不要关闭。没有新事件时，可能会看到 heartbeat：

```text
: keepalive
```

### 5. 发射测试事件

打开第三个终端：

```bash
uv run python scripts/dev_emit_events.py --task-id task-001
```

第二个终端应实时看到类似输出：

```text
id: 1
event: worker.started
data: {...}

id: 2
event: artifact.created
data: {...}

id: 3
event: worker.completed
data: {...}
```

如果当前数据库中已经有事件，`id` 可能不是从 `1` 开始；只要序号递增、事件类型正确即可。

### 6. 测试断线续传

使用 `Last-Event-ID`：

```bash
curl -N -H "Last-Event-ID: 2" http://127.0.0.1:8000/api/tasks/task-001/events
```

或使用显式 query cursor：

```bash
curl -N "http://127.0.0.1:8000/api/tasks/task-001/events?after_seq=2"
```

期望只返回 `seq > 2` 且 `visibility=user` 的事件。

### 7. 验收标准

- `curl -N` 连接不会立即退出。
- 发射脚本运行后，SSE 终端不需要刷新即可看到事件。
- 每个事件包含：
  - `id: <seq>`
  - `event: <router_event.type>`
  - `data: <RouterEvent JSON>`
- 默认不会输出 `visibility=internal` 的事件。
- `Last-Event-ID` 和 `after_seq` 都能从指定序号后恢复读取。

### 8. 常见问题

如果 `psql` 连接失败，优先检查：

```bash
sudo service postgresql status
psql 'postgresql://router:router@localhost:5432/router' -c 'select 1;'
```

如果 `curl -N` 返回 `404`，说明 `task-001` 未创建，重新运行：

```bash
bash scripts/dev_setup_db.sh
```

如果 `curl -N` 没有看到新事件，确认事件脚本使用了相同的 `DATABASE_URL`：

```bash
set -a
source .env
set +a
uv run python scripts/dev_emit_events.py --task-id task-001
```
