# Implementation Plan: Session 持久化（结构重构版）

**Branch**: `007-session-persistence` | **Date**: 2026-07-14 | **Spec**: [spec.md](./spec.md)

**Design Basis**: [architecture-refactor-plan.md](./architecture-refactor-plan.md) —— 若冲突，以后者为准。

---

## 1. 根因分析与优先级

初步实现有三个独立的正确性缺陷，按严重程度排序：

| 优先级 | 问题 | 调用链 | 影响 |
|:--:|------|------|------|
| **P0** | `_emit_message` 同时调用 `on_message(msg)` 和 `messages.append(msg)`，而 `messages` 就是 `active.messages` 同一个引用 → 每条 assistant/tool 消息在内存中追加两次 | `Agent._emit_message()` → `Controller.append_message()` → `active.messages.append()` → Agent 再次 `messages.append()` | SQLite 正确（一条），working context 重复（两条）→ 浪费 token、LLM 看到重复历史、resume 后上下文不一致 |
| **P0** | final assistant 在 `Agent.run()` 直接 `return msg.content`，未经过 `_emit_message()` | `Agent.run():124` → `return msg.content` | **最终答案完全不持久化**。DB 无记录，working context 无记录。Resume 后缺失整条最终回复 |
| **P1** | 创建 session 时 system message 是空占位符，首轮 `_run_turn()` 的注入只替换内存不持久化（`append_message` 对 role="system" 直接 return） | `start_new()` → `create_session({"role":"system","content":""})` → 首轮 `append_message({"role":"system",...})` → 只改 `active.messages[0]` 后 return | DB 永久为空 system prompt。恢复已有多条消息的 session 时，`_run_turn()` 占位符判断不成立，**模型在无真实 system prompt 的上下文中运行** |
| **P2** | 测试未覆盖真实持久化链路 | `_FakeAgent.run()` 缺少 `on_message` 参数；集成测试直接调 `mgr.append_message()` | P0/P1 问题全部漏过 |
| **P3** | Controller.close() 不注销 grant listener 和 Todo persistence Hook | `close()` 只做 `cleanup_if_empty` + `active = None` | 悬垂引用；重复创建 Conversation 时 Hook 累积 |

**注意**: 经过验证，以下清理在现有流程中正常工作——
- `Conversation.start()` 的 `finally` 确实调用 `todo_handle.dispose()`（reminder Hook 正常退出时注销）
- `start_new()` 和 `resume()` 确实调用 `replace_session_rules()` 和 `replace_todos()`（权限和 Todo 在新建/切换时正确替换）
- `/resume` 切换不重新创建 Conversation（单次进程内无 Hook 累积）

因此 close() 改进的重点是 **grant listener** 和 **Todo persistence Hook** 两个确实未管理的资源。

---

## 2. 实施顺序

按"先修正确性 → 再补测试 → 最后重构结构"原则：

| Step | 类型 | 内容 |
|:--:|------|------|
| **S1** | 🔴 正确性 | 修复 `_emit_message` sink 语义：`on_message` 是完整 sink，Agent 不再自己 append |
| **S2** | 🔴 正确性 | 持久化 final assistant：`Agent.run()` 退出前经 `_emit_message` 发射最终消息 |
| **S3** | 🔴 正确性 | 创建时写入真实 system prompt：`start_new()` 传入真实 prompt，移除 `_run_turn()` 的首轮注入补丁 |
| **S4** | 🟡 测试 | 新增真实持久化链路集成测试：覆盖 `Agent.run → on_message → Controller → Repository` |
| **S5** | 🟢 重构 | 合并 `normalize_message()` 到 `agent/utils.py` |
| **S6** | 🟢 重构 | `register_hook()` 返回 disposer；`TodoReminderHandle` 不访问全局 `HOOKS` |
| **S7** | 🟢 重构 | 新建 `agent/session_controller.py`；Controller.close() 完整释放 |
| **S8** | 🟢 重构 | 重写 Conversation 启动与切换菜单（循环式）；清理 `main.py`；清理 `session_manager.py` |

