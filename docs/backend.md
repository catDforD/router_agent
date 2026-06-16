# 后端开发计划
1. **开发总原则**
    ```
    先 mock worker 跑通：
    创建任务 → Main Agent/Runtime 决策 → 调用 mock worker → 写 Artifact → 写 Event → 前端可看

    再接真实 worker：
    mock plc-dev → 真实 plc-dev
    mock plc-test → 真实 plc-test
    mock plc-formal → 真实 plc-formal
    mock plc-repair → 真实 plc-repair

    最后稳定：
    质量门禁 → 修复循环 → 超时取消 → Trace → 评测集
    ```
    规则：
    ```
    1. Main Agent 可以自由决策，但 Runtime 必须二次校验。
    2. 大内容一律 Artifact 化，不塞进 TaskState 或 Main Agent 上下文。
    3. WorkerInput / WorkerResult / Artifact / RouterEvent 必须通过 schema 校验。
    4. 每一步都要能从 events + artifacts + worker_jobs 回放。
    ```

2. **后端目录结构**
    ```
    backend/
        app/
            main.py
            api/
                tasks.py
                artifacts.py
                events.py
                health.py
            core/
                config.py
                logging.py
                ids.py
                time.py
                errors.py
            models/
                router_schema.py          # Pydantic 版五大 schema
                db_models.py              # SQLAlchemy / SQLModel 表模型
            schemas/
                json_schema_export.py     # 导出 JSON Schema
            repositories/
                task_repo.py
                artifact_repo.py
                event_repo.py
                worker_job_repo.py
                gate_repo.py
            services/
                task_service.py
                artifact_store.py
                event_service.py
                runtime_service.py
                quality_gate.py
                scheduler_guard.py
            agents/
                main_agent.py
                instructions.py
                tools.py
                output_schema.py
            mcp/
                adapter.py
                mock_worker.py
                client.py
                normalizer.py
            workers/
                worker_input_builder.py
                worker_result_handler.py
            tests/
                unit/
                integration/
                e2e/
                fixtures/
    ```
    技术栈：
    ```
    FastAPI
    Pydantic
    SQLAlchemy / SQLModel
    PostgreSQL
    本地文件 Artifact Store
    OpenAI Agents SDK
    pytest
    ```

3. **初始化后端工程**  
    目标：
    ```
    搭建最小 FastAPI 后端，可以启动、健康检查、加载配置、打日志。
    ```
    Todo：
    ```
    1. 创建后端项目。
    2. 配置依赖。
    3. 建立配置文件。
    4. 建立统一日志。
    5. 建立健康检查接口。
    ```
    建议环境变量：
    ```
    APP_ENV=local
    DATABASE_URL=postgresql+psycopg://router:router@localhost:5432/router
    ARTIFACT_ROOT=./data/artifacts
    OPENAI_API_KEY=...
    MCP_MODE=mock
    ```
    接口：
    ```
    GET /health
    GET /api/health
    ```
    检查方式：
    ```
    uvicorn app.main:app --reload
    curl http://localhost:8000/health
    ```
    期望返回：
    ```
    {
      "status": "ok",
      "app": "router-backend",
      "env": "local"
    }
    ```
    真实测试：
    ```
    服务启动后，断开数据库也要能访问健康检查。
    数据库相关检查放到 GET /api/health/dependencies。
    ```

4. **落地五大 Schema**  
    目标：
    ```
    把已确定的五大 schema 落成后端真正使用的 Pydantic 类型。
    ```
    Todo：
    ```
    实现 backend/app/models/router_schema.py：
    TaskState
    WorkerInput
    WorkerResult
    Artifact
    RouterEvent
    ArtifactRef
    Assumption
    ClarificationQuestion
    Failure
    Diagnostic
    TraceContext
    ```
    JSON Schema 导出：
    ```
    python -m app.schemas.json_schema_export
    ```
    导出路径：
    ```
    schema/task_state.schema.json
    schema/worker_input.schema.json
    schema/worker_result.schema.json
    schema/artifact.schema.json
    schema/router_event.schema.json
    ```
    检查方式：
    ```
    pytest tests/unit/test_router_schema.py -q
    pytest tests/unit/test_schema_fixtures.py -q
    ```
    必须覆盖：
    ```
    1. 合法 TaskState 可以创建。
    2. 非法 schema_version 会失败。
    3. WorkerInput 缺 task_id 会失败。
    4. WorkerResult.execution_status 和 outcome.status 可以区分。
    5. Artifact 大内容不要求 inline_content。
    6. RouterEvent seq 必须是整数。
    7. 五个 JSON fixture 都能被 Pydantic 解析。
    ```
    Fixture：
    ```
    tests/fixtures/task_state.valid.json
    tests/fixtures/worker_input.plc_dev.valid.json
    tests/fixtures/worker_result.test_failed.valid.json
    tests/fixtures/artifact.plc_code.valid.json
    tests/fixtures/event.worker_started.valid.json
    ```
    验收标准：
    ```
    五大 schema 全部能校验。
    JSON Schema 可以导出。
    TypeScript contract、Pydantic schema、JSON Schema 字段没有明显偏差。
    ```

