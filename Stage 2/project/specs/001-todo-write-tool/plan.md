# Implementation Plan: TodoWrite Tool

**Branch**: `001-todo-write-tool` | **Date**: 2026-07-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from [specs/001-todo-write-tool/spec.md](./spec.md)

## Summary

为 Agent Harness 添加 `todo_write` 工具，让 Agent 在执行复杂任务前能规划步骤、跟踪进度。核心改动：新增 TodoWriteTool（符合现有 Tool 基类模式）、在 Agent 主循环中维护计数器 + 注入 nag 提醒、在 SYSTEM_PROMPT 中添加简洁的工作流指导。纯增量改动，不修改现有工具行为。

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: 仅标准库 + openai（已有依赖），无新增第三方依赖

**Storage**: 进程内存（全局列表 `CURRENT_TODOS`），不持久化

**Testing**: pytest（项目尚无测试框架，本 feature 建立测试基础设施）

**Target Platform**: 跨平台（Windows/Linux），当前在 Windows 11 上开发

**Project Type**: CLI agent harness（单进程命令行应用）

**Performance Goals**: 无硬性要求。todo_write 本身无 I/O，O(1) 操作

**Constraints**: 无特殊约束

**Scale/Scope**: 单用户、单进程、1 个新工具 + Agent 循环 2 处改动 + 1 处 prompt 修改

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Correctness First | ✅ | todo_write 不做实际工作（无 I/O、无副作用），失败模式极少 |
| II. Small Steps | ✅ | V1 仅 1 个工具 + 1 个计数器 + 1 处 prompt 修改，~150 行新增代码 |
| III. Clarity & Maintainability | ✅ | TodoWriteTool 职责单一（维护列表 + 显示），命名遵循现有约定 |
| IV. Consistent Style | ✅ | 继承 Tool 基类，遵循 ToolParameter 模式，注册方式与现有 10 个工具一致 |
| V. Don't Reinvent Wheel | ✅ | 设计直接参考 Claude Code TodoWrite V1 + learn-claude-code 教程 |
| VI. Mainstream Alignment | ✅ | 三个状态（pending/in_progress/completed）是任务管理的通用模式 |
| VII. Core Module Tests | ✅ | Agent 循环（含计数器逻辑）是核心模块，plan 中已包含测试任务 |
| VIII. Backward Compatibility | ✅ | 纯增量改动：新增工具注册、Agent 循环追加逻辑、prompt 追加段落。现有 10 个工具零改动 |

**Gate Result**: ALL PASS → Proceed to Phase 0

## Project Structure

### Documentation (this feature)

```text
specs/001-todo-write-tool/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (tool input_schema contract)
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
agent/
├── agent.py             # [MODIFY] 添加 rounds_since_todo 计数器 + nag 提醒注入
└── prompts.py           # [MODIFY] SYSTEM_PROMPT 追加 planning 指导段落

tools/
├── __init__.py          # [MODIFY] register_all() 注册 TodoWriteTool
└── todo_write.py        # [NEW] TodoWriteTool 实现

tests/
└── test_todo_write.py   # [NEW] 单元测试：工具本身 + Agent 循环中的计数器逻辑
```

**Structure Decision**: 单项目结构，沿用现有目录分层。`tools/todo_write.py` 落点在 `tools/`（与其他工具一致），Agent 循环逻辑修改在 `agent/agent.py`，prompt 修改在 `agent/prompts.py`。

## Complexity Tracking

> 无 Constitution Check 违规项，此节略过。