---

## 3. Technical Context

- **Language**: Python 3.12+
- **Storage**: SQLite（每 session 一个 `.db`，包含 4 张表）
- **Dependencies**: 标准库 `sqlite3`、`uuid`、`contextlib`、`dataclasses`（零新增）
- **Tests**: pytest
- **Constraints**: 不改 Agent Think-Act-Observe 循环结构、不改 Schema、不改 RAG/LLMClient/compact/ToolRegistry/配置系统、不改 PermissionEngine

---

## 4. Constitution Check

| # | Principle | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Correctness First | ✅ | P0 正确性修复在最前面，重构在最后 |
| 2 | Small Steps | ✅ | 8 步，每步独立可验证 |
| 3 | Clarity & Maintainability | ✅ | 按职责和变化原因拆分 |
| 4 | Good Architecture | ✅ | 单向依赖，disposer 模式 |
| 5 | Don't Reinvent the Wheel | ✅ | 标准库 |
| 6 | Mainstream Practices | ✅ | Repository、显式依赖注入和 disposer 均采用社区通行模式 |
| 7 | Core Module Tests | ✅ | Phase 3 增加消息 sink、final assistant 和真实 system prompt 三条核心路径集成测试 |
| 8 | Backward Compatibility | ✅ | `Conversation(agent)` 仍有效 |
| 9 | Keep Agent Loop Simple | ✅ | 修改限于消息出口点，循环体结构不变 |
| 10 | Recognize When to Elevate Design | ✅ | S7 将协调 active、权限和 Todo 的 Controller 拆为独立模块 |

---

## 5. Step-by-Step Implementation

### S1: 修复 `_emit_message` sink 语义

**问题**: 当前 `_emit_message` 同时调用 `on_message(msg)` 和 `messages.append(msg)`。在持久化模式下，`messages` 就是 `active.messages`（同一个 list 引用），`on_message` 内部已执行 `active.messages.append(msg)`，Agent 的第二次 `messages.append(msg)` 导致同一条消息在内存中出现两次。

```text
持久化模式:
  _emit_message(msg, messages, on_message=Controller.append_message)
    → on_message(msg)
      → Controller.append_message(msg)
        → self._mgr.append_message(id, msg)     # SQLite: 一条 ✓
        → self.active.messages.append(msg)      # 内存: 第一次追加
    → messages.append(msg)                      # 内存: 第二次追加 ← 重复！
      (messages IS active.messages → 同一 list)
```

**正确语义**: `on_message` 是完整 sink——调用后 Agent 不再自己操作 messages。

```python
# agent/agent.py

def _emit_message(
    msg: dict,
    messages: list,
    on_message: Callable[[dict], None] | None,
) -> None:
    """将消息通过 sink 加入列表。

    on_message 是完整 sink：持久化模式由 Controller 完成
    「SQLite 写一次 → active.messages 追加一次」；
    Agent 不再自己 append。

    on_message 为 None 时（SubAgent / Transient），Agent 直接 append。
    """
    if on_message is not None:
        on_message(msg)        # sink 负责持久化 + 内存追加
    else:
        messages.append(msg)   # 默认：仅内存追加
```

**改动清单**:

| 文件 | 位置 | 操作 |
|------|------|------|
| `agent/agent.py` | `_emit_message()` L53-64 | 改为 `if on_message: on_message(msg) else: messages.append(msg)` |

**验证**:
```bash
python -m pytest tests/ -q -k "not test_compact"
# 预期: 全部通过。Transient 路径走 else 分支，行为不变。
# SubAgent 路径不传 on_message，行为不变。
```

---

### S2: 持久化 final assistant

**问题**: `Agent.run()` 在 `stop_reason != "tool_calls"` 时直接 `return msg.content`，最终答案未经过 `_emit_message()`。