5. **数据库表和迁移**  
    目标：
    ```
    把 Task、Artifact、Event、WorkerJob、GateResult 持久化。
    ```
    Todo：
    ```
    实现表：
    tasks
    artifacts
    events
    worker_jobs
    gate_results
    ```
    建议字段：
    ```
    tasks:
      id, session_id, user_id, status, phase, task_type, difficulty_level,
      state_json, created_at, updated_at, completed_at

    artifacts:
      id, task_id, type, version, status, visibility, uri, content_hash,
      summary, artifact_json, created_at, updated_at

    events:
      id, task_id, seq, type, severity, visibility, event_json, created_at

    worker_jobs:
      id, task_id, worker_type, status, input_json, result_json,
      started_at, completed_at

    gate_results:
      id, task_id, gate_type, status, blocking, evidence_artifact_ids,
      result_json, created_at
    ```
    检查方式：
    ```
    alembic upgrade head
    pytest tests/unit/test_repositories.py -q
    ```
    必须覆盖：
    ```
    1. create_task 后能读取 TaskState。
    2. append_event 自动递增 seq。
    3. create_artifact 后不能覆盖同一 artifact_id。
    4. worker_job 可以从 running 更新为 completed。
    5. task state_json 可以完整保存和恢复。
    ```
    真实测试：
    ```
    python scripts/dev_seed_task.py

    select id, status, phase, task_type from tasks;
    select task_id, seq, type from events order by seq;
    ```
    验收标准：
    ```
    任务、事件、artifact、worker job 都能落库。
    Event 在同一 task 内 seq 单调递增。
    Artifact 不直接覆盖旧版本。
    ```

6. **Artifact Store**  
    目标：
    ```
    实现本地 Artifact Store。
    ```
    Todo：
    ```
    实现 backend/app/services/artifact_store.py：
    write_artifact_content(task_id, artifact_type, version, content, metadata)
    read_artifact_content(artifact_id)
    create_artifact_record(...)
    get_artifact_ref(...)
    list_task_artifacts(task_id)
    ```
    本地路径建议：
    ```
    data/artifacts/
      task_001/
        requirements_ir_v1.json
        plc_code_v1.st
        test_report_v1.md
        formal_report_v1.md
        counterexample_p2.json
        patch_v1.diff
        final_report_v1.md
    ```
    检查方式：
    ```
    pytest tests/unit/test_artifact_store.py -q
    ```
    必须覆盖：
    ```
    1. 写入内容后能读取。
    2. content_hash 正确。
    3. 同一 artifact 新版本不会覆盖旧版本。
    4. artifact metadata 写入数据库。
    5. 大内容不写 inline_content。
    ```
    真实测试：
    ```
    python scripts/dev_create_artifacts.py
    curl http://localhost:8000/api/tasks/{task_id}/artifacts
    curl http://localhost:8000/api/artifacts/{artifact_id}
    ```
    验收标准：
    ```
    Artifact 列表能查。
    Artifact 内容能读。
    ArtifactRef 能被 WorkerInput 使用。
    ```

7. **Event Service 和 SSE**  
    目标：
    ```
    前端可以实时看到任务过程。
    ```
    Todo：
    ```
    实现 backend/app/services/event_service.py
    实现 backend/app/api/events.py
    ```
    接口：
    ```
    GET /api/tasks/{task_id}/events
    Content-Type: text/event-stream
    ```
    规则：
    ```
    事件必须 append-only。
    visibility=internal 的事件默认不返回给前端。
    ```
    检查方式：
    ```
    pytest tests/unit/test_event_service.py -q
    ```
    必须覆盖：
    ```
    1. append_event 生成 seq。
    2. 同一 task 下 seq 递增。
    3. visibility=user 的事件可以被前端读取。
    4. visibility=internal 的事件默认不返回给前端。
    ```
    真实测试：
    ```
    curl -N http://localhost:8000/api/tasks/{task_id}/events
    python scripts/dev_emit_events.py --task-id {task_id}
    ```
    期望事件：
    ```
    worker.started
    artifact.created
    worker.completed
    ```
    验收标准：
    ```
    前端不用轮询，也能看到 worker_started、artifact_created、worker_completed。
    ```

8. **Task API**  
    目标：
    ```
    前端可以创建任务、查询任务、追加用户消息、取消任务。
    ```
    Todo：
    ```
    实现 backend/app/api/tasks.py：
    POST   /api/tasks
    GET    /api/tasks/{task_id}
    POST   /api/tasks/{task_id}/messages
    POST   /api/tasks/{task_id}/cancel
    ```
    POST /api/tasks 请求：
    ```
    {
      "message": "帮我实现一个带急停、故障锁存、自动/手动切换的输送线控制逻辑",
      "project_context": {
        "target_plc_language": "ST",
        "target_platform": "Codesys"
      }
    }
    ```
    返回：
    ```
    {
      "task_id": "task_xxx",
      "status": "created",
      "events_url": "/api/tasks/task_xxx/events"
    }
    ```
    检查方式：
    ```
    pytest tests/integration/test_task_api.py -q
    ```
    必须覆盖：
    ```
    1. POST /api/tasks 创建 TaskState。
    2. 创建 raw_user_request artifact。
    3. 生成 task.created event。
    4. GET /api/tasks/{id} 返回当前 TaskState。
    5. cancel 可以把 running task 置为 cancelled。
    ```
    真实测试：
    ```
    curl -X POST http://localhost:8000/api/tasks \
      -H "Content-Type: application/json" \
      -d '{
        "message": "帮我实现一个带急停和故障锁存的电机启停逻辑",
        "project_context": {
          "target_plc_language": "ST",
          "target_platform": "Codesys"
        }
      }'
    ```
    验收标准：
    ```
    数据库出现 task。
    Artifact Store 出现 raw_user_request。
    Event Stream 出现 task.created。
    ```

