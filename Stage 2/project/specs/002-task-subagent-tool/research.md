# Research: Task Tool & Sub-Agent

**Feature**: 002-task-subagent-tool
**Date**: 2026-07-11 (updated)
**Reference**: https://github.com/shareAI-lab/learn-claude-code/tree/main/s06_subagent

## Decision 1: Sub-Agent 实现方式 — `SubAgent(Agent)` 子类

**Decision**: 创建 `SubAgent(Agent)` 子类。在 `__init__()` 中通过 `super().__init__()` 设定 Sub-Agent 的所有默认配置（`SUB_SYSTEM_PROMPT`、`max_steps=30`、`tool_filter={"task", "todo_write"}`、`print_handler=sub_print_handler`）。覆盖 `_execute_tool_calls()` 以叠加轮数跟踪和提醒注入逻辑。

**Rationale**:
- **配置内聚**：Sub-Agent 的所有特殊性（prompt、工具限制、步数限制、输出格式）集中在子类 `__init__()` 中，调用方只需 `SubAgent(llm, executor)`
- **轮数跟踪天然隔离**：`self._round` 实例变量，每次 `SubAgent()` 创建新的，无需 register/unregister hook
- **不碰 `run()` 循环体**：覆盖 `_execute_tool_calls()`（在循环内部但不在循环体逻辑中），遵循 Constitution IX
- **符合 Constitution IV**：继承是 Python 惯用的特殊化手段，比裸参数组合更能表达"这是一种特殊的 Agent"的语义

**Alternatives considered**:
- 裸 `Agent` + 参数组合（上一版方案）：配置散落在 `spawn_subagent()` 中，提醒注入需要 hook → unregister 缺失 → 额外复杂度
- `Agent` ABC/接口：引入全新架构风格（项目当前无 ABC 模式），`Agent` 变抽象类 → 现有所有 `Agent()` 调用全部 break → 违反 Constitution VIII
- `SubAgent(Agent)` 子类：✅ 最终选择 — 改动量小，语义清晰，一举解决配置内聚 + 轮数跟踪 + U1（hook unregister）三个问题

## Decision 2: 工具过滤方式 — `tool_filter` 参数

**Decision**: 给 `Agent.__init__()` 增加可选参数 `tool_filter: set[str] | None = None`。在 `run()` 方法中 schemas 准备阶段（循环外）过滤。`SubAgent.__init__()` 固定传入 `tool_filter={"task", "todo_write"}`。

**Rationale**: 
- 遵循 Constitution IX — 过滤逻辑在循环外，循环体不增一行
- `tool_filter` 以 set 传入需排除的工具名，语义清晰
- 默认值 `None` 完全向后兼容
- 同一个 `ToolExecutor` 实例 → permission session 自然共享

**Alternatives considered**:
- 新建独立 `ToolExecutor`：需访问私有属性，permission session 行为不确定
- `ToolRegistry.exclude()`：增加 API 面

## Decision 3: Permission Session 共享

**Decision**: Sub-Agent 与主 Agent 共享同一个 `ToolExecutor` 实例。Permission engine 在 `ToolExecutor` 构造时绑定在 PreToolUse hook 链上，共享 executor 即共享 permission session。

**Rationale**:
- Session 级 ALLOW/DENY 规则存储在 permission engine 内部
- 主 Agent 和 Sub-Agent 调用同一 executor → 同一 permission engine → 自然共享
- Sub-Agent 的工具调用走 `executor.execute()` → `trigger_hooks("PreToolUse", ...)` → 审批流程完全一致

**Alternatives considered**:
- 独立 executor + 克隆 engine：额外开销，违反 spec Clarification Q2
- 独立 executor + 无权限：安全隐患，违反 FR-006

## Decision 4: 终止策略 — `_execute_tool_calls()` 覆盖