```python
# agent/agent.py:118-126 (当前)
if stop_reason != "tool_calls":
    trigger_hooks("PreAgentStop", messages)
    return msg.content or "（模型未返回文本）"
```

**影响**:

```text
一个工具调用轮次的持久化结果（当前）:
  system:       ✅ DB (空内容)
  user:         ✅ DB + 内存
  assistant(tool_calls): ✅ DB + 内存(×2 bug)
  tool result:  ✅ DB + 内存(×2 bug)
  final assistant: ❌ 完全缺失 —— DB 无、内存无

Resume 后:
  messages = [system(空), user, assistant(tool_calls), tool result]
  ← 缺少 final assistant！
  模型看到 "调用了工具，得到了结果，然后..." —— 没有然后了
```

**修复**: 在 return 之前归一化并发射 final assistant。

```python
# agent/agent.py —— Agent.run() 中的修正

if stop_reason != "tool_calls":
    # 持久化 final assistant 消息（S1 修复后的 _emit_message 保证只追加一次）
    # S2 尚未执行 S5，此处先复用现有私有归一化函数。
    final_msg = _normalize_message(msg)
    _emit_message(final_msg, messages, on_message)
    trigger_hooks("PreAgentStop", messages)
    return msg.content or "（模型未返回文本）"
```

**注意**: `trigger_hooks("PreAgentStop", messages)` 需要在 `_emit_message` 之后调用，确保 final assistant 已在 working context 中。
S5 合并消息工具时，再将这里的 `_normalize_message(msg)` 统一替换为
`normalize_message(msg)`。

**改动清单**:

| 文件 | 位置 | 操作 |
|------|------|------|
| `agent/agent.py` | `Agent.run()` L118-126 | 在 return 前添加 `_normalize_message(msg)` + `_emit_message(...)`；S5 再切换为公共 `normalize_message()` |

**验证**:
```bash
python -m pytest tests/ -q -k "not test_compact"
# 手动: 对话一轮 → sqlite3 检查 messages 表最后一条是 role='assistant' 的最终回复
```

---

### S3: 创建时写入真实 system prompt

**问题**: 当前两条路径协作导致 system prompt 从未持久化：

```text
start_new():
  create_session({"role":"system","content":""})  → DB: system=""

首轮 _run_turn():
  append_message({"role":"system","content": REAL_PROMPT})
    → role=="system" → active.messages[0] = message → return  ← 跳过了持久化！

结果: DB 中 system content 永久为空
```

恢复已有多条消息的 session 时：

```text
resume → load_session → snap.messages = [system(""), user, assistant, ...]
→ _run_turn() 检查:
    len(self.messages) == 1 且 self.messages[0]["content"] == "" ?
    → len = 3 (有多条消息) → 条件不成立 → 不注入 system prompt
→ Agent 在无 system prompt 的上下文中运行
```

**修复思路**: 从源头消除问题——创建 session 时直接传入真实 system prompt，删除首轮注入补丁。

**S3a: SessionController 构造时接收 system_message**

```python
# agent/session_controller.py (或当前在 conversation.py 中的 SessionController)

class SessionController:
    def __init__(self, session_manager, permission_engine, todo_handle,
                 system_message: dict):  # ← 新增参数
        ...
        self._system_message = system_message

    def start_new(self) -> ActiveSession:
        ...
        session_id = self._mgr.create_session(self._system_message)
        #                                      ^^^^^^^^^^^^^^^^^^^^
        # 直接传入真实 system prompt
        ...
        self.active = ActiveSession(
            id=session_id,
            title="Untitled",
            messages=[self._system_message],  # ← 使用真实 prompt
        )
```

**S3b: 删除 `append_message` 对 system 角色的特殊处理**