9. **TaskState 初始化和难度初判**  
    目标：
    ```
    用户创建任务后，后端可以初始化一个可持久化、可回放的保守 TaskState。
    当前阶段不做真实语义难度判断；难度初判留给后续 Main Agent intake/classification 逻辑。
    ```
    Todo：
    ```
    实现 backend/app/services/task_service.py。
    只预留 TaskState.task_type、difficulty、gates 等字段，不实现临时关键词分类器。
    ```
    初始化内容：
    ```
    schema_version = router.v1
    status = created
    phase = intake
    task_type = unknown
    difficulty.level = L0
    difficulty.confidence = 低置信度
    difficulty.reasons 说明尚未经过 agent 分类
    difficulty.requires_test = false
    difficulty.requires_formal = false
    difficulty.requires_repair_loop = false
    runtime_limits.max_repair_rounds = 3
    runtime_limits.max_parallel_workers = 4
    gates.test_required = false
    gates.formal_required = false
    current_artifacts.raw_user_request
    ```
    后续 Main Agent 接入后再实现的判断行为：
    ```
    包含“解释 / 分析 / 看一下” → qa 或 analyze  ，L0/L1
    包含“生成 / 实现 / 开发 / 写一个” → new_plc_development， L2
    包含“修复 / 报错 / 不通过 / bug” → repair_existing_code L3
    包含“急停 / 互锁 / 故障锁存 / 模式切换 / 状态机” → L3/L4，requires_formal=true
    包含“测试 / 仿真 / 用例” → requires_test=true, L2
    ```
    检查方式：
    ```
    pytest backend/app/tests/unit/test_task_service.py -q
    pytest backend/app/tests/unit/test_task_api.py -q
    ```
    当前必须覆盖：
    ```
    1. create_task 后 TaskState 为 created/intake。
    2. create_task 后 task_type = unknown，difficulty.level = L0，且置信度低。
    3. create_task 后 test/formal/repair gate 都不要求。
    4. runtime_limits 使用默认上限。
    5. raw_user_request artifact 已写入，并同步到 current_artifacts.raw_user_request。
    6. task.created event 已写入，GET /api/tasks/{id} 可以返回当前 TaskState。
    ```
    延后到 Main Agent 接入后覆盖：
    ```
    简单解释任务 → L0/L1，不要求 test/formal。
    普通电机启停 → L2，要求 test。
    带急停故障锁存 → L3，要求 test + formal。
    已有代码报错 → repair_existing_code。
    ```
    当前真实测试：
    ```
    创建 1 个任务：
    1. 写一个带急停和互锁的输送线逻辑

    curl http://localhost:8000/api/tasks/{task_id}
    ```
    验收标准：
    ```
    TaskState.task_type = unknown。
    TaskState.difficulty.level = L0，且 reasons 说明尚未经过 agent 分类。
    gates.test_required = false。
    gates.formal_required = false。
    raw_user_request artifact 和 task.created event 可查。
    ```

10. **Scheduler Guard**  
    目标：
    ```
    把关键调度规则写成确定性代码，不只靠 Main Agent prompt。
    ```
    Todo：
    ```
    实现 backend/app/services/scheduler_guard.py：
    validate_worker_call(state, worker_type, input_artifacts)
    validate_finish_task(state, final_status)
    validate_parallel_jobs(state, jobs)
    validate_repair_allowed(state)
    ```
    强制规则：
    ```
    1. 没有 current_code，不能调用 plc-test / plc-formal / plc-repair。
    2. 没有 open blocking failure，不能调用 plc-repair。
    3. repair_rounds >= 3，不能继续 repair。
    4. active_parallel_workers 不能超过 4。
    5. repair 后必须 regression_required=true。
    6. formal 曾失败后 repair，必须 formal_regression_required=true。
    7. 有 blocking failure，不能 succeeded。
    ```
    检查方式：
    ```
    pytest tests/unit/test_scheduler_guard.py -q
    ```
    必须覆盖：
    ```
    test before dev 被拒绝。
    formal before dev 被拒绝。
    repair before failure 被拒绝。
    第 4 轮 repair 被拒绝。
    blocking failure 下 finish succeeded 被拒绝。
    L3 任务跳过 formal finish 被拒绝。
    ```
    真实测试：
    ```
    python scripts/dev_guard_check.py
    ```
    期望输出：
    ```
    PASS: test without code rejected
    PASS: repair without failure rejected
    PASS: finish with blocking failure rejected
    ```
    验收标准：
    ```
    即使 Main Agent 决策错误，Runtime 也不会执行非法动作。
    ```

11. **Quality Gate**  
    目标：
    ```
    最终交付前，统一检查当前 TaskState 是否满足交付条件。
    ```
    Todo：
    ```
    实现 backend/app/services/quality_gate.py。
    返回 gate_report artifact 和 gate_results 记录。
    ```
    Gate 类型：
    ```
    requirements_gate
    code_gate
    test_gate
    formal_gate
    regression_gate
    final_gate
    ```
    检查方式：
    ```
    pytest tests/unit/test_quality_gate.py -q
    ```
    必须覆盖：
    ```
    1. L1 qa 可以不测试。
    2. L2 new_plc_development 没 test_report 不能 success。
    3. L3 带急停没有 formal_report 不能 success。
    4. 有 open blocking failure 不能 success。
    5. repair 后没有 regression test 不能 success。
    ```
    真实测试：
    ```
    python scripts/dev_run_gate.py --fixture task_l3_no_formal.json
    ```
    期望：
    ```
    {
      "status": "failed",
      "blocking": true,
      "message": "L3 task requires formal verification report before success."
    }
    ```
    验收标准：
    ```
    finish_task 之前必须经过 Quality Gate。
    ```

12. **MCP Adapter 和 Mock Worker**  
    目标：
    ```
    不依赖真实子智能体，先跑通完整 worker 调用协议。
    ```
    Todo：
    ```
    实现 backend/app/mcp/adapter.py
    实现 backend/app/mcp/mock_worker.py
    实现 backend/app/mcp/normalizer.py
    ```
    Mock 配置：
    ```
    MCP_MODE=mock

    MOCK_SCENARIO=dev_test_pass
    MOCK_SCENARIO=test_failed_then_repair_pass
    MOCK_SCENARIO=formal_failed_then_repair_pass
    MOCK_SCENARIO=need_clarification
    MOCK_SCENARIO=worker_timeout
    ```
    规则：
    ```
    每个 mock worker 都必须返回标准 WorkerResult。
    ```
    检查方式：
    ```
    pytest tests/unit/test_mcp_adapter_mock.py -q
    ```
    必须覆盖：
    ```
    1. plc-dev 返回 plc_code artifact。
    2. plc-test pass 返回 test_report。
    3. plc-test failed 返回 failing_trace + Failure。
    4. plc-formal failed 返回 counterexample + Failure。
    5. plc-repair 返回 patch + plc_code:v2。
    6. timeout 被归一化为 execution_status=timeout。
    ```
    真实测试：
    ```
    python scripts/dev_call_mock_worker.py --worker plc-dev
    python scripts/dev_call_mock_worker.py --worker plc-test --scenario test_failed
    python scripts/dev_call_mock_worker.py --worker plc-formal --scenario formal_failed
    python scripts/dev_call_mock_worker.py --worker plc-repair
    ```
    验收标准：
    ```
    每个 mock worker 都能接收 WorkerInput、返回 WorkerResult、写 produced_artifacts，
    并生成 worker.started / worker.completed / artifact.created events。
    ```

