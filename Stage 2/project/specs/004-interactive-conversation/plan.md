# Implementation Plan: 交互式对话

**Branch**: `004-interactive-conversation` | **Date**: 2026-07-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/004-interactive-conversation/spec.md`

## Summary

为 myAgent 添加多轮交互对话能力（REPL 外循环 + 跨轮消息保持）和 Agent 反问工具（ask_user），将系统从"单次任务执行"升级为"持续对话协作"。核心设计：`Agent.run()` 改为接收 messages 列表（由调用方负责组装），`Conversation` 类负责多轮状态下 messages 的组装和生命周期管理，新增 AskUserTool，更新 System Prompt。

## Technical Context

**Language/Version**: Python 3.12+
**Primary Dependencies**: openai (已有), 无新增依赖
**Storage**: 无持久化（v1 不实现 session save/resume）
**Testing**: pytest (已有)
**Target Platform**: Windows/Linux CLI
**Project Type**: CLI agent 应用
**Performance Goals**: 单轮响应延迟不因跨轮而增加（与现有单次模式持平）
**Constraints**: 不修改 Agent.run() 核心循环体（Constitution IX）；`run()` 签名从 `run(user_input: str)` 改为 `run(messages: list[dict])`——调用方负责组装 messages
**Scale/Scope**: 单用户终端会话，10+ 轮连续对话

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Correctness First | ✅ | 先实现正确路径（REPL + ask_user），再处理边界（Ctrl+C、compact、空输入） |
| II | Small Steps | ✅ | P1（REPL）和 P2（ask_user）独立可交付，每步可单独测试 |
| III | Clarity & Maintainability | ✅ | Conversation 类单一职责（外循环管理），Agent 循环体不动 |
| IV | Good Architecture | ✅ | 遵循现有分层：agent/ 层 + tools/ 层 + main.py 组装 |
| V | Don't Reinvent | ✅ | REPL 循环和 ask_user 模式均来自 Claude Code 验证方案 |
| VI | Mainstream Practices | ✅ | REPL + Tool-as-interaction 为业界标准模式 |
| VII | Unit Tests | ✅ | Conversation、AskUserTool、Agent 续接均独立可测 |
| VIII | Backward Compatibility | ⚠️ | `run()` 签名从 `(user_input: str)` 改为 `(messages: list[dict])`。影响面：SubAgent、TaskTool、main.py、测试——均为机械性改动（将输入包装为 messages 列表）。Constitution 允许合理 breaking change，在此讨论并记录 |
| IX | Keep Agent Loop Simple | ✅ | 循环体零改动——外循环在 Conversation 中，反问在工具层 |
| X | Elevate Design | ✅ | Conversation 类满足准则 1（概念内聚）+ 准则 3（主流程纯洁）；SubAgent 继续复用

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
# 现有结构（仅列出本次涉及的改动文件）
Stage 2/project/
├── main.py                     # [修改] 组装并启动 Conversation（替代硬编码 agent.run()）
├── agent/
│   ├── agent.py                # [修改] run() 签名改为 run(messages: list[dict])，循环体不变
│   ├── conversation.py         # [新增] Conversation 类：外循环 + messages 组装
│   └── prompts.py              # [修改] SYSTEM_PROMPT 新增"反问规则"小节
├── tools/
│   ├── ask_user.py              # [新增] AskUserTool：Agent 反问工具
│   ├── task.py                  # [修改] spawn_subagent() 适配新的 run(messages) 签名
│   └── __init__.py              # [修改] register_all() 加入 AskUserTool
├── tooling/
│   └── permission/policy.py     # [修改] 新增 ask_user 的 allow 规则
└── tests/
    ├── test_conversation.py     # [新增] Conversation 类测试
    ├── test_ask_user.py         # [新增] AskUserTool 测试
    ├── test_task.py             # [修改] 适配 run(messages) 签名
    └── test_todo_write.py       # [修改] 适配 run(messages) 签名（如有直接调用）
```

**Structure Decision**: 单项目结构。Conversation 放在 `agent/` 目录下（属于 agent 编排层），AskUserTool 放在 `tools/` 目录下（属于工具层），遵循现有分层约定。

## Detailed Design

### 核心设计原则

`Agent.run()` 改为接收 messages 列表——**谁调用谁负责组装 messages**。这是一个纯粹的数据流变化：循环体一字不改，只是 messages 的来源从"方法内部创建"变为"调用方传入"。

```
之前: agent.run(user_input)           → Agent 内部: 创建 messages → 循环 → 返回
之后: agent.run(messages)             → 调用方: 组装 messages → Agent: 循环 → 返回
   └─ Conversation 负责组装          └─ messages 被原地修改，调用方可继续使用
```

