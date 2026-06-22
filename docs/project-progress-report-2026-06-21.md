# Router Agent 项目进度汇报

> 汇报日期：2026-06-21  
> 范围：基于当前仓库代码、`docs/` 文档、OpenSpec 任务清单和一次本地前后端联调结果。

## 结论

Router Agent 已经推进到可本地演示的前后端工作台：用户可以在前端提交 PLC 任务，后端 Main Agent 通过 OpenAI 兼容 Chat Completions tool loop 编排 worker、写入事件和 artifact，并通过 SSE 在前端展示过程。

## 当前完成情况

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| Router 契约 | 已落地 | Pydantic 模型、JSON Schema 和 TypeScript 声明均已存在，覆盖 TaskState、WorkerInput、WorkerResult、Artifact、RouterEvent。 |
| 后端 API | 已跑通 | FastAPI 提供健康检查、任务创建、任务状态、消息追加、取消、SSE 事件、artifact 和 trace summary 接口。 |
| Runtime 与持久化 | 已跑通 | PostgreSQL 表、Alembic 迁移、repositories、artifact store、event service、worker job 和 gate result 已实现。 |
| Main Agent | 已重构完成 | 当前使用 OpenAI 兼容 Chat Completions tool calling，不依赖 Responses API 或 `response_format`。 |
| Worker 接入 | 可联调 | 已有 mock、real/hybrid MCP 路径，暴露 `plc_dev.run`、`plc_test.run`、`plc_formal.run`、`plc_repair.run`。 |
| Quality Gate | 已跑通 | 能基于需求、代码、测试、形式化、回归和最终交付状态做门禁判断，并写入 gate artifact。 |
| 前端工作台 | 已跑通 | Vite + React 工作台已具备任务输入、事件时间线、Subagent 卡片、Quality Gates、Artifact 面板、Final Report 和 Trace 入口。 |
| 本地启动器 | 已跑通 | `uv run main.py` 可启动后端、前端、迁移、artifact root，并按配置启动 PLC worker。 |
| 文档 | 已补齐关键路径 | `docs/local-dev.md`、`docs/frontend-api.md`、`docs/architecture.md`、`docs/backend.md` 已覆盖启动、接口和架构说明。 |

活跃 OpenSpec 变更清单目前均为已勾选状态，包括：

- `add-frontend-api-usage-guide`
- `add-router-frontend-workspace-and-dev-launcher`
- `refactor-main-agent-openai-compatible-tool-loop`

## 整体工作流程

```text
用户在前端提交 PLC 任务
  ↓
Frontend POST /api/tasks 创建任务，并打开 SSE 事件流
  ↓
Backend Runtime 创建 TaskState、raw_user_request artifact 和初始事件
  ↓
Main Agent 通过 Chat Completions tool loop 更新计划、选择工具
  ↓
Runtime 执行工具：调用 PLC MCP worker、读取/写入 artifact、运行 Quality Gate
  ↓
Event Service 持久化公开事件，前端通过 SSE 展示进度
  ↓
Artifact Store 保存代码、测试报告、形式化报告、gate report、final report
  ↓
Main Agent 写最终报告，并通过 finish_task 收口为 succeeded / partial_failed / failed
  ↓
前端展示最终报告、Subagent 状态、Quality Gates、Artifact 和 Trace
```

### 流程录屏样例

> 录屏均来自本地启动的前端、后端和 PLC worker。已重新录制为 1920x1080；建议汇报时打开 MP4 并全屏播放，WebM 作为备用格式保留。

| 样例 | 覆盖路径 | 结果 | 附件 |
| --- | --- | --- | --- |
| 简单电机启停开发 | 前端提交 → `plc-dev` → `plc-test` → Quality Gate → Final Report | `succeeded`，48 个事件，8 个 artifacts | [结果摘要](assets/progress-report-2026-06-21/results/workflow-simple-motor-result.md) |
| 安全相关输送线任务 | 前端提交 → `plc-dev` → `plc-test` → `plc-formal` → Quality Gate → Final Report | `partial_failed`，68 个事件，12 个 artifacts | [结果摘要](assets/progress-report-2026-06-21/results/workflow-safety-formal-result.md) |
| 说明类轻量任务 | 前端提交 → Main Agent 规划与回答 → 不调用 worker → Final Report | `succeeded`，26 个事件，4 个 artifacts | [结果摘要](assets/progress-report-2026-06-21/results/workflow-qa-explanation-result.md) |

#### 样例一：简单电机启停开发

<video controls width="100%">
  <source src="assets/progress-report-2026-06-21/videos/workflow-simple-motor.mp4" type="video/mp4">
  <source src="assets/progress-report-2026-06-21/videos/workflow-simple-motor.webm" type="video/webm">
</video>

输出结果：

- Task ID: `task-e4d4d86f29bb4670b52462e51d9c3254`
- 最终状态: `succeeded`
- Worker: `plc-dev:completed`、`plc-test:completed`
- 系统输出摘要：交付 `FB_MotorControl` ST 功能块，包含启动、停止、急停、故障锁存、故障复位、运行灯和故障灯；测试和 Quality Gate 均通过。
- 附件：[结果摘要](assets/progress-report-2026-06-21/results/workflow-simple-motor-result.md)、[Final Report JSON](assets/progress-report-2026-06-21/results/workflow-simple-motor-final-report.json)、[Trace JSON](assets/progress-report-2026-06-21/results/workflow-simple-motor-trace.json)

