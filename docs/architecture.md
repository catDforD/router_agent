# 总体思路

> 当前状态：后端已经从早期的 OpenAI Agents SDK / structured final output 方案，切换为 OpenAI 兼容 Chat Completions tool loop。Main Agent 通过普通 `messages + tools + tool_calls` 运行，不依赖 `response_format`；前端通过 SSE 消费公开的 `agent.message`、`agent.tool_called`、`agent.tool_result` 等事件。

1. **方向**  
    ```
    Main Agent 负责理解、规划、调度、循环、追问和综合；plc-dev、plc-test、plc-formal、plc-repair 是外部 worker，不作为 Router 内部 handoff agent，而是通过 MCP tools 调用。
    ```

2. **目前分工设想**  
    - 前端：简要展示该项目的工作流程
    - 后端：Main Agent + 调度 + 状态
    - Subagent MCP：对接 plc-dev / plc-test / plc-formal / plc-repair

3. **整体架构**  
    ```
    ┌────────────────────────────────────────────────┐
    │                  Frontend                      │
    │  Chat / Task Timeline / Agent Cards / Artifacts│
    └─────────────────────┬──────────────────────────┘
                          │ HTTP + SSE/WebSocket
    ┌─────────────────────▼─────────────────────────┐
    │                Router Backend                 │
    │                                               │
    │  1. API Layer                                 │
    │     - create task                             │
    │     - send user message                       │
    │     - stream events                           │
    │     - fetch artifacts                         │
    │                                               │
    │  2. Main Agent Service                        │
    │     - OpenAI-compatible Chat Completions      │
    │     - tool loop                               │
    │     - instructions                            │
    │     - function tools                          │
    │     - public messages and tool events         │
    │                                               │
    │  3. Runtime / Scheduler Guard                 │
    │     - TaskState                               │ 
    │     - repair loop control                     │
    │     - max concurrency                         │
    │     - quality gate                            │
    │     - state transition validation             │
    │                                               │
    │  4. Artifact Store                            │
    │     - requirements_ir                         │
    │     - plc_code                                │
    │     - test_report                             │
    │     - formal_report                           │
    │     - counterexample                          │
    │     - patch                                   │
    │     - final_report                            │
    │                                               │
    │  5. MCP Tool Adapter                          │
    │     - schema validation                       │
    │     - timeout / retry                         │
    │     - error normalization                     │
    │     - trace mapping                           │
    └─────────────────────┬─────────────────────────┘
                          │ MCP client calls
    ┌─────────────────────▼─────────────────────────┐
    │              PLC Worker MCP Server            │
    │                                               │
    │  plc_dev.run                                  │
    │  plc_test.run                                 │
    │  plc_formal.run                               │
    │  plc_repair.run                               │
    └─────────────────────┬─────────────────────────┘
                          │ existing APIs / models / tools
    ┌─────────────────────▼─────────────────────────┐
    │              Existing Subagent Systems        │
    │                                               │
    │  智能开发 / 智能测试 / 形式化验证 / 智能修复     │
    └───────────────────────────────────────────────┘
    ```

4. **MCP Server**  
    只做一个 MCP Server，里面暴露四个 agent 的各种能力，如下：
    ```
    plc_worker_mcp_server
    ├── plc_dev.run
    ├── plc_test.run
    ├── plc_formal.run
    └── plc_repair.run

    一个 MCP Server
        → 四个 tool
        → 每个 tool 内部调用已有智能体 API
        → 统一写 artifact
        → 统一返回 WorkerResult
    ```
    目前先只要能正常启动 subagent 执行任务即可，也就是实现 run 功能。
    后续是肯定会将每一个 subagent 都拆成一个 mcp server 的，具体看每个 subagent 中的功能多不多。

5. **Main Agent 编排模式**  
    当前选择 Main Agent 自己掌控编排：模型每轮输出公开消息和 tool calls，后端执行工具、写事件和 artifact，再把 compact tool result 回灌给模型继续下一轮。外部 worker 仍然只是工具，不接管会话。

    ```
    Main Agent:
    tools = [
        list_files,
        read_file,
        write_file,
        apply_patch,
        exec_command,
        git_status,
        read_artifact,
        write_artifact,
        plc_dev,
        plc_test,
        plc_formal,
        plc_repair
    ]
    ```