13. **WorkerResult Handler**  
    目标：
    ```
    worker 返回后，Runtime 能正确更新 TaskState。
    ```
    Todo：
    ```
    实现 backend/app/workers/worker_result_handler.py。
    ```
    状态更新规则：
    ```
    plc-dev completed/passed:
      current_code = plc_code:v1
      current_io_contract = io_contract:v1
      phase 可进入 testing 或 formal_verifying

    plc-test completed/passed:
      latest_test_report = test_report
      gates.latest_test_passed = true
      gates.regression_required = false

    plc-test completed/failed:
      latest_test_report = test_report
      failures 增加 open blocking failure
      gates.has_blocking_failure = true

    plc-formal completed/passed:
      latest_formal_report = formal_report
      gates.latest_formal_passed = true
      gates.formal_regression_required = false

    plc-formal completed/failed:
      latest_formal_report = formal_report
      latest_counterexample = counterexample
      failures 增加 open blocking failure
      gates.has_blocking_failure = true

    plc-repair completed/passed:
      latest_patch = patch
      current_code = patched plc_code
      repair_rounds += 1
      gates.regression_required = true
      如果 formal 曾失败：formal_regression_required = true
    ```
    检查方式：
    ```
    pytest tests/unit/test_worker_result_handler.py -q
    ```
    必须覆盖：
    ```
    1. dev result 更新 current_code。
    2. test failed 增加 failure。
    3. formal failed 增加 counterexample。
    4. repair result 生成 current_code:v2。
    5. repair 后设置 regression_required。
    6. repair_rounds 正确递增。
    ```
    真实测试：
    ```
    python scripts/dev_worker_result_chain.py --scenario formal_failed_then_repair_pass
    ```
    验收标准：
    ```
    TaskState 永远反映当前最新代码、最新测试结果、最新验证结果和开放失败。
    ```

14. **Main Agent Function Tools**  
    目标：
    ```
    给 OpenAI Agents SDK 的 Main Agent 暴露后端 function tools。
    ```
    Todo：
    ```
    实现 backend/app/agents/tools.py。
    ```
    工具：
    ```
    call_plc_dev
    call_plc_test
    call_plc_formal
    call_plc_repair
    run_parallel_workers
    read_artifact
    run_quality_gate
    finish_task
    ```
    工具内部统一流程：
    ```
    validate_worker_call
    build_worker_input
    create_worker_job
    emit worker.started
    call_mcp_adapter
    handle_worker_result
    emit worker.completed / worker.error
    return summarized WorkerResult to Main Agent
    ```
    返回给 Main Agent 的内容：
    ```
    status
    summary
    artifact_refs
    failures summary
    next_recommended_action
    ```
    约束：
    ```
    不要把完整测试日志或完整代码返回给 Main Agent。
    ```
    检查方式：
    ```
    pytest tests/unit/test_agent_tools.py -q
    ```
    必须覆盖：
    ```
    1. call_plc_dev 能调用 mock worker。
    2. call_plc_test 在没有 current_code 时被 guard 拒绝。
    3. call_plc_repair 在没有 failure 时被 guard 拒绝。
    4. finish_task 有 blocking failure 时被拒绝。
    5. read_artifact 支持 summary / full 两种模式。
    ```
    真实测试：
    ```
    python scripts/dev_call_agent_tool.py --tool call_plc_dev
    ```
    验收标准：
    ```
    不用 Main Agent，也能单独调试每个 tool。
    ```

15. **Main Agent Service**  
    目标：
    ```
    接入 OpenAI Agents SDK，Main Agent 可以根据 TaskState 调用 function tools。
    ```
    Todo：
    ```
    实现 backend/app/agents/main_agent.py
    实现 backend/app/agents/instructions.py
    实现 backend/app/agents/output_schema.py
    ```
    Main Agent 输入使用压缩视图：
    ```
    state_view = {
      "task_id": "...",
      "user_goal": "...",
      "task_type": "...",
      "difficulty": "...",
      "gates": "...",
      "current_artifacts": "...",
      "open_failures": "...",
      "repair_rounds": "0/3",
      "available_tools": [...]
    }
    ```
    Main Agent instructions：
    ```
    1. 需求不完整先追问。
    2. 新开发通常先 call_plc_dev。
    3. L2+ 必须 test。
    4. L3/L4 且有急停/互锁/故障锁存/模式切换必须 formal。
    5. 测试或形式化失败后 repair。
    6. repair 后必须 regression。
    7. 最多 repair 3 轮。
    8. 最终必须 run_quality_gate 再 finish_task。
    ```
    检查方式：
    ```
    pytest tests/integration/test_main_agent_with_mock_tools.py -q
    ```
    必须覆盖：
    ```
    1. 简单 qa 不调用 worker。
    2. 普通开发调用 dev + test。
    3. L3 任务调用 dev + test + formal。
    4. test failed 后调用 repair + regression test。
    5. formal failed 后调用 repair + regression test + formal regression。
    ```
    真实测试：
    ```
    MCP_MODE=mock
    MOCK_SCENARIO=dev_test_pass

    curl -X POST http://localhost:8000/api/tasks \
      -H "Content-Type: application/json" \
      -d '{
        "message": "帮我写一个电机启停控制逻辑，包含启动、停止和运行指示。",
        "project_context": {
          "target_plc_language": "ST",
          "target_platform": "Codesys"
        }
      }'

    curl -N http://localhost:8000/api/tasks/{task_id}/events
    ```
    期望事件：
    ```
    task.created
    main_agent.started
    main_agent.decision
    worker.started plc-dev
    artifact.created plc_code
    worker.completed plc-dev
    worker.started plc-test
    artifact.created test_report
    worker.completed plc-test
    gate.started
    gate.passed
    task.succeeded
    ```
    验收标准：
    ```
    Main Agent 能真实跑完一条 mock happy path。
    ```