#### 样例二：安全相关输送线任务

<video controls width="100%">
  <source src="assets/progress-report-2026-06-21/videos/workflow-safety-formal.mp4" type="video/mp4">
  <source src="assets/progress-report-2026-06-21/videos/workflow-safety-formal.webm" type="video/webm">
</video>

输出结果：

- Task ID: `task-0aa272d8747d4b29b96e324e0463747f`
- 最终状态: `partial_failed`
- Worker: `plc-dev:completed`、`plc-dev:completed`、`plc-test:completed`、`plc-formal:completed`
- 系统输出摘要：核心安全功能（启动/停止、急停、故障锁存）已生成、测试并通过形式化验证；自动/手动模式切换和电机互锁未被当前 LLM fallback worker 生成，因此最终如实标记为部分失败。
- 附件：[结果摘要](assets/progress-report-2026-06-21/results/workflow-safety-formal-result.md)、[Final Report JSON](assets/progress-report-2026-06-21/results/workflow-safety-formal-final-report.json)、[Trace JSON](assets/progress-report-2026-06-21/results/workflow-safety-formal-trace.json)

#### 样例三：说明类轻量任务

<video controls width="100%">
  <source src="assets/progress-report-2026-06-21/videos/workflow-qa-explanation.mp4" type="video/mp4">
  <source src="assets/progress-report-2026-06-21/videos/workflow-qa-explanation.webm" type="video/webm">
</video>

输出结果：

- Task ID: `task-ecb35a08be1e43b5825df8a086cc102e`
- 最终状态: `succeeded`
- Worker: 无 worker 调用
- 系统输出摘要：Main Agent 将任务识别为 L0 说明类任务，仅给出自保持、停止优先和故障复位的简洁设计说明；不生成代码，不进入 worker 调度链路。
- 附件：[结果摘要](assets/progress-report-2026-06-21/results/workflow-qa-explanation-result.md)、[Final Report JSON](assets/progress-report-2026-06-21/results/workflow-qa-explanation-final-report.json)、[Trace JSON](assets/progress-report-2026-06-21/results/workflow-qa-explanation-trace.json)


## 本地联调结果

本次使用 `.env` 中的本地配置启动：

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8000`
- PLC Worker MCP: `http://localhost:9000/mcp`

演示任务：

```text
帮我实现一个输送线 PLC 控制逻辑：包含启动/停止、急停、故障锁存、自动/手动模式切换，并输出 ST 代码、测试结果和形式化验证结论。
```

结构化结果：

| 项 | 结果 |
| --- | --- |
| Task ID | `task-099c1bccfe124635b67145946cebad74` |
| 最终状态 | `partial_failed` |
| 事件数量 | 81 |
| Worker Jobs | `plc-dev` 2 次、`plc-test` 2 次、`plc-formal` 1 次，均 completed |
| Artifacts | 14 个，包括需求、PLC 代码、IO contract、测试报告、形式化报告、gate report、final report、main agent log |
| Gate | 首次 test gate 因代码更新后测试报告过期失败；补跑回归测试后，第二次 Quality Gate 通过 |

这次联调证明主链路、事件流、artifact、gate 和最终报告都能闭环；

## 前端截图

### 工作台初始态

![Router Agent 工作台初始态](assets/progress-report-2026-06-21/router-workspace-initial.png)

### 任务执行中

![Router Agent 任务执行中](assets/progress-report-2026-06-21/router-task-testing.png)

### 任务终态与最终报告摘要

![Router Agent 任务终态](assets/progress-report-2026-06-21/router-task-final.png)

## 验证记录

| 命令 | 结果 |
| --- | --- |
| `uv run python -m compileall backend` | 通过 |
| `uv run pytest backend/app/tests/unit -q` | 253 passed |
| `uv run pytest backend/app/tests/e2e/test_router_mock_scenarios.py -q` | 5 passed |
| `cd frontend && npm run build` | 通过 |
| `cd frontend && npm run smoke` | 通过，创建任务并读取首事件、artifact 和 trace |
| `git diff --check` | 通过 |

## 主要风险与下一步

1. 外部 worker 输出需要收敛。本次 real/hybrid 路径中多次出现 worker LLM 输出不匹配 draft contract，系统使用了本地 fallback draft 才继续集成测试。**同时当前未接入真实的 subagent 进行测试，各个 subagent 均使用 LLM 模拟功能测试。**
2. 前端长标题和密集事件流展示还需优化。当前功能完整，但长任务标题、长 artifact id 和密集事件在小视口下仍需要进一步压缩和折叠。
3. Router Agent 的后台工作逻辑还需优化，目前先 plan 再执行的过程较为僵硬，需优化系统提示词和工作链路使其更加灵活。

## 参考文档

- [README](../README.md)
- [总体架构](architecture.md)
- [后端开发计划](backend.md)
- [前端 API 使用指南](frontend-api.md)
- [本地开发环境](local-dev.md)
- [后端测试指南](backend_test.md)