6. **后端设计细节**  
    - **后端 API （供前端调用）**  
        接口规划：
        ```
        POST   /api/tasks 
        GET    /api/tasks/{task_id}
        POST   /api/tasks/{task_id}/messages
        GET    /api/tasks/{task_id}/events
        GET    /api/tasks/{task_id}/artifacts
        GET    /api/artifacts/{artifact_id}
        POST   /api/tasks/{task_id}/cancel
        ```
        解释：
        ```
        POST /api/tasks
        创建任务，保存用户原始输入，启动 Main Agent run

        GET /api/tasks/{task_id}/events
        SSE 或 WebSocket，用于前端展示调度过程

        GET /api/artifacts/{artifact_id}
        展示代码、测试报告、形式化验证报告、patch、final report
        ```
        这些接口都将提供给前端进行调用，前端只能走这些接口调用相关能力。

        前端集成时请以 [Frontend API Usage Guide](frontend-api.md) 为调用手册；
        该文档补充了任务创建、SSE 事件流、Artifact 读取、最终报告和 trace summary 的使用细节。
    - **后端实现（Main Agent Service）**  
        后端服务主要负责：
        ```
        1. 构造 OpenAI 兼容 Chat Completions 请求
        2. 注入 instructions
        3. 注册 function tool schemas
        4. 执行 tool loop
        5. 写入 main_agent.message / tool_called / tool_result 事件
        6. 在允许停止时由 runtime finalization 写最终报告 artifact
        7. 由 runtime lifecycle 做受控终态变更
        8. 把 trace_id / task_id / worker_job_id 关联起来
        ```
        Main Agent Instructions 示例：
        ```
        你是 PLC 超级智能体，负责根据用户任务调度外部 worker 完成 PLC 开发、测试、形式化验证和修复。

        你可以使用以下工具：
            - plc_dev：生成或修改 PLC 代码
            - plc_test：生成并执行测试，返回测试报告和失败轨迹
            - plc_formal：抽取并验证形式化性质，返回验证报告和反例
            - plc_repair：基于失败证据生成最小修复
            - read_artifact：读取必要 artifact 摘要或内容
            - runtime finalization：写最终报告 artifact 并执行受控终态变更

        调度规则：
            1. 需求不完整时，先追问用户。
            2. 简单解释类任务，不调用 worker 或只调用一个 worker。
            3. 新开发任务通常先调用 plc_dev。
            4. 中高复杂度任务，开发后必须调用 plc_test。
            5. 包含急停、互锁、故障锁存、模式互斥、状态机安全性质时，必须调用 plc_formal。
            6. 测试或形式化验证失败时，调用 plc_repair。
            7. 修复后必须重新测试；如果形式化验证曾失败，也必须重新形式化验证。
            8. 最多修复 3 轮。
            9. 不要把大段日志塞进最终回答，只引用 artifact。
            10. 最终回答必须包含代码、假设、测试结果、验证结果、修复说明和未解决问题。
        ```
    - **Runtime/Scheduler Guard**  
        Runtime 需要维护一个 TaskState：
        ```
        class TaskState(BaseModel):
            task_id: str
            session_id: str
            user_goal: str

            task_type: Literal[
                "qa",
                "new_plc_development",
                "modify_existing_code",
                "test_existing_code",
                "formal_verify_existing_code",
                "repair_existing_code"
            ]

            difficulty: Literal["L0", "L1", "L2", "L3", "L4"]

            status: Literal[
                "created",
                "planning",
                "waiting_user",
                "running",
                "repairing",
                "succeeded",
                "partial_failed",
                "failed",
                "cancelled"
            ]

            repair_rounds: int = 0
            max_repair_rounds: int = 3
            max_parallel_workers: int = 4

            current_code_artifact_id: str | None = None
            requirements_artifact_id: str | None = None
            latest_test_report_id: str | None = None
            latest_formal_report_id: str | None = None
            latest_patch_id: str | None = None

            has_test_run: bool = False
            has_formal_run: bool = False
            has_blocking_failure: bool = False
            unresolved_questions: list[str] = []
            assumptions: list[str] = []
        ```
        Scheduler Guard 负责实现一些规则：
        ```
        1. repair_rounds >= 3 时，不允许再次调用 repair。
        2. 没有代码 artifact 时，不允许调用 test / formal / repair。
        3. repair 后，必须重新调用 test。
        4. 如果 formal 曾经失败，repair 后必须重新 formal。
        5. L3/L4 且包含急停、互锁、故障锁存、模式互斥时，不能跳过 formal。
        6. 有 blocking failure 时，不允许 runtime finalization 标记为 succeeded。
        7. worker 返回 need_clarification 时，Main Agent 必须追问用户。
        ```
    - **Artifact Store**  
        Main Agent 是不可能在上下文里携带完整代码、完整测试日志、完整反例、这需要统一 artifact 化，Artifact schema 示例：
        ```
        class Artifact(BaseModel):
            artifact_id: str
            task_id: str
            type: Literal[
                "raw_user_request",
                "requirements_ir",
                "plc_code",
                "io_contract",
                "test_cases",
                "test_report",
                "failing_trace",
                "formal_properties",
                "formal_report",
                "counterexample",
                "patch",
                "repair_summary",
                "final_report"
            ]
            version: int
            parent_artifact_id: str | None = None
            created_by: Literal[
                "user",
                "main_agent",
                "plc_dev",
                "plc_test",
                "plc_formal",
                "plc_repair",
                "runtime"
            ]
            uri: str
            content_hash: str
            summary: str
            metadata: dict
            created_at: datetime
        ```
        数据存储方案：
        ```
        PostgreSQL：artifact metadata
        S3 / MinIO / 本地对象存储：artifact 内容
        Redis：运行中状态和事件，可选
        ```
    - **MCP Tool Adapter**  
        MCP Tool Adapter 是后端里连接外部 worker 的封装层。  
        统一 WorkerInput：  
        ```
        class ArtifactRef(BaseModel):
            artifact_id: str
            type: str
            version: int
            uri: str | None = None
            summary: str | None = None

        class WorkerInput(BaseModel):
            task_id: str
            worker_job_id: str
            objective: str

            input_artifacts: list[ArtifactRef]

            constraints: list[str] = []
            expected_outputs: list[str] = []

            context: dict = {}
            budget: dict = {
                "timeout_seconds": 300,
                "max_tokens": 16000,
                "max_iterations": 8
            }

            callback: dict | None = None
        ```
        统一 WorkerResult：  
        ```
        class Diagnostic(BaseModel):
            severity: Literal["info", "warning", "error", "blocking"]
            code: str
            message: str
            related_artifact_id: str | None = None

        class WorkerResult(BaseModel):
            status: Literal[
                "succeeded",
                "failed",
                "partial",
                "need_clarification",
                "timeout"
            ]

            summary: str
            produced_artifacts: list[ArtifactRef] = []
            diagnostics: list[Diagnostic] = []

            assumptions: list[str] = []
            failures: list[dict] = []
            metrics: dict = {}

            next_recommended_action: Literal[
                "finish",
                "test",
                "formal",
                "repair",
                "ask_user",
                "retry",
                "none"
            ] = "none"

        ```

