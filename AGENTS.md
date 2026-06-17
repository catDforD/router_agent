# 仓库指南

## 项目结构与模块组织

本仓库包含 Router 后端契约定义和运行时模型。

- `backend/`：运行时代码的 Python 包。
- `backend/models/router_schema.py`：Router v1 跨服务契约的 Pydantic v2 模型。
- `schema/`：JSON Schema 契约文件，每种消息或产物类型对应一个 schema。
- `schema/ts/router_contract.d.ts`：面向消费者生成或维护的 TypeScript 声明接口。
- `docs/`：设计说明和实施计划。
- `pyproject.toml` 和 `uv.lock`：Python 项目元数据和锁定的依赖状态。

当 Python 模型、JSON Schema 文件和 TypeScript 声明描述同一份契约时，请保持 schema 变更在三者之间同步。

## 构建、测试与开发命令

使用 `uv` 进行环境和依赖管理。

- `uv sync`：根据 `pyproject.toml` 和 `uv.lock` 创建或更新本地虚拟环境。
- `uv run python -m compileall backend`：检查 Python 文件是否存在语法错误。
- `uv run python - <<'PY' ... PY`：在尚无测试套件时运行聚焦的验证片段。
- `git diff --check`：在提交前检测空白字符错误。

当前没有已打包的应用入口或构建步骤。

## 编码风格与命名约定

目标版本为 Python `>=3.11` 和 Pydantic `>=2.11,<3`。遵循 `backend/models/router_schema.py` 中的现有风格：显式枚举、`BaseModel` 子类、带类型的字段，以及用于严格边界模型的 `extra="forbid"`。使用 4 空格缩进；字段使用描述性的 snake_case；类和枚举使用 PascalCase；仅常量使用 UPPER_CASE。除非有意进行带版本的 schema 迁移，否则请保留 `router.v1`、`plc-dev`、`need_clarification` 等契约字符串值。

## 测试指南

目前尚未配置测试框架。对于模型或 schema 更新，在引入行为时应添加聚焦的验证覆盖，最好在未来的 `tests/` 目录下使用 `pytest`。测试名称应描述契约行为，例如 `test_worker_result_rejects_extra_fields`。在测试尚未建立之前，至少运行语法检查，并为变更的 schema 路径实例化具有代表性的 Pydantic 模型。

## 提交与拉取请求指南

Git 历史使用简洁的 Conventional Commit 前缀，例如 `feat: add router v1 schema contract`、`docs: add architecture implementation plan` 和 `chore: add uv project setup`。请继续使用 `feat:`、`fix:`、`docs:` 或 `chore:`。

Git 新建分支请使用以下格式的命名方式：`feat/xxx`、`fix/xxx`、`docs/xxx`、`chore/xxx`。

拉取请求应包含摘要、契约兼容性说明、已运行的验证命令，以及相关 issue 或设计文档链接。修改 `schema/` 或 `backend/app/models/` 时，请包含示例载荷。

请使用 ssh 的方式进行 git 相关操作。

## 安全与配置提示

不要提交密钥、本地虚拟环境或生成的缓存。请将 schema 文件视为外部 API 契约：如果没有记录迁移影响，应避免破坏字段名、枚举值或必填属性。