```python
# 旧代码（删除）:
if role == "system":
    if self.active.messages and self.active.messages[0].get("role") == "system":
        self.active.messages[0] = message
    else:
        self.active.messages.append(message)
    return  # ← 跳过持久化
```

system message 只在创建时写入一次，不再有运行时注入的场景。`append_message` 对所有角色统一处理：

```python
def append_message(self, message: dict) -> None:
    if self.active is None:
        raise SessionPersistenceError("没有 active session")
    self._mgr.append_message(self.active.id, message)
    self.active.messages.append(message)
```

**S3c: 删除 `_run_turn` 的首轮 system prompt 注入**

```python
# agent/conversation.py —— _run_turn()

# 删除这段:
if not self.messages or (
    len(self.messages) == 1 and self.messages[0]["role"] == "system"
    and self.messages[0]["content"] == ""
):
    self._controller.append_message({...system prompt...})

# 直接追加 user 消息即可:
self._controller.append_message({"role": "user", "content": user_input})
```

**S3d: main.py 装配时传入 system_message**

```python
conv = Conversation(
    agent,
    session_manager=session_mgr,
    permission_engine=engine,
    system_message={"role": "system", "content": SYSTEM_PROMPT},
)
```

**改动清单**:

| 文件 | 操作 |
|------|------|
| `agent/conversation.py` — `SessionController.__init__` | 新增 `system_message` 参数 |
| `agent/conversation.py` — `SessionController.start_new` | `create_session(self._system_message)` |
| `agent/conversation.py` — `SessionController.append_message` | 删除 role=="system" 特殊分支；统一：先持久化再追加内存 |
| `agent/conversation.py` — `Conversation.__init__` | 接收并传递 `system_message` 给 Controller |
| `agent/conversation.py` — `_run_turn` | 删除首轮 system prompt 注入逻辑 |
| `main.py` | `Conversation()` 构造新增 `system_message={"role":"system","content":SYSTEM_PROMPT}` |

**验证**:
```bash
python -m pytest tests/ -q -k "not test_compact"
# 手动: 创建 session → 直接退出 → sqlite3:
#   SELECT content FROM messages WHERE role='system';
#   预期: 输出真实的 SYSTEM_PROMPT 内容（非空字符串）
```

---

### S4: 新增真实持久化链路集成测试

**问题**: 当前所有测试绕过 `Agent.run → on_message → Controller → Repository` 链路。

| 测试文件 | 实际覆盖 | 缺失 |
|----------|---------|------|
| `test_conversation.py` | Transient 模式（`controller is None`） | `on_message` 路径 |
| `test_session_persistence.py` | 手工 `mgr.append_message()` | Agent 真实调用 |
| `test_session_manager.py` | Repository 层 | 上层集成 |

**新增测试**: 用 mock LLM 驱动完整链路。