7. **MCP tools 具体设计**  
    当前方案中计划 MCP Server 中暴露四个 tools：
    ```
    plc_dev.run
    plc_test.run
    plc_formal.run
    plc_repair.run
    ```

8. **Main Agent 的实际执行流程**
    - 新建 PLC 开发任务
        ```
        用户输入
        ↓
        Router Backend 创建 task
        ↓
        Main Agent 读取 task state
        ↓
        必要时生成 requirements_ir
        ↓
        Main Agent 调用 plc_dev
        ↓
        dev worker 生成 plc_code:v1
        ↓
        Main Agent 判断复杂度
        ↓
        并行调用：
        - plc_test
        - plc_formal
        ↓
        如果都通过：
        run_quality_gate
        runtime finalization
        ↓
        如果任一失败：
        plc_repair
        ↓
        生成 plc_code:v2
        ↓
        回归：
        - plc_test
        - 如果 formal 曾失败，plc_formal
        ↓
        最多 3 轮
        ↓
        最终交付
        ```
    - 已有代码修复任务
        ```
        用户上传已有代码 / 报错 / 失败场景
        ↓
        Main Agent 判断任务类型为 repair_existing_code
        ↓
        优先调用 plc_test 或 plc_formal 复现问题
        ↓
        获得 failure evidence
        ↓
        调用 plc_repair
        ↓
        回归测试
        ↓
        必要时形式化验证
        ↓
        最终输出 patch、修复说明、回归结果
        ```
    - 简单解释类任务
        ```
        用户问：这段 ST 代码是什么意思？
        ↓
        Main Agent 直接回答
        或只调用 plc_dev 做代码解释
        ↓
        不跑测试、不跑形式化、不跑修复
        ```