16. **Runtime Loop 和后台执行**  
    目标：
    ```
    POST /api/tasks 创建任务后，不阻塞 HTTP 请求，后台执行 Main Agent 流程。
    ```
    Todo：
    ```
    MVP 可以先用 FastAPI BackgroundTasks 或 asyncio task。
    如果任务耗时明显，再上 Celery / Dramatiq。

    实现：
    runtime_service.start_task(task_id)
    runtime_service.run_main_agent_episode(task_id)
    runtime_service.resume_after_user_message(task_id)
    ```
    Runtime 限制：
    ```
    max_agent_turns_per_episode = 20
    max_worker_calls_per_task = 20
    max_repair_rounds = 3
    ```
    检查方式：
    ```
    pytest tests/integration/test_runtime_loop.py -q
    ```
    必须覆盖：
    ```
    1. POST /api/tasks 快速返回。
    2. 后台任务继续执行。
    3. 任务执行中可以 GET TaskState。
    4. 任务完成后 status=succeeded。
    5. cancel 后后续 worker 不再启动。
    ```
    真实测试：
    ```
    创建任务后立即查询：
    curl http://localhost:8000/api/tasks/{task_id}

    期望 status=running。
    几秒后再查，期望 status=succeeded。
    ```
    验收标准：
    ```
    API 不被长任务阻塞。
    事件流能实时推送后台执行状态。
    ```

17. **端到端 Mock 场景测试**  
    目标：
    ```
    不用真实 Worker，先把 Router 自身跑稳。
    ```
    Todo：
    ```
    实现 tests/e2e/test_router_mock_scenarios.py。
    ```
    场景：
    ```
    1. 简单开发成功：
       用户任务 → dev → test pass → gate pass → succeeded

    2. 测试失败后修复成功：
       用户任务 → dev → test failed → repair → regression test pass → gate pass → succeeded

    3. 形式化失败后修复成功：
       用户任务 → dev → test pass + formal failed → repair → test pass + formal pass → succeeded

    4. 需求不完整：
       用户任务 → dev need_clarification 或 Main Agent 直接 ask_user → waiting_user

    5. 修复 3 轮仍失败：
       用户任务 → dev → test failed → repair x3 → still failed → partial_failed
    ```
    检查方式：
    ```
    pytest tests/e2e/test_router_mock_scenarios.py -q
    ```
    每个场景检查：
    ```
    1. 最终 task.status 正确。
    2. worker_jobs 数量正确。
    3. artifacts 类型正确。
    4. events 顺序正确。
    5. gate_result 正确。
    6. repair_rounds 正确。
    ```
    真实测试：
    ```
    python scripts/e2e_run_mock_task.py --scenario test_failed_then_repair_pass
    ```
    验收标准：
    ```
    没有真实 Worker 时，Router 的状态机、门禁、事件、Artifact 全部稳定。
    ```

18. **接入真实 MCP Server**  
    目标：
    ```
    把 mock worker 替换成真实 MCP worker 调用。
    ```
    Todo：
    ```
    实现 backend/app/mcp/client.py
    实现 backend/app/mcp/adapter.py
    ```
    配置：
    ```
    MCP_MODE=real
    PLC_WORKER_MCP_URL=http://localhost:9000/mcp
    PLC_WORKER_TIMEOUT_SECONDS=300
    ```
    Adapter 职责：
    ```
    1. WorkerInput → MCP tool call 参数。
    2. MCP response → WorkerResult。
    3. schema 校验。
    4. timeout。
    5. retry，可选。
    6. 错误归一化。
    7. mcp_request_id 写入 trace_context。
    ```
    检查方式：
    ```
    pytest tests/integration/test_mcp_real_contract.py -q
    ```
    可先使用本地 fake MCP server：
    ```
    python scripts/start_fake_mcp_server.py
    ```
    必须覆盖：
    ```
    1. list tools 可以看到 plc_dev.run / plc_test.run / plc_formal.run / plc_repair.run。
    2. plc_dev.run 接收 WorkerInput。
    3. 返回内容能解析成 WorkerResult。
    4. 非法返回会被 normalizer 拒绝。
    ```
    真实测试：
    ```
    python -m plc_worker_mcp_server
    MCP_MODE=real uvicorn app.main:app --reload
    ```
    验收标准：
    ```
    真实 plc-dev 至少能生成 plc_code artifact。
    如果 plc-test / plc-formal 暂时未真实接入，也要能用 hybrid 模式。
    ```
    Hybrid 配置：
    ```
    PLC_DEV_MODE=real
    PLC_TEST_MODE=mock
    PLC_FORMAL_MODE=mock
    PLC_REPAIR_MODE=mock
    ```

