# Research: TodoWrite Tool

**Feature**: TodoWrite Tool | **Date**: 2026-07-11

## Decision 1: Tool Implementation Pattern

- **Decision**: 遵循现有 Tool 基类模式（继承 `Tool`，实现 `run()` + `get_parameters()`）
- **Rationale**: 项目已有 10 个工具全部使用此模式，新增工具必须保持一致性（Constitution IV）。Tool 基类提供了 `to_schema()` 自动转换，零额外成本。
- **Alternatives considered**:
  - 独立函数 + 手动注册：违反现有模式，增加维护负担
  - 使用插件系统：过度设计，V1 不需要

## Decision 2: 计数器位置

- **Decision**: 计数器在 `Agent.run()` 主循环内维护，而非在 todo_write 工具内部或 executor 中
- **Rationale**: FR-007 明确要求"每轮结束后递增"——只有 Agent 主循环知道"什么时候一轮结束"。工具内部不知道它自己被谁调用、在什么上下文中调用。executor 只知道单次工具执行，不知道"轮次"概念。
- **Alternatives considered**:
  - 放在 executor 中：executor 是工具无关的，不应耦合 todo_write 特定逻辑
  - 放在 todo_write 工具内部：工具是纯函数式的（参数→结果），不应持有计数器状态

## Decision 3: 提醒消息格式

- **Decision**: 使用 `<reminder>Update your todos.</reminder>` 字面字符串，以 `role: "user"` 注入消息列表
- **Rationale**: 教程直接使用此格式，Claude Code 源码中也有类似机制。user role 确保 LLM 将其视为来自用户的指令，优先级高于 assistant 自省。
- **Alternatives considered**:
  - 作为 system 消息注入：容易被后续长消息稀释
  - 修改工具返回结果来暗示：侵入性强，不够直接

## Decision 4: SYSTEM_PROMPT 修改方式

- **Decision**: 在现有 SYSTEM_PROMPT 末尾追加 planning 指导段落，不修改现有内容
- **Rationale**: Constitution VIII（向后兼容）+ 现有 prompt 的 behavioral rules 仍然适用，增量追加最安全。
- **Alternatives considered**:
  - 完全重写 prompt：可能破坏现有 Agent 行为
  - 在每轮开始时动态注入：增加复杂度，V1 不需要

## Decision 5: 测试策略

- **Decision**: 使用 pytest，测试分为两层——单元测试（TodoWriteTool 独立行为）+ 集成测试（Agent 循环中的计数器逻辑）
- **Rationale**: Constitution VII 要求核心模块（Agent 循环）有单测。todo_write 工具本身逻辑简单但有状态（全局列表），必须验证状态正确性。
- **Alternatives considered**:
  - 仅集成测试：工具本身 bug 难定位
  - Mock LLM 的 E2E 测试：V1 阶段过度，LLM 非确定性导致测试不稳定