```python
# tests/test_session_persistence.py —— 新增

class TestRealPersistenceChain:
    """覆盖 Agent.run → on_message → Controller → Repository 完整链路。"""

    def test_full_roundtrip_via_agent_run(self, tmp_path):
        """一轮对话经过真实 Agent.run() 后，消息完整持久化且不重复。"""
        from agent.agent import Agent
        from agent.session_manager import SessionManager
        from agent.session_controller import SessionController
        from tooling.permission import PermissionEngine
        from tooling.executor import ToolExecutor
        from tools.todo_write import register_todo_hooks

        # 1. 组装真实组件
        sessions_dir = str(tmp_path / "sessions")
        mgr = SessionManager(sessions_dir=sessions_dir)
        engine = PermissionEngine(default_behavior="allow")
        executor = ToolExecutor(permission_engine=engine,
                                approver=lambda n, p, r: {"decision": "allow"})
        # 注册工具（Agent.run 需要 schemas）
        from tools import register_all
        register_all(executor, include_dangerous=False, workdir=str(tmp_path), llm=None)

        system_msg = {"role": "system", "content": "You are helpful."}
        ctrl = SessionController(mgr, engine, system_msg)
        ctrl.start_new()

        # 2. Mock LLM：一轮返回 final answer（无工具调用）
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Hello, I am Claude."
        mock_response.tool_calls = None
        mock_llm.chat.return_value = ("stop", mock_response)

        agent = Agent(mock_llm, executor, system_prompt="You are helpful.", max_steps=5)

        # 3. 注入 user 消息 + 调用 Agent.run
        user_msg = {"role": "user", "content": "hi"}
        ctrl.append_message(user_msg)

        answer = agent.run(
            ctrl.active.messages,
            on_message=lambda m: ctrl.append_message(m),
        )

        # 4. 验证
        # final answer 字符串正确
        assert answer == "Hello, I am Claude."

        # DB 中有完整的消息序列
        snap = mgr.load_session(ctrl.active.id)
        assert snap.message_count == 3  # system + user + assistant
        roles = [m["role"] for m in snap.messages]
        assert roles == ["system", "user", "assistant"]

        # system prompt 是真实内容
        assert snap.messages[0]["content"] == "You are helpful."

        # final assistant 已持久化
        assert snap.messages[2]["content"] == "Hello, I am Claude."

        # 内存中无重复消息
        assert len(ctrl.active.messages) == snap.message_count
        for i, (mem, db) in enumerate(zip(ctrl.active.messages, snap.messages)):
            assert mem == db, f"消息 {i} 不一致"

        ctrl.close()

    def test_tool_roundtrip_via_agent_run(self, tmp_path):
        """工具调用轮次：assistant(tool_calls) → tool → final assistant 完整持久化。"""
        # ... 类似结构，mock LLM 第一次返回 tool_calls，第二次返回 final
        pass

    def test_subagent_messages_not_persisted(self, tmp_path):
        """SubAgent 中间消息仅存在于局部 messages，不进入主 session。"""
        # ... 验证 SubAgent 不接收 on_message，其消息不写入 DB
        pass
```

**改动清单**:

| 文件 | 操作 |
|------|------|
| `tests/test_session_persistence.py` | 新增 `TestRealPersistenceChain` 类（3 个测试） |
| `tests/test_conversation.py` | `_FakeAgent.run()` 签名更新为 `run(self, messages, on_message=None)` |

**验证**:
```bash
python -m pytest tests/test_session_persistence.py::TestRealPersistenceChain -v
```

---

### S5: 合并 `normalize_message()` 到 `agent/utils.py`

**目标**: 删除 `Agent._normalize_message()` 私有函数和 `filter_assistant_message()` 重复实现，统一为 `agent.utils.normalize_message()`。

**背景**: S1-S2 之后，`Agent.run()` 中 `normalize_message` 被调用了两次（assistant with tool_calls + final assistant）。这个函数是纯数据转换，属于 utils 而非 Agent 私有。

```python
# agent/utils.py —— 新增（合并 _normalize_message + filter_assistant_message）

def normalize_message(msg) -> dict:
    """将 SDK ChatCompletionMessage 或 dict 归一化为仅含 4 字段的纯 dict。

    这是项目中消息归一化的唯一入口。

    输出字段（值为 None 时省略该键）:
        role: str
        content: str | None
        tool_calls: list | None
        tool_call_id: str | None
    """
    if isinstance(msg, dict):
        return msg

    result: dict = {"role": getattr(msg, "role", "")}

    content = getattr(msg, "content", None)
    if content is not None:
        result["content"] = content

    if hasattr(msg, "tool_calls") and msg.tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", "function"),
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]

    if hasattr(msg, "tool_call_id") and msg.tool_call_id:
        result["tool_call_id"] = msg.tool_call_id

    return result
```

**改动清单**:

| 文件 | 操作 |
|------|------|
| `agent/utils.py` | 新增 `normalize_message(msg) -> dict` |
| `agent/utils.py` | 删除 `filter_assistant_message()` 函数（功能已被 normalize_message 覆盖） |
| `agent/agent.py` | 删除 `_normalize_message()` 函数定义 |
| `agent/agent.py` | 将 `_normalize_message` 调用改为 `from agent.utils import normalize_message` |

**验证**:
```bash
python -m pytest tests/ -q -k "not test_compact"
grep -n "_normalize_message\|filter_assistant_message" agent/agent.py  # 预期: 无匹配
```

---

### S6: `register_hook()` 返回 disposer

**目标**: `register_hook(event, callback)` 返回 `Callable[[], None]`（幂等 disposer）。`TodoReminderHandle` 只持有 disposer，不访问 `HOOKS` 字典。

```python
# hooks.py

def register_hook(event: str, callback: HookCallback) -> Callable[[], None]:
    """注册 Hook 回调，返回幂等 disposer。"""
    if event not in HOOKS:
        raise ValueError(f"未知的 hook 事件: {event}")
    HOOKS[event].append(callback)

    def dispose() -> None:
        try:
            HOOKS[event].remove(callback)
        except (ValueError, KeyError):
            pass  # 已移除

    return dispose
```

```python
# tools/todo_write.py —— TodoReminderHandle

class TodoReminderHandle:
    def __init__(self, pre_disposer, post_disposer):
        self._pre_disposer = pre_disposer
        self._post_disposer = post_disposer
        self._counter = 0
        self._disposed = False

    def dispose(self) -> None:
        if self._disposed:
            return
        self._pre_disposer()
        self._post_disposer()
        self._disposed = True
    # ... reset/increment_and_check 不变
```

**改动清单**:

| 文件 | 操作 |
|------|------|
| `hooks.py` | `register_hook()` 返回 disposer |
| `tools/todo_write.py` | `TodoReminderHandle.__init__` 接收 disposer 而非 callback |
| `tools/todo_write.py` | `TodoReminderHandle.dispose()` 调用 disposer 而非操作 `HOOKS` |
| `tools/todo_write.py` | `register_todo_hooks()` 传递 `register_hook()` 返回值 |

**验证**:
```bash
python -m pytest tests/test_todo_write.py -v
grep -n "HOOKS" tools/todo_write.py  # 预期: 无匹配（除 import 外）
```

---

### S7: 新建 `agent/session_controller.py` + 完善 close()

**目标**: SessionController、ActiveSession、ActiveSessionDeletionError 移入独立文件。Controller 完全拥有自己的生命周期资源（grant listener、Todo Hook），close() 保证释放。

**7.1 新建文件**: `agent/session_controller.py`

| 元素 | 新位置 | 原因 |
|------|--------|------|
| `SessionController` | `agent/session_controller.py` | 独立的应用层入口 |
| `ActiveSession` | `agent/session_controller.py` | Controller 的运行时状态 |
| `ActiveSessionDeletionError` | `agent/session_controller.py` | Controller 层异常 |
| `SessionSummary`, `SessionSnapshot`, `SessionManager` | 保留在 `agent/session_manager.py` | Repository 层 |

**7.2 Controller 完整实现伪代码**:

```python
# agent/session_controller.py

class SessionController:
    def __init__(self, session_manager, permission_engine, system_message: dict):
        self._mgr = session_manager
        self._engine = permission_engine
        self._system_message = system_message
        self.active: ActiveSession | None = None

        # Todo reminder hooks —— Controller 自行管理
        self._todo_handle = register_todo_hooks()
        # Todo persistence Hook disposer —— close 时注销
        self._todo_persistence_disposer: Callable[[], None] | None = None

        # 安装 grant listener
        self._engine.set_grant_listener(self._on_grant)
        # 注册 Todo persistence Hook
        self._register_todo_persistence()

    # ── 生命周期 ──

    def start_new(self) -> ActiveSession:
        if self.active is not None:
            self.close()
        session_id = self._mgr.create_session(self._system_message)
        self._engine.replace_session_rules([])
        replace_todos([])
        self._todo_handle.reset()
        self.active = ActiveSession(id=session_id, title="Untitled",
                                     messages=[dict(self._system_message)])
        return self.active

    def resume(self, session_id: str) -> ActiveSession:
        snap = self._mgr.load_session(session_id)
        self._engine.replace_session_rules(snap.permissions)
        replace_todos(snap.todos)
        self._todo_handle.reset()
        self.active = ActiveSession(id=snap.id, title=snap.title,
                                     messages=snap.messages)
        return self.active

    def switch(self, session_id: str) -> ActiveSession:
        if self.active is not None and session_id == self.active.id:
            return self.active
        return self.resume(session_id)

    def close(self) -> None:
        """完整释放所有资源。try/finally 保证某步失败时其余仍执行。"""
        # 1. 空对话清理
        if self.active is not None:
            try:
                self._mgr.cleanup_if_empty(self.active.id)
            except Exception:
                pass

        # 2. 注销 grant listener（PermissionEngine 不再持有对 _on_grant 的引用）
        try:
            self._engine.set_grant_listener(None)
        except Exception:
            pass

        # 3. 清空 session permission rules
        try:
            self._engine.replace_session_rules([])
        except Exception:
            pass

        # 4. 清空 Todo
        try:
            replace_todos([])
        except Exception:
            pass

        # 5. 注销 Todo persistence Hook
        if self._todo_persistence_disposer is not None:
            try:
                self._todo_persistence_disposer()
            except Exception:
                pass
            self._todo_persistence_disposer = None

        # 6. dispose reminder hooks
        try:
            self._todo_handle.dispose()
        except Exception:
            pass

        self.active = None

    # ── 消息出口 ──

    def append_message(self, message: dict) -> None:
        """先持久化（SQLite），再追加内存。失败时内存不变。"""
        if self.active is None:
            raise SessionPersistenceError("没有 active session")
        self._mgr.append_message(self.active.id, message)
        self.active.messages.append(message)

    # ── 列表与管理 ──

    def list_sessions(self): ...
    def rename(self, session_id, title): ...    # 含 active.title 同步
    def delete(self, session_id): ...           # 禁止删除 active

    # ── 权限回调 ──

    def _on_grant(self, grant) -> None:
        if self.active is not None:
            try:
                self._mgr.save_grant(self.active.id, grant)
            except SessionNotFound:
                pass

    # ── Todo 持久化 Hook ──

    def _register_todo_persistence(self) -> None:
        controller_self = self
        def on_post_tool_use(tool_name, params, result):
            if tool_name == "todo_write" and controller_self.active is not None:
                try:
                    controller_self._mgr.save_todos(
                        controller_self.active.id, snapshot_todos())
                except (SessionNotFound, SessionPersistenceError):
                    pass
        self._todo_persistence_disposer = register_hook(
            "PostToolUse", on_post_tool_use)
```

**7.3 从旧位置删除**:

| 文件 | 删除内容 |
|------|---------|
| `agent/conversation.py` | SessionController 类定义、ActiveSession、`_register_todo_persistence_hook` |
| `agent/session_manager.py` | `ActiveSession` dataclass、`ActiveSessionDeletionError` |

**验证**:
```bash
python -m pytest tests/ -q -k "not test_compact"
grep "class SessionController" agent/conversation.py  # 预期: 无匹配
grep "class ActiveSession" agent/session_manager.py   # 预期: 无匹配
```

---

### S8: 重写 Conversation + 清理 main.py + 清理 session_manager.py

**S8a: Conversation 重写要点**