---

### 1. Agent.run() 签名变更

**文件**: `agent/agent.py`

```python
# agent/agent.py
class Agent:
    def run(self, messages: list[dict]) -> str:
        """在已有 messages 上执行 Think→Act→Observe 循环。
        
        messages 必须已包含 system prompt（如需要）和最新的 user 消息。
        循环体会原地修改 messages（追加 assistant 和 tool 消息）。
        """
        trigger_hooks("UserPromptSubmit",
                      messages[-1]["content"] if messages else "")

        for _ in range(self.max_steps):
            # ── 以下循环体一字不改 ──
            inject = trigger_hooks("PreLLMCall")
            if inject:
                messages.extend(inject["messages"])

            compact_pipeline(messages, self.llm)

            schemas = self.executor.get_schemas()
            if self.tool_filter:
                schemas = [s for s in schemas
                           if s["function"]["name"] not in self.tool_filter]

            stop_reason, msg = self.llm.chat(messages, schemas)

            if stop_reason == "tool_calls":
                messages.append(msg)
                self._execute_tool_calls(msg.tool_calls, messages)

            tool_calls = msg.tool_calls if stop_reason == "tool_calls" else None
            trigger_hooks("PostRound", stop_reason, tool_calls)

            if stop_reason != "tool_calls":
                trigger_hooks("PreAgentStop", messages)
                return msg.content or "（模型未返回文本）"

        trigger_hooks("PreAgentStop", messages)
        return "Agent 已停止：达到最大步数限制。"
```

**与现在的差异**：

| | 现在 | 改后 |
|---|------|------|
| 签名 | `run(self, user_input: str)` | `run(self, messages: list[dict])` |
| 前 3 行 | 创建 `messages: list[dict] = []` + 插入 system + 插入 user | 移除——调用方负责 |
| 循环体 | 不变 | **一字不改** |

**变动行数**: 约 3 行删除 + 1 行签名修改。

---

### 2. 调用方适配

`run()` 签名变了，所有调用方需要机械性地将 user_input 包装为 messages 列表。

#### 2a. Conversation（新调用方）

**文件**: `agent/conversation.py`（新增）

```python
class Conversation:
    """多轮对话编排器。拥有 messages 生命周期，负责组装并传给 Agent.run()。"""

    def __init__(self, agent: Agent):
        self.agent = agent
        self.messages: list[dict] = []
        self._interrupted_once = False

    def start(self) -> None:
        """主 REPL 循环。"""
        trigger_hooks("SessionStart")
        print("🤖 myAgent 已启动。输入 /exit 退出，Ctrl+C 中断当前操作。\n")

        while True:
            try:
                user_input = input("👤 你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 再见！")
                break

            if user_input.lower() in ("/exit", "/quit", "exit"):
                print("👋 再见！")
                break
            if not user_input:
                continue

            try:
                self._run_turn(user_input)
                self._interrupted_once = False
            except KeyboardInterrupt:
                print("\n⚠️ 已中断当前操作。")
                if self._interrupted_once:
                    print("再次中断，退出程序。")
                    break
                self._interrupted_once = True
            except Exception as exc:
                # API 错误等——不崩溃，提示后继续
                print(f"\n❌ 发生错误: {exc}")
                print("请重试或输入 /exit 退出。")

    def _run_turn(self, user_input: str) -> None:
        """组装 messages 后调用 Agent.run()。"""
        # 首轮：插入 system prompt
        if not self.messages:
            self.messages.append({
                "role": "system",
                "content": self.agent.system_prompt,
            })

        # 追加用户输入
        self.messages.append({"role": "user", "content": user_input})

        # Agent 在 self.messages 上原地执行，循环体自动追加 assistant + tool 消息
        answer = self.agent.run(self.messages)
        print(f"\n🤖 MyAgent: {answer}\n")
```

**设计要点**:
- `messages` 为空列表 → 自动插入 system prompt，无需 `_first_turn` 标记
- API 异常 (`except Exception`) 有兜底——不崩溃，提示用户后继续
- `SessionStart` hook 在 `start()` 中触发

#### 2b. SubAgent（现有调用方）

**文件**: `agent/agent.py` — SubAgent 继承 `run()`，签名自动跟随父类。

SubAgent 本身不需要改。需要改的是调用 SubAgent 的地方。

**文件**: `tools/task.py` — `spawn_subagent()`