19. **真实 plc-dev 接入测试**  
    目标：
    ```
    真实开发 Worker 能在 Router 流程里生成 PLC 代码产物。
    ```
    输入输出契约：
    ```
    输入：
      requirements_ir 或 raw_user_request
      target_plc_language
      target_platform
      constraints
      expected_outputs

    输出：
      plc_code artifact
      io_contract artifact
      assumptions
      summary
    ```
    检查方式：
    ```
    pytest tests/integration/test_real_plc_dev.py -q
    ```
    必须覆盖：
    ```
    1. WorkerResult.execution_status=completed。
    2. outcome.status=passed 或 need_clarification。
    3. produced_artifacts 包含 plc_code。
    4. Artifact 内容非空。
    5. TaskState.current_artifacts.current_code 已更新。
    ```
    真实测试任务：
    ```
    帮我写一个电机启停 ST 程序：
    - StartBtn 启动
    - StopBtn 停止
    - EmergencyStop 触发时立即停止
    - MotorFault 触发时停止并锁存
    - FaultReset 复位
    - 输出 MotorRun、RunLamp、FaultLamp
    ```
    验收标准：
    ```
    Artifacts 中有 plc_code:v1 和 io_contract:v1。
    代码可以被前端 Artifact Panel 展示。
    Main Agent 最终回答引用 artifact，而不是直接塞完整内部日志。
    ```

20. **真实 plc-test 接入测试**  
    目标：
    ```
    真实测试 Worker 能读取 Router 生成的代码 artifact，返回测试报告。
    ```
    输入输出契约：
    ```
    输入：
      requirements_ir
      plc_code
      io_contract，可选

    输出：
      test_cases
      test_report
      failing_trace，可选
      metrics.test_metrics
      failures，可选
    ```
    检查方式：
    ```
    pytest tests/integration/test_real_plc_test.py -q
    ```
    必须覆盖：
    ```
    1. 对正确代码返回 passed。
    2. 对故意错误代码返回 failed。
    3. failed 时必须有 failure 和 evidence artifact。
    4. test_report artifact 可以读取。
    ```
    真实测试：
    ```
    准备故意错误代码：EmergencyStop 触发时没有强制 MotorRun=false。
    python scripts/real_test_plc_test_worker.py --fixture motor_estop_bug
    ```
    验收标准：
    ```
    plc-test 能发现失败。
    Router 能把 failure 写进 TaskState.failures。
    gates.has_blocking_failure=true。
    next_recommended_action=repair。
    ```

21. **真实 plc-formal 接入测试**  
    目标：
    ```
    形式化验证 Worker 能验证关键性质，并返回反例。
    ```
    输入输出契约：
    ```
    输入：
      requirements_ir
      plc_code
      safety constraints

    输出：
      formal_properties
      formal_report
      counterexample，可选
      metrics.formal_metrics
      failures，可选
    ```
    检查方式：
    ```
    pytest tests/integration/test_real_plc_formal.py -q
    ```
    必须覆盖：
    ```
    1. L3 任务自动触发 formal。
    2. 通过时 latest_formal_passed=true。
    3. 失败时有 counterexample。
    4. formal failed 后 gates.has_blocking_failure=true。
    ```
    真实测试性质：
    ```
    EmergencyStop = TRUE -> MotorRun = FALSE
    MotorFault = TRUE -> MotorRun = FALSE
    FaultLatched = TRUE -> MotorRun = FALSE
    ```
    验收标准：
    ```
    formal_report:v1 和 counterexample:v1 可以生成。
    Router 可以把 counterexample 作为 repair 的输入证据。
    ```

22. **真实 plc-repair 接入测试**  
    目标：
    ```
    修复 Worker 能基于测试失败或形式化反例生成 patch 和新代码版本。
    ```
    输入输出契约：
    ```
    输入：
      current plc_code
      failure evidence
      test_report / failing_trace
      formal_report / counterexample
      repair_round

    输出：
      patch
      patched plc_code
      repair_summary
    ```
    检查方式：
    ```
    pytest tests/integration/test_real_plc_repair.py -q
    ```
    必须覆盖：
    ```
    1. repair 没有 failure 时被 guard 拒绝。
    2. repair 成功后 current_code 指向 plc_code:v2。
    3. patch_metadata.from_code_artifact_id = plc_code:v1。
    4. patch_metadata.to_code_artifact_id = plc_code:v2。
    5. repair 后 regression_required=true。
    ```
    真实测试：
    ```
    dev 或 fixture 生成 EmergencyStop bug
    → plc-test failed 或 plc-formal failed
    → plc-repair 修复
    → plc-test regression
    → plc-formal regression
    ```
    验收标准：
    ```
    修复后自动回归。
    如果回归通过，task 可以 succeeded。
    如果回归失败，进入下一轮 repair，最多 3 轮。
    ```

23. **并行 Worker 调用**  
    目标：
    ```
    支持开发完成后并行调用 plc-test 和 plc-formal。
    ```
    Todo：
    ```
    实现 run_parallel_workers。
    ```
    规则：
    ```
    1. jobs 数量不能超过 max_parallel_workers。
    2. 每个 job 独立 worker_job_id。
    3. 任一 job 失败不影响其他 job 完成。
    4. 全部结果返回后统一 update_state。
    5. 如果任一 outcome.failed/blocking，进入 repair 候选。
    ```
    检查方式：
    ```
    pytest tests/integration/test_parallel_workers.py -q
    ```
    必须覆盖：
    ```
    1. test 和 formal 并行成功。
    2. test failed + formal passed。
    3. test passed + formal failed。
    4. 一个 timeout，一个 completed。
    5. 超过 4 个并发被拒绝。
    ```
    真实测试：
    ```
    创建 L3 任务：
    帮我实现带急停、互锁、故障锁存的输送线控制逻辑。
    ```
    验收标准：
    ```
    并行不会导致 TaskState 丢更新。
    Event seq 仍然单调递增。
    ```