9. **前端设计需求**
    ```
    1. Chat Panel
        用户输入、Main Agent 解释、追问用户

    2. Execution Timeline
        展示 Main Agent 当前计划和每个 worker job 状态

    3. Agent Cards
        智能开发 / 智能测试 / 形式化验证 / 智能修复
        显示 idle / running / success / failed / skipped

    4. Artifact Panel
        代码
        I/O 表
        测试报告
        形式化验证报告
        反例
        patch
        最终报告
    ```
    事件流：
    ```
    {
      "event_id": "evt_001",
      "task_id": "task_001",
      "type": "worker_started",
      "timestamp": "2026-06-15T10:00:00Z",
      "payload": {
        "worker_type": "plc-dev",
        "worker_job_id": "job_dev_001",
        "summary": "智能开发开始生成 Structured Text 代码"
      }
    }
    ```

10. **分工建议**
- Backend / Main Agent / Runtime 负责
    ```
    1. FastAPI / Node 后端骨架
    2. TaskState / WorkerJob / Artifact / Event 数据模型
    3. OpenAI-compatible tool-loop Main Agent
    4. function tools 封装
    5. Scheduler Guard
    6. Quality Gate
    7. Trace Mapping
    ```
- MCP Worker / Subagent Adapter 负责
    ```
    1. 实现 plc_worker_mcp_server
    2. 暴露 plc_dev.run / plc_test.run / plc_formal.run / plc_repair.run
    3. 对接已有四个智能体接口
    4. 统一 WorkerInput / WorkerResult schema
    5. timeout、错误归一化、artifact 写入
    6. mock worker 和真实 worker 切换
    ```
- Frontend / Product Flow / Eval 负责
    ```
    1. 超级智能体入口页面
    2. 聊天输入
    3. 执行 timeline
    4. 四个 agent card 状态展示
    5. artifact viewer
    6. SSE / WebSocket 事件接入
    7. 准备 20-30 个 PLC MVP 测试任务
    ```

11. 技术栈建议
    ```
    Backend:
    - Python + FastAPI
    - OpenAI-compatible Chat Completions provider
    - PostgreSQL
    - Redis，可选，用于事件和运行中状态
    - SQLAlchemy / SQLModel
    - Pydantic

    MCP:
    - Python MCP SDK
    - 一个 plc_worker_mcp_server
    - 内部 adapter 调已有智能体接口

    Frontend:
    - React / Next.js
    - SSE 或 WebSocket
    - Monaco Editor 展示代码
    - Markdown viewer 展示报告
    - Timeline component 展示执行过程

    Storage:
    - MVP：本地文件 + PostgreSQL metadata
    - 后续：S3 / MinIO + PostgreSQL metadata
    ```

12. Schemas 设计
    均放在 schema 路径下供查看：
    ```
    TaskState      = Main Agent 和 Runtime 的当前事实视图
    WorkerInput    = Runtime 调 MCP worker 的标准输入
    WorkerResult   = MCP worker 返回 Runtime 的标准输出
    Artifact       = 所有中间产物和交付物的不可变存储对象
    RouterEvent    = 前端展示、调试、回放、trace 的事件流
    ```
    项目中部分文件：
    ```
    TypeScript interface（schema/ts/router_contract.d.ts）
    = 人类可读的结构设计
    = 给前端/后端/MCP worker 对齐字段用

    Python Pydantic（backend/app/models/router_schema.py）
    = 后端真正使用的类型定义和校验逻辑

    JSON Schema（schema）
    = 语言无关的正式契约文件
    = 放在 schema/ 目录
    = 前端、后端、MCP tool 都可以引用
    ```
