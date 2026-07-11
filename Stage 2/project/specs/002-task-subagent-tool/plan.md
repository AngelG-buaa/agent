# Implementation Plan: Task Tool & Sub-Agent

**Branch**: `002-task-subagent-tool` | **Date**: 2026-07-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-task-subagent-tool/spec.md`

## Summary

为 Agent 新增 `task` 工具和 Sub-Agent 机制。当主 Agent 面对可独立完成的复杂子任务时，通过 `task` 工具创建 `SubAgent` 实例（拥有全新的消息列表和独立的执行循环），Sub-Agent 完成子任务后仅返回最终文本结论，中间步骤不出现在主 Agent 上下文中。核心价值是**上下文隔离**。

技术方案：`Agent` 类增加 `tool_filter` 和 `print_handler` 两个可选参数（不改循环体）。新增 `SubAgent(Agent)` 子类，在 `_execute_tool_calls()` 中叠加轮数跟踪和提醒注入逻辑。打印回调统一放入 `agent/utils.py`，与 Agent 同层，保持依赖方向正确。

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: openai SDK（LLM 客户端）、现有项目依赖（无新增第三方库）

**Storage**: N/A（纯内存操作，Sub-Agent 消息列表随函数返回而丢弃）

**Testing**: pytest（现有 `tests/` 目录，`tests/test_task.py` 为新增测试文件）

**Target Platform**: 跨平台 CLI（Windows/Linux/macOS）

**Project Type**: CLI application

**Performance Goals**: 无硬性指标（功能正确性优先于性能，per constitution I）

**Constraints**: Sub-Agent 最大 30 轮执行限制、V1 同步执行（主 Agent 阻塞等待）、通过子类而非修改基类循环来实现扩展

**Scale/Scope**: 单用户 CLI 工具，11 个现有工具 + 1 个新 `task` 工具

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 1 | 先正确再优化？ | ✅ | 无猜测性优化，`SubAgent` 直接复用 `Agent.run()` 循环 |
| 2 | 保持现有架构分层？ | ✅ | `agent/utils.py` + `SubAgent` 在 agent 层，`tools/task.py` 在工具层。依赖方向：`tools/task.py` → `agent/`，不反向 |
| 3 | 保持现有代码风格？ | ✅ | `SubAgent` 是标准 Python 子类，type hints、docstring 与现有一致 |
| 4 | 无轮子被重复发明？ | ✅ | 复用 `Agent`、`ToolExecutor`、`Tool` 基类。子类是最简单的特殊化手段 |
| 5 | 与主流做法一致？ | ✅ | 与 learn-claude-code s06 教程一致。`SubAgent` 子类也比裸参数组合更直观 |
| 6 | 核心模块有测试计划？ | ✅ | `SubAgent`、`TaskTool`、`spawn_subagent()` 纳入测试 |
| 7 | 无不必要的 breaking change？ | ✅ | `tool_filter`/`print_handler` 有默认值；`SubAgent` 是新增类；现有 `Agent` 行为完全不变 |
| 8 | **IX: Agent Loop 简洁性？** | ✅ | `run()` 循环体 0 行改动。`tool_filter` 在循环外，`print_handler` 替换已有 `print()`。`SubAgent._execute_tool_calls()` 覆盖父类方法（叠加行为），不改循环本身 |

**Gate Result**: ✅ 8/8 ALL PASS — 进入 Phase 0

---

## Design Details

### 整体架构

```
agent/
├── agent.py          # Agent (基类) + SubAgent(Agent) 子类
│                     #   Agent: +tool_filter, +print_handler 参数
│                     #   SubAgent: 封装配置默认值 + 轮数跟踪 + 提醒注入
├── utils.py          # ★ NEW: _default_print_handler, _sub_print_handler, _extract_key_param
├── prompts.py        # + SUB_SYSTEM_PROMPT 常量
├── ...

tools/
├── task.py           # ★ NEW: TaskTool + spawn_subagent()（极薄组装层）
├── ...
```

依赖方向：`tools/task.py` → `agent/agent.py` → `agent/utils.py` → `agent/prompts.py`。全部向下。

### 1. `Agent` 基类新增参数（不改循环体）

```python
# agent/agent.py

