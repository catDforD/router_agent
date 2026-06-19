# 湖州 Router 方案

> 当前实现状态：Router Main Agent 已经改为 OpenAI 兼容 Chat Completions tool loop。它不依赖 OpenAI Responses API，也不依赖 `response_format` 结构化输出；最终报告和终态变更通过 `write_final_report`、`finish_task` 等工具完成。本文仍保留原始设计思路，但涉及 OpenAI Agents SDK 的描述应按这一实现状态理解。

1.  **Agent + 外部 Worker 架构**

    :::
    超级智能体 Main Agent：通过 OpenAI 兼容 Chat Completions + tool calling 实现，像 Claude Code / Codex 的主线程

      - 理解用户意图

      - 判断任务难度

      - 决定调用哪些外部 worker

      - 决定串行、并行、循环、终止、追问用户

      - 汇总结果并给最终交付

    四个外部 worker：业务上仍可称为 Subagents，但工程上不是 Router 内部 Agent

      - plc-dev：生成/修改 PLC 代码

      - plc-test：生成测试、执行测试、返回失败轨迹

      - plc-formal：抽取性质、验证性质、返回反例

      - plc-repair：基于测试失败/形式化反例做最小修复
    :::

    这里需要明确边界：`plc-dev`、`plc-test`、`plc-formal`、`plc-repair` 不由 Router 直接实现，也不接管 Main Agent 会话。Router 只通过 MCP tools 调用它们。Main Agent 负责规划和综合，外部 worker 负责专业能力。

2.  **架构设计**

    结构划分：

    :::
    Main Agent = 自由规划器 + 调度决策器 + 结果综合器

    Backend Runtime = 执行器 + 状态管理 + Artifact 管理 + 预算/并发控制

    MCP Tool Adapter = 外部 worker 接口封装 + schema 校验 + 错误归一化

    External Workers = plc-dev / plc-test / plc-formal / plc-repair
    :::

    Main Agent 使用普通 tool calling 做多轮编排。Backend Runtime 仍然由我们自己实现，负责把 Main Agent 的决策变成受控执行、持久化事件和 artifact，并对关键状态变更做二次校验。

    理想的执行流程：

    :::
    用户：帮我实现一个带急停、故障锁存、自动/手动切换的输送线控制逻辑

    Main Agent：

      1. 识别这是 PLC 新开发任务

      2. 判断复杂度 L3/L4

      3. 决定先调用 plc-dev

      4. 开发完成后并行调用 plc-test 和 plc-formal

      5. 发现测试失败或验证失败后调用 plc-repair

      6. 修复后重新调用测试和形式化验证

      7. 最终综合代码、测试报告、验证报告、修复说明

    Backend Runtime：

      - 真正发起 Main Agent run 和 MCP tool call

      - 保存代码版本

      - 保存测试报告

      - 保存形式化反例

      - 控制最多修复 3 轮

      - 控制最多并发 4 个 worker job

      - 保证每个 worker 输入输出符合 schema
    :::

3.  **子智能体调用设计**

    4 个子智能体最好设计成 4 种外部能力类型，而不是 Router 内部的 4 个 Agent。每次调用产生一个 worker job，job 的执行细节由外部系统负责。

    ```plaintext
    External Worker Type:
      - plc-dev
      - plc-test
      - plc-formal
      - plc-repair

    MCP Tool:
      - plc_dev.run
      - plc_test.run
      - plc_formal.run
      - plc_repair.run

    Worker Job:
      - plc-dev#conveyor_control
      - plc-test#integration_test
      - plc-formal#safety_properties
      - plc-repair#round_1
    ```

    最小输入输出协议：

    ```typescript
    type WorkerInput = {
      task_id: string;
      objective: string;
      input_artifact_ids: string[];
      constraints?: string[];
      expected_schema?: string;
    };

    type WorkerResult = {
      status: "succeeded" | "failed" | "need_clarification";
      artifact_ids: string[];
      summary: string;
      assumptions?: string[];
      failures?: unknown[];
      next_recommended_action?: string;
    };
    ```

    Main Agent 不直接依赖外部 worker 的内部上下文，只依赖稳定的输入输出协议和 artifact 引用。