24. **取消、超时和错误归一化**  
    目标：
    ```
    任务不会无限挂住，用户可以取消，Worker 错误可诊断。
    ```
    Todo：
    ```
    POST /api/tasks/{task_id}/cancel
    Worker timeout
    MCP error normalization
    Main Agent max turns
    Worker max calls
    ```
    错误类型：
    ```
    MCP_TIMEOUT
    MCP_CONNECTION_ERROR
    WORKER_SCHEMA_INVALID
    WORKER_EXECUTION_ERROR
    GUARD_REJECTED
    QUALITY_GATE_FAILED
    MAIN_AGENT_MAX_TURNS_EXCEEDED
    ```
    检查方式：
    ```
    pytest tests/integration/test_timeout_cancel_error.py -q
    ```
    必须覆盖：
    ```
    1. worker timeout 生成 worker.timeout event。
    2. timeout WorkerResult.execution_status=timeout。
    3. cancel 后 task.status=cancelled。
    4. cancelled 后不再启动新 worker。
    5. 非法 WorkerResult 被 normalizer 拒绝。
    ```
    真实测试：
    ```
    MOCK_SCENARIO=worker_timeout
    curl -X POST http://localhost:8000/api/tasks/{task_id}/cancel
    ```
    验收标准：
    ```
    任务可终止。
    错误能通过 events 和 worker_jobs 定位。
    ```

25. **Trace Mapping 和日志**  
    目标：
    ```
    每个任务可以追踪到 Main Agent run、WorkerJob、MCP request、Artifact 和 Event。
    ```
    Todo：
    ```
    trace_context
    openai_trace_id
    main_agent_run_id
    worker_job_id
    mcp_request_id
    artifact_ids
    event correlation
    ```
    说明：
    ```
    OpenAI Agents SDK 带 tracing，可用于可视化、调试、监控 agentic flows。
    后端需要把 SDK trace 和自己的 worker job / artifact / event id 关联起来。
    ```
    检查方式：
    ```
    pytest tests/integration/test_trace_mapping.py -q
    ```
    必须覆盖：
    ```
    1. main_agent.started event 有 main_agent_run_id。
    2. worker.started event 有 worker_job_id。
    3. artifact.created event 有 artifact_id。
    4. worker.completed event correlation 里有 artifact_ids。
    5. task 查询可以返回 trace summary。
    ```
    真实测试：
    ```
    GET /api/tasks/{task_id}
    ```
    期望：
    ```
    {
      "trace": {
        "openai_trace_id": "...",
        "main_agent_run_ids": ["..."],
        "latest_main_agent_run_id": "..."
      }
    }
    ```
    验收标准：
    ```
    出现问题时，可以从 task_id 反查：
    Main Agent 说了什么、调用了哪个 tool、哪个 worker job、产出了哪个 artifact。
    ```

26. **最终报告生成**  
    目标：
    ```
    任务完成时生成 final_report artifact，而不只是返回一段聊天文本。
    ```
    Todo：
    ```
    finish_task 必须生成 final_report:v1。
    ```
    内容包括：
    ```
    1. 用户目标
    2. 任务类型和难度
    3. 最终 PLC 代码 artifact
    4. I/O contract artifact
    5. 测试结果摘要
    6. 形式化验证结果摘要
    7. 修复轮次和 patch 摘要
    8. 假设
    9. 未解决问题
    10. 最终状态：succeeded / partial_failed / failed
    ```
    检查方式：
    ```
    pytest tests/integration/test_final_report.py -q
    ```
    必须覆盖：
    ```
    1. succeeded 任务有 final_report artifact。
    2. partial_failed 任务也有 final_report artifact。
    3. final_report 引用关键 artifact id。
    4. 不包含大段 worker log。
    ```
    真实测试：
    ```
    curl http://localhost:8000/api/tasks/{task_id}/artifacts
    curl http://localhost:8000/api/artifacts/{final_report_id}
    ```
    验收标准：
    ```
    前端可以直接展示最终报告。
    用户能看到代码、测试、验证、修复和未解决问题。
    ```

27. **后端评测集**  
    目标：
    ```
    用固定任务集回归 Router 行为，避免 prompt 或 worker 改动导致流程退化。
    ```
    Todo：
    ```
    准备 tests/eval/plc_tasks.yaml，至少 20 个任务。
    ```
    任务样例：
    ```
    1. 简单 ST 代码解释
    2. 电机启停
    3. 电机启停 + 急停
    4. 故障锁存 + 复位
    5. 双电机互锁
    6. 自动/手动模式切换
    7. 输送线顺序控制
    8. 定时器控制
    9. 计数器控制
    10. 已有代码修复
    11. 测试失败修复
    12. 形式化反例修复
    ```
    Case 定义：
    ```
    - id: motor_estop
      message: "帮我实现带急停的电机启停逻辑"
      expected:
        task_type: new_plc_development
        min_difficulty: L3
        required_workers:
          - plc-dev
          - plc-test
          - plc-formal
        final_status:
          - succeeded
          - partial_failed
    ```
    检查方式：
    ```
    pytest tests/e2e/test_eval_tasks.py -q
    ```
    输出：
    ```
    eval_report.md
    ```
    真实测试：
    ```
    make eval
    ```
    验收标准：
    ```
    核心 20 个任务不退化。
    L3 任务不能跳过 formal。
    repair 后不能跳过 regression。
    ```

28. **部署前检查**  
    目标：
    ```
    本地 MVP 可以稳定给前端和 MCP 联调。
    ```
    Todo：
    ```
    准备：
    docker-compose.yml
    .env.example
    Makefile
    README_BACKEND.md
    ```
    docker-compose 包含：
    ```
    postgres
    router-backend
    fake-mcp-server，可选
    ```
    Makefile：
    ```
    dev:
    	uvicorn app.main:app --reload

    test:
    	pytest -q

    migrate:
    	alembic upgrade head

    schema:
    	python -m app.schemas.json_schema_export

    eval:
    	pytest tests/e2e/test_eval_tasks.py -q
    ```
    检查方式：
    ```
    cp .env.example .env
    docker compose up -d
    make migrate
    make test
    make eval
    ```
    真实测试：
    ```
    POST /api/tasks
    GET /api/tasks/{id}/events
    GET /api/tasks/{id}/artifacts
    GET /api/artifacts/{id}
    ```
    验收标准：
    ```
    新同事按 README 能在 30 分钟内跑通 mock happy path。
    ```