class Agent:
    def __init__(self, llm, executor, system_prompt=None,
                 max_steps=10,
                 tool_filter: set[str] | None = None,      # NEW
                 print_handler: Callable | None = None):     # NEW
        ...
        self.tool_filter = tool_filter
        self.print_handler = print_handler or default_print_handler

    def run(self, user_input):
        ...
        schemas = self.executor.get_schemas()
        if self.tool_filter:                                # 循环外
            schemas = [s for s in schemas
                       if s["function"]["name"] not in self.tool_filter]
        for _ in range(self.max_steps):                     # 循环体不变
            stop_reason, msg = self.llm.chat(messages, schemas)
            ...
            if stop_reason == "tool_calls":
                messages.append(msg)
                self._execute_tool_calls(msg.tool_calls, messages)
            ...

    def _execute_tool_calls(self, tool_calls, messages):
        for tc in tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            self.print_handler(name, args)                  # 原 print() → 回调
            ...
```

### 2. `agent/utils.py` — 打印回调（新文件，与 Agent 同层）

```python
# agent/utils.py

def default_print_handler(name: str, args: dict) -> None:
    """主 Agent 格式：🔧 调用工具: name({args})"""
    print(f"  🔧 调用工具: {name}({args})")


def sub_print_handler(name: str, args: dict) -> None:
    """Sub-Agent 精简格式：[sub] name(key_param)"""
    summary = _extract_key_param(name, args)
    print(f"  [sub] {name}({summary})")


def _extract_key_param(name: str, args: dict) -> str:
    if name == "bash":
        cmd = str(args.get("command", ""))
        return cmd[:60] + ("..." if len(cmd) > 60 else "")
    if name in ("read_file", "write_file", "edit_file", "read_chunk"):
        return str(args.get("file_path", args.get("path", "?")))
    if name == "glob":
        return str(args.get("pattern", "?"))
    if name in ("web_search", "search_knowledge"):
        return str(args.get("query", "?"))[:60]
    if name == "web_fetch":
        return str(args.get("url", "?"))[:60]
    return ""
```

### 3. `SubAgent(Agent)` — 配置内聚 + 轮数跟踪

```python
# agent/agent.py — 新增子类

class SubAgent(Agent):
    """子代理：独立上下文、受限工具集、30 轮限制、精简输出。"""

    def __init__(self, llm, executor):
        super().__init__(
            llm=llm,
            executor=executor,
            system_prompt=SUB_SYSTEM_PROMPT,
            max_steps=30,
            tool_filter={"task", "todo_write"},
            print_handler=sub_print_handler,
        )
        self._round = 0

    def _execute_tool_calls(self, tool_calls, messages):
        """覆盖父类：跟踪轮数，第 30 轮后注入提醒。"""
        self._round += 1
        if self._round == 30:
            messages.append({
                "role": "user",
                "content": "你已达到最大轮数限制，请基于已有信息给出当前最佳结论。"
            })
        super()._execute_tool_calls(tool_calls, messages)
```

设计要点：

- **配置内聚**：`SubAgent.__init__()` 集中所有默认值，调用方只需传 `llm` + `executor`
- **轮数跟踪**：`self._round` 实例变量，天然隔离（每次 `SubAgent()` 创建都是新的）
- **提醒注入**：覆盖 `_execute_tool_calls()`，第 30 轮 LLM 调用结束后注入提醒。此时 `max_steps=30`，下一轮（第 31 次循环）不会执行 — Agent 拿到提醒后应直接给文本回复，不进工具调用分支
- **零 hook 依赖**：不注册/注销任何 hook，状态全在实例内部
- **不碰 `run()`**：循环体完全继承

### 4. `tools/task.py` — 极薄组装层

```python
# tools/task.py

from agent.agent import SubAgent
from agent.prompts import SUB_SYSTEM_PROMPT

class TaskTool(Tool):
    """task 工具：委派子任务给 Sub-Agent 执行。"""
    def __init__(self):
        super().__init__(name="task", description=...)

    def get_parameters(self):
        return [ToolParameter("description", "string", "子任务描述", required=True)]

    def run(self, parameters: dict) -> dict:
        description = parameters.get("description", "").strip()
        if not description:
            return {"error": "description is required"}
        try:
            result = spawn_subagent(description, self._main_agent)
            return {"result": result}
        except Exception as e:
            return {"error": str(e)}


def spawn_subagent(description: str, main_agent: Agent) -> str:
    """创建 SubAgent，同步执行，仅返回最终文本结论。"""
    print(f"\n[Subagent spawned] {description[:100]}")

    sub = SubAgent(llm=main_agent.llm, executor=main_agent.executor)
    result = sub.run(description)

    print(f"[Subagent done]")
    return result