**Decision**: 在 `SubAgent._execute_tool_calls()` 中覆盖父类方法。每轮调用 `self._round += 1`。当 `self._round == 30` 时，在父类执行工具调用后向消息列表注入提醒。若第 30 轮后 Sub-Agent 仍未停止（LLM 持续返回 tool_calls），`max_steps=30` 硬截断循环，返回最后一条消息的 content。

**Rationale**:
- **零 hook 依赖**：不需要 register/unregister，避免 U1 问题
- **时机精确**：`_execute_tool_calls()` 每轮恰好调用一次（LLM 返回 tool_calls 时），计数器天然等于"Sub-Agent 调用工具的轮数"
- **提醒在调用后注入**：第 30 轮工具调用完成 → 注入提醒消息 → LLM 看到提醒 → 应返回文本 → 正常终止。若 LLM 仍返回 tool_calls → `max_steps=30` 硬截断兜底
- `self._round` 是实例变量，每次 `SubAgent()` 新建 → 天然隔离

**Alternatives considered**:
- `PreLLMCall` hook：需要 register + unregister，当前 hooks.py 无 unregister
- 提示词限制 LLM 行为：LLM 无法精确数轮数，不可作为安全边界

## Decision 5: 输出格式差异化 — `print_handler` 参数 + `agent/utils.py`

**Decision**: 给 `Agent` 增加 `print_handler` 参数。所有打印回调（`default_print_handler`、`sub_print_handler`、`_extract_key_param`）统一放入 `agent/utils.py`。`SubAgent.__init__()` 默认传入 `print_handler=sub_print_handler`。

**Rationale**:
- 遵循 Constitution IX — 替换已有 `print()` 调用，循环体不增代码
- 回调放 `agent/utils.py` 保持了依赖方向：`tools/task.py` → `agent/agent.py` → `agent/utils.py`。不存在 `agent/` → `tools/` 的反向依赖
- `[Subagent spawned]` / `[Subagent done]` 标记在 `spawn_subagent()` 中直接 print（属于组装层的生命周期标记，非工具调用输出）

**Alternatives considered**:
- 回调放 `tools/task.py`：`SubAgent`（在 `agent/agent.py`）会反向依赖 `tools/` → 违反分层
- Hook 方式（PostToolUse）：主 Agent 仍输出自己的 print → 双重输出

## Decision 6: Sub-Agent SYSTEM Prompt 设计

**Decision**: 独立编写 `SUB_SYSTEM_PROMPT` 常量，放 `agent/prompts.py`。包含三段：
1. 身份声明 — "你是一个子代理（Sub-Agent）"
2. 行为准则 — "直接使用工具完成任务，不要委派给其他代理"
3. 输出要求 — "返回结论性结果"

不继承主 Agent 的通用行为准则或 TodoWrite 工作流。

**Rationale**: Sub-Agent 的职责是"执行"而非"规划/决策"。精简 prompt 让 LLM 更快进入执行模式。

## Decision 7: Agent Loop 不碰原则

**Decision**: `Agent.run()` 的 Think→Act→Observe 循环体不增删任何一行代码。

**Rationale**: 遵循 Constitution IX。`tool_filter` 在循环前过滤 schemas；`print_handler` 替换循环内已有的 `print()`。`SubAgent` 覆盖 `_execute_tool_calls()` — 该方法在循环体内部被调用，但其自身不是循环逻辑，只是工具执行的具体步骤。

## Decision 8: 打印回调位置 — `agent/utils.py`

**Decision**: 将所有打印回调（`default_print_handler`、`sub_print_handler`、`_extract_key_param`）放入新文件 `agent/utils.py`。

**Rationale**:
- 与 `Agent` 类同层，`SubAgent` 可以直接 `from agent.utils import sub_print_handler`
- 依赖方向保持 `tools/ → agent/`，不反向
- `utils.py` 语义中性——它是 agent 层共用的工具函数，不属于任何特定类
- 未来其他 agent 层的打印需求（如 JSON 日志 handler）也可以放在这里