4.  **Main Agent 应该具备哪些工具**

    ```typescript
    type MainAgentTool =
      | "update_plan"
      | "request_clarification"
      | "call_plc_dev"
      | "call_plc_test"
      | "call_plc_formal"
      | "call_plc_repair"
      | "run_parallel_workers"
      | "read_artifact"
      | "run_quality_gate"
      | "write_final_report"
      | "finish_task";
    ```

    这些工具由 Backend Runtime 提供给 Main Agent。Main Agent 可以选择工具和参数，但并发上限、修复轮次、schema 校验、artifact 写入权限由 Runtime 强制执行。

5.  **Main Agent 调度策略**

    Main Agent 不应该只是简单 router，而应该有一套调度规则。例如：

    :::
    你是 PLC 超级智能体，负责完成用户任务。

    你可以调用以下外部 worker：

    1. plc-dev：生成/修改 PLC 代码

    2. plc-test：生成并执行测试

    3. plc-formal：形式化验证

    4. plc-repair：根据失败证据修复

    调度原则：

    - 不确定需求时，先追问，不要盲目开发

    - 简单解释类任务，不调用 worker 或只调用一个 worker

    - 新开发任务，通常先调用 plc-dev

    - 中高风险 PLC 控制任务，开发后必须调用 plc-test

    - 包含急停、互锁、故障锁存、模式互斥、状态机安全性质时，必须调用 plc-formal

    - 测试或形式化验证失败时，调用 plc-repair

    - 修复后必须重新调用 plc-test；如果之前形式化验证失败，也必须重新调用 plc-formal

    - 最多修复 3 轮；仍失败则停止并说明原因

    - 最终回答必须包含代码、假设、测试结果、验证结果和未解决问题
    :::

    这些规则写进 Main Agent instructions，但不能只靠 prompt 保证。Backend Runtime 必须对关键规则做二次校验，例如修复轮次、必须测试、必须形式化验证和最终质量门禁。

6.  **Main Agent 核心循环**

    可以抽象成一个 runtime loop。Main Agent 每一步重新决策，Runtime 负责执行和约束：

    ```python
    def router_runtime_loop(user_task):
        state = init_task_state(user_task)

        while not state.done:
            decision = run_main_agent(state.view())
            decision = validate_decision(decision, state)

            if decision.type == "ask_user":
                return ask_user(decision.question)

            if decision.type == "call_worker":
                result = call_mcp_tool(
                    worker_type=decision.worker_type,
                    objective=decision.objective,
                    input_artifacts=decision.input_artifacts,
                    expected_schema=decision.expected_schema,
                )
                state = update_state(state, result)

            if decision.type == "spawn_parallel":
                results = spawn_worker_jobs_parallel(
                    jobs=decision.jobs,
                    max_concurrency=4,
                )
                state = update_state(state, results)

            if decision.type == "run_gate":
                gate_result = run_quality_gate(state, decision.gate)
                state = update_state(state, gate_result)

            if decision.type == "finish":
                state.done = True

            if state.repair_rounds >= 3 and state.has_blocking_failure:
                state.done = True
                state.final_status = "partial_failed"

        return compose_final_answer(state)
    ```

    这里 Main Agent 是自由决策，但不是无限自由。例如：

    :::
    测试失败，但形式化验证通过

    → 可能只调用 repair，然后只跑测试回归

    测试通过，但形式化验证失败

    → repair 主要基于 counterexample，然后测试 + 形式化都回归

    测试和形式化都失败，但失败指向需求歧义

    → 不修复，先追问用户

    开发 worker 返回假设太多

    → Main Agent 追问用户，而不是继续测试
    :::

7.  **后端实现**

    后端只需要把边界做清楚，不必过早复杂化：

    - `TaskState`：记录任务目标、当前阶段、复杂度、repair_rounds、关键 artifact id、未解决问题

    - `ArtifactStore`：保存代码、测试报告、验证报告、反例、修复说明；artifact 尽量不可变，更新时生成新版本

    - `MCP Tool Adapter`：封装 `plc-*` 既有接口，统一输入输出 schema、timeout、错误码和运行日志

    - `Scheduler Guard`：限制允许的状态转移、最多修复 3 轮、最多并发 4 个 worker job

    - `Quality Gate`：在最终输出前检查是否已经满足测试、形式化验证和必要的人工追问条件

    - `Trace Mapping`：把 OpenAI trace、worker job id、artifact id 关联起来，方便回放和审计

    这样实现后，OpenAI 兼容 tool loop 负责 Main Agent 的智能决策；Backend Runtime 负责工程上的确定性约束；外部 worker 通过 MCP tools 提供专业能力。