- `start(resume: bool = False)` — 统一入口
- `_startup_menu(sessions)` — `--resume` 启动菜单（循环式，非递归）
- `_repl_resume_menu(sessions)` — REPL 内 `/resume` 菜单（循环式，有 active session）
- `_enter_repl()` — REPL 入口 + `finally: controller.close()`
- `_run_turn()` — 删除 system prompt 注入（已在 S3 完成）
- 不再直接 import SessionManager；Conversation 只通过 Controller 操作 session
- 同时为 None 或同时非 None 校验（禁止静默退化）

**S8b: main.py**

删除 `_list_and_act()` 和 `session_ui` import → 变为纯装配点：
```python
conv.start(resume=args.resume)
```

**S8c: session_manager.py**

- 删除 `_init_schema()` 死代码
- `list_sessions()` 使用 `logger.warning()` 替代 `print(..., file=sys.stderr)`
- `SessionSnapshot.permissions` 类型标注为 `list[PermissionGrant]`（TYPE_CHECKING）

**验证**:
```bash
python -m pytest tests/ -q
grep "_list_and_act\|session_ui" main.py           # 预期: 无匹配
grep "def _init_schema" agent/session_manager.py   # 预期: 无匹配
grep "print.*stderr" agent/session_manager.py      # 预期: 无匹配
```

---

## 6. 完成标准

- [ ] `_emit_message` 使用 `if on_message: on_message(msg) else: messages.append(msg)` —— 每条消息在内存中只追加一次
- [ ] final assistant 经 `_emit_message` 持久化到 DB + 追加到 working context
- [ ] 数据库中的 system prompt 与实际 Agent system prompt 一致（创建时写入，非空占位符）
- [ ] `append_message` 不再对 role=="system" 有特殊处理（统一：先持久化再追加内存）
- [ ] `_run_turn` 不再有首轮 system prompt 注入逻辑
- [ ] `TestRealPersistenceChain` 覆盖 `Agent.run → on_message → Controller → Repository` 完整链路
- [ ] `agent.py` 不再定义 `_normalize_message()`，统一使用 `agent.utils.normalize_message()`
- [ ] `register_hook()` 返回 disposer；`TodoReminderHandle` 不直接访问全局 `HOOKS`
- [ ] `agent/session_controller.py` 独立文件，SessionController 拥有完整的生命周期资源管理
- [ ] `Controller.close()` 注销 grant listener + Todo persistence Hook + reminder
- [ ] `Conversation.start(resume: bool)` 统一入口；启动/REPL 菜单均循环式
- [ ] `main.py` 只有装配 + `conv.start(resume=args.resume)`，无 session 业务逻辑
- [ ] `SessionManager` 不打印终端文本、不含 ActiveSession、schema 初始化单一入口
- [ ] 全量测试通过（除 pre-existing `test_compact.py`）

---

## 7. 非目标

- 不改变 SQLite schema（DDL 保持不变）
- 不增加工具中断恢复协议
- 不持久化 compact working context
- 不重构 PermissionEngine（已在 006 完成）
- 不把 Todo 改造成并发多 session 状态容器
- 不因文件长度继续拆分 SessionManager

---

## 8. Project Structure（重构后）

```text
main.py                     # 装配点 → conv.start(resume=args.resume)
hooks.py                    # register_hook(event, callback) -> disposer

agent/
├── agent.py                # Agent.run(messages, on_message=None)
├── utils.py                # normalize_message(msg) -> dict（唯一入口）
├── conversation.py         # Conversation: REPL + 命令解析 + session 菜单
├── session_controller.py   # SessionController + ActiveSession（新建）
├── session_manager.py      # SessionManager: 纯 SQLite Repository
├── ui.py                   # Session 列表 UI: 纯 I/O，零业务依赖
├── compact.py              # 不变
├── llm_client.py           # 不变
└── prompts.py              # 不变

tools/
├── todo_write.py           # snapshot/replace + TodoReminderHandle（disposer）
└── ...

tests/
├── test_session_manager.py     # Repository 单测
├── test_session_persistence.py # 集成测试（含 TestRealPersistenceChain）
└── ...
```