```python
# 之前
def spawn_subagent(description: str, llm, executor) -> str:
    sub = SubAgent(llm=llm, executor=executor)
    result = sub.run(description)                    # str → str

# 改后
def spawn_subagent(description: str, llm, executor) -> str:
    sub = SubAgent(llm=llm, executor=executor)
    messages = [
        {"role": "system", "content": sub.system_prompt},
        {"role": "user", "content": description},
    ]
    result = sub.run(messages)                       # list[dict] → str
```

**文件**: `tests/test_task.py` — 测试中创建 SubAgent 的地方同样适配。

#### 2c. main.py 单次模式

```python
# 之前
question = "给我详细调研西兰花怎么做好吃"
answer = agent.run(question)

# 改后（如果仍需单次模式）
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": question},
]
answer = agent.run(messages)

# 推荐模式：直接启动 REPL
conv = Conversation(agent)
conv.start()
```

**文件**: `tests/test_todo_write.py`, `tests/test_task.py` — 所有 `agent.run("input")` 改为 `agent.run([system_msg, user_msg])`。

---

### 3. Conversation 类

完整代码见 §2a。这里记录关键设计决策：

| 决策 | 选择 | 理由 |
|------|------|------|
| system prompt 插入 | `if not self.messages:` 判断空列表 | 比 `_first_turn` 标记更自然——messages 为空 = 新会话 |
| API 异常处理 | `except Exception` 在 `_run_turn()` 层级 | 网络/限流/服务端错误不导致 REPL 崩溃 |
| `/exit` 拦截 | 在 `start()` 主循环中，不送入 Agent | 元命令不属于对话内容 |
| Ctrl+C 行为 | 第一次中断当前轮 → 第二次退出 | 符合 spec Edge Case |
| `SessionStart` hook | 在 `start()` 中触发 | 替代 main.py 中的调用 |

---

### 4. AskUserTool — 反问工具

**文件**: `tools/ask_user.py`（新增）

继承 `Tool` ABC，遵循现有工具模式。参见 [research.md](./research.md) §4 的完整伪代码。

**关键设计点**:
- `run()` 调用 `input()` 阻塞等待用户回答
- 返回 `{"answer": "..."}` 作为 tool result
- `description` 包含使用场景引导（LLM 通过 function description 理解何时调用）
- 需在 `permission/policy.py` 中添加 allow 规则，避免"批准提问"的荒谬场景

---

### 5. System Prompt 重设计

**文件**: `agent/prompts.py`

不采用"追加新小节"，而是整体重写行为准则。核心变化：

- "行为准则"拆为三个子节：**工具使用**（保留原有规则）、**自主决策 vs 反问用户**（新增决策框架）、**提问规范**（新增操作规范）
- 删除"不要过分收集信息，有了可以初步回答查询的信息即可回答"——这条与反问功能存在根本冲突
- 人格从"智能助手"→"智能协作者"
- 新增并发约束："如需反问，不要在同一轮混入其他工具调用"

完整 prompt 已直接写入 `agent/prompts.py`。SUB_SYSTEM_PROMPT 不变。

---

### 6. 权限规则更新

**文件**: `tooling/permission/policy.py`

在 `_SAFE_TOOLS` 列表中加入 `"ask_user"`：

```python
_SAFE_TOOLS = ["calculator", "get_current_time", "read_chunk",
               "search_knowledge", "web_search", "ask_user"]
```

无需额外 PermissionRule——现有的 `_SAFE_TOOLS` 循环已自动生成 ALLOW 规则。

---

### 7. main.py

**文件**: `main.py`

```python
if __name__ == "__main__":
    llm = LLMClient(llm_cfg.api_key, llm_cfg.base_url, llm_cfg.model)
    executor = build_tool_executor(project_root=WORKDIR)
    register_all(executor, include_dangerous=True, workdir=WORKDIR, llm=llm)
    register_todo_hooks()

    agent = Agent(llm, executor, system_prompt=SYSTEM_PROMPT, max_steps=50)

    conv = Conversation(agent)
    conv.start()
```

### 8. 修复确认的问题

本次调查中发现的 4 个问题在此方案中的处理：

| 问题 | 状态 | 说明 |
|------|------|------|
| UserPromptSubmit 双重触发 | ✅ **自动消除** | 不存在 `continue_from`，hook 只在 `run()` 中触发一次 |
| API 异常崩溃 | ✅ **已修复** | `Conversation._run_turn()` 中 `except Exception` 兜底 |
| L3 compact 跨轮误操作 | ⚠️ **低优先级** | 幂等操作，不产生错误结果。可在后续迭代优化 |
| max_steps 截断消息不一致 | ⚠️ **低优先级** | 仅在极端场景触发（连续 50 步工具调用无文本输出） |

## Complexity Tracking

> 无 Constitution 违规，无需记录。