29. **推荐开发顺序总表**  
    ```
    | 阶段 | 内容 | 完成后检查 | 真实测试 |
    | --- | --- | --- | --- |
    | 1 | 工程初始化 | /health 通过 | 服务可启动 |
    | 2 | 五大 Schema | schema 单测通过 | fixture 可解析 |
    | 3 | DB 和 Repository | repository 测试通过 | seed task 可查 |
    | 4 | Artifact Store | artifact 测试通过 | 文件可写可读 |
    | 5 | Event + SSE | event 测试通过 | curl -N 可看到事件 |
    | 6 | Task API | API 测试通过 | POST /api/tasks 创建任务 |
    | 7 | TaskState 初始化 | task init 测试通过 | 创建任务后 unknown/L0，artifact/event 可查 |
    | 8 | Scheduler Guard | guard 测试通过 | 非法调用被拒绝 |
    | 9 | Quality Gate | gate 测试通过 | 有 blocking failure 不能 success |
    | 10 | Mock MCP Adapter | mock 测试通过 | 四个 worker mock 可调用 |
    | 11 | WorkerResult Handler | state 更新测试通过 | dev/test/formal/repair 链路更新 state |
    | 12 | Agent Function Tools | tool 测试通过 | 单独调用工具成功 |
    | 13 | Main Agent Service | mock agent 集成测试通过 | happy path 可跑完 |
    | 14 | Runtime Loop | 后台任务测试通过 | 创建任务后异步执行 |
    | 15 | E2E Mock | 5 个场景通过 | 修复循环可跑 |
    | 16 | 真实 MCP 接入 | contract 测试通过 | real/mock hybrid 跑通 |
    | 17 | 真实 dev/test/formal/repair | 各 worker 集成测试通过 | PLC 示例任务跑通 |
    | 18 | 并行、取消、超时 | 可靠性测试通过 | timeout/cancel 可观测 |
    | 19 | Trace + Final Report | trace/report 测试通过 | 前端可展示完整过程 |
    | 20 | Eval + 部署 | make test && make eval 通过 | docker compose 一键启动 |
    ```

30. **第一阶段最小目标**  
    闭环：
    ```
    POST /api/tasks
      → 创建 TaskState
      → 写 raw_user_request artifact
      → 发 task.created event
      → Runtime 后台执行
      → Main Agent 或固定 runtime 调用 mock plc-dev
      → mock plc-dev 写 plc_code:v1
      → mock plc-test 写 test_report:v1
      → run_quality_gate
      → finish_task
      → 写 final_report:v1
      → SSE 全程可见
    ```
    验收命令：
    ```
    make migrate
    make test
    uvicorn app.main:app --reload
    ```
    创建任务：
    ```
    curl -X POST http://localhost:8000/api/tasks \
      -H "Content-Type: application/json" \
      -d '{
        "message": "帮我写一个电机启停控制逻辑，包含启动、停止、急停和运行指示。",
        "project_context": {
          "target_plc_language": "ST",
          "target_platform": "Codesys"
        }
      }'
    ```
    查看：
    ```
    curl -N http://localhost:8000/api/tasks/{task_id}/events
    curl http://localhost:8000/api/tasks/{task_id}/artifacts
    ```
    通过标准：
    ```
    1. API 能创建任务。
    2. Event stream 能展示执行过程。
    3. Artifact Store 有 plc_code、test_report、final_report。
    4. TaskState 最终 status=succeeded。
    5. 全流程不依赖真实子智能体。
    ```

31. **第二阶段最小目标**  
    目标：
    ```
    第二周接入真实 plc-dev，其余 worker 继续 mock：
    real plc-dev
    mock plc-test
    mock plc-formal
    mock plc-repair
    ```
    验收标准：
    ```
    1. 真实 plc-dev 能产出 plc_code:v1。
    2. mock plc-test 能读取真实代码 artifact。
    3. final_report 能引用真实代码。
    4. 前端可以展示真实 PLC 代码。
    ```

32. **第三阶段最小目标**  
    目标：
    ```
    第三周接入真实 plc-test 和修复闭环：
    real plc-dev
    real plc-test
    mock/real plc-repair
    mock plc-formal
    ```
    验收标准：
    ```
    1. 故意错误代码能被 plc-test 发现。
    2. Router 能把 test failure 写入 TaskState.failures。
    3. plc-repair 能被触发。
    4. repair 后自动 regression test。
    ```

33. **第四阶段最小目标**  
    目标：
    ```
    第四周接入 plc-formal，稳定 L3 高可靠流程：
    dev → test + formal → repair → regression test + formal regression → final_report
    ```
    验收标准：
    ```
    1. L3 任务不会跳过 formal。
    2. formal failed 能产生 counterexample。
    3. counterexample 能作为 repair 输入。
    4. repair 后 formal regression 自动执行。
    5. 最多 3 轮修复。
    6. 20 个 eval 任务核心路径通过。
    ```

34. **后端完成 MVP 的判定标准**  
    ```
    1. 五大 schema 已落地，并且有 JSON Schema 导出。
    2. 任务、事件、artifact、worker_job 全部持久化。
    3. 前端可以通过 SSE 看到完整执行过程。
    4. Main Agent 可以通过 function tools 调用外部 worker。
    5. Runtime Guard 能挡住非法调用。
    6. Quality Gate 能挡住不完整交付。
    7. mock worker 下 5 个核心 E2E 场景通过。
    8. 至少 plc-dev 和 plc-test 已真实接入。
    9. repair loop 可用，最多 3 轮。
    10. L3 任务会触发 formal，或在 formal 未真实接入时明确降级为 partial / mock。
    11. 每个最终任务都有 final_report artifact。
    12. 任何失败都能从 task_id 回放 events、worker_jobs、artifacts。
    ```

35. **参考**  
    ```
    OpenAI Agents SDK:
    https://openai.github.io/openai-agents-python/
    ```