```

`TaskTool` 需要 `main_agent` 引用才能访问 `llm` 和 `executor`。当前 `Tool.run()` 不接收 Agent 引用。解决方案：在 `register_all()` 之后调用 `TaskTool.set_agent(agent)`，或在 `executor.execute()` 中注入。具体实现见 tasks.md。

### 5. 改动点汇总

| 文件 | 改动 | 行数 |
|------|------|------|
| `agent/agent.py` | `Agent.__init__` +2 参数（`tool_filter`、`print_handler`） | ~3 行 |
| `agent/agent.py` | `Agent.run()` 循环外加 `tool_filter` 过滤 | ~3 行 |
| `agent/agent.py` | `Agent._execute_tool_calls()` 中 `print()` → `self.print_handler()` | 1 行 |
| `agent/agent.py` | `SubAgent(Agent)` 子类（`__init__` + `_execute_tool_calls` 覆盖） | ~25 行 |
| `agent/utils.py` | **新文件**：`default_print_handler` + `sub_print_handler` + `_extract_key_param` | ~30 行 |
| `agent/prompts.py` | + `SUB_SYSTEM_PROMPT` 常量 | ~10 行 |
| `tools/task.py` | **新文件**：`TaskTool` + `spawn_subagent()`（无打印回调） | ~50 行 |
| `tools/__init__.py` | 注册 `TaskTool` | +1 行 |
| `tests/test_task.py` | **新文件**：`SubAgent` + `TaskTool` + 端到端测试 | ~100 行 |

**Agent.run() 循环体改动：0 行。** 基类改动全部在循环外部或替换已有调用。`SubAgent` 覆盖 `_execute_tool_calls()`（不在循环体内）。

## Project Structure

### Documentation (this feature)

```text
specs/002-task-subagent-tool/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
Stage 2/project/
├── agent/
│   ├── agent.py          # Agent +2 参数 (~7行) + SubAgent 子类 (~25行)
│   ├── utils.py          # ★ NEW: print 回调函数 (~30行)
│   ├── llm_client.py     # 无修改
│   ├── message_utils.py  # 无修改
│   └── prompts.py        # + SUB_SYSTEM_PROMPT
├── tools/
│   ├── __init__.py       # register_all() 注册 TaskTool (+1行)
│   ├── task.py           # ★ NEW: TaskTool + spawn_subagent()
│   └── ...               # 现有工具全部无修改
├── tooling/
│   ├── base.py           # 无修改
│   ├── registry.py       # 无修改
│   ├── executor.py       # 无修改 (可能需要参数透传)
│   └── permission/       # 无修改
├── hooks.py              # 无修改
├── main.py               # 无修改
├── config.py             # 无修改
└── tests/
    ├── test_todo_write.py # 现有测试
    └── test_task.py       # ★ NEW
```

**Structure Decision**: `SubAgent` 作为 `Agent` 的子类放在 `agent/agent.py` 中（紧邻基类，便于理解继承关系）。打印回调放入 `agent/utils.py`（与 Agent 同层，`SubAgent` 直接 import）。`tools/task.py` 仅做组装——`TaskTool` + `spawn_subagent()`，不包含打印逻辑。

## Complexity Tracking

> 无 Constitution Check 违规项。

---

## Phase 0: Research Summary

详见 [research.md](./research.md)

| # | 决策 | 选择 |
|---|------|------|
| 1 | Sub-Agent 实现方式 | `SubAgent(Agent)` 子类——配置内聚 + 轮数跟踪 + 提醒注入，封装在一个类中 |
| 2 | 工具过滤方式 | `tool_filter: set[str]` 构造参数，`run()` 循环外过滤 |
| 3 | Permission 共享 | 共享同一 `ToolExecutor` 实例 |
| 4 | 终止策略 | `SubAgent._execute_tool_calls()` 覆盖，第 30 轮后注入提醒 |
| 5 | 输出格式 | `print_handler` 回调，回调函数放 `agent/utils.py` |
| 6 | SYSTEM Prompt | `SUB_SYSTEM_PROMPT` 常量，`SubAgent.__init__()` 默认设置 |
| 7 | Agent Loop 保护 | Constitution IX — 基类 `run()` 循环体 0 行改动 |

---

## Phase 1: Design Artifacts

- **data-model.md**: [data-model.md](./data-model.md) — 更新为 SubAgent 子类实体
- **quickstart.md**: [quickstart.md](./quickstart.md) — 5 个端到端验证场景
- **contracts/**: 无外部接口，跳过
