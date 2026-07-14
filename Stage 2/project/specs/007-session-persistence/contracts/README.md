# Contracts: Session 持久化（结构重构版）

**Feature**: 007-session-persistence
**Date**: 2026-07-14

本目录定义 session 持久化三层架构中各组件之间的公共接口协议。这些是模块边界——实现可按内部设计变化，但以下接口签名构成跨模块契约。

若与 [architecture-refactor-plan.md](../architecture-refactor-plan.md) 冲突，以后者为准。

## 1. SessionController（`agent/session_controller.py`）

独立模块。拥有 active session 生命周期、消息出口、grant listener、Todo Hook 和 disposer。Conversation 通过 Controller 间接操作 session，不直接访问 SessionManager。

```python
class SessionController:
    """Active session 应用入口 —— 生命周期 + 消息出口 + 状态不变量。"""

    def __init__(
        self,
        session_manager: SessionManager,
        permission_engine: PermissionEngine,
        todo_handle: TodoReminderHandle,
        system_message: dict,  # 真实的 system prompt（非空占位符）
    ): ...

    # ---- 生命周期 ----

    def start_new(self) -> ActiveSession:
        """创建新 session（含真实 system message）→ 替换权限/ Todo → 返回 active。"""

    def resume(self, session_id: str) -> ActiveSession:
        """加载完整 SessionSnapshot → 替换权限/ Todo → 返回 active。
        任何步骤失败均不改变当前 active。"""

    def switch(self, session_id: str) -> ActiveSession:
        """同 resume，但目标 = 当前 active 时幂等无副作用。"""

    def close(self) -> None:
        """完整释放：空对话清理 → 移除 grant listener → 清空 session rules →
        清空 Todo → 注销 Todo persistence Hook → dispose reminder → active = None。
        使用 try/finally 保证某一步失败时其余资源仍释放。"""

    # ---- 消息出口 ----

    def append_message(self, message: dict) -> None:
        """先写库（事务），再追加到 working context。失败时内存不变。
        这是主 session 所有消息的唯一入口。"""

    # ---- 列表与管理（委托 SessionManager）----

    def list_sessions(self) -> list[SessionSummary]: ...

    def rename(self, session_id: str, title: str) -> None:
        """重命名；若目标是当前 active，同步更新 active.title。"""

    def delete(self, session_id: str) -> None:
        """禁止删除 active session；否则委托 Repository 物理删除。"""

    # ---- 权限回调（由 grant_listener 触发）----

    def _on_grant(self, grant: PermissionGrant) -> None:
        """PermissionEngine 回调 → 持久化 grant 到当前 active session。"""
```

**不变量**:
1. 任意时刻最多一个 active session
2. 所有主会话原始消息只经过 `append_message()`
3. 切换权限前用 `replace_session_rules()` 替换，不触发 listener
4. 切换 Todo 时空列表也必须覆盖旧状态
5. resume/switch 成功后重置 Todo reminder
6. 取消或加载失败不改变 active session
7. active session 在运行期间不可删除
8. 切换到当前 active session 是无副作用操作

**附属数据结构**（同文件）:

```python
@dataclass
class ActiveSession:
    id: str
    title: str
    messages: list[dict]

class ActiveSessionDeletionError(Exception):
    """尝试删除当前 active session。"""
```

## 2. SessionManager（`agent/session_manager.py`）

纯 SQLite Repository。不依赖 Conversation、Agent、Hook、PermissionEngine 或 TodoWriteTool。不包含 ActiveSession 或 ActiveSessionDeletionError。

```python
class SessionManager:
    """纯 SQLite Repository —— schema 初始化 + CRUD + 事务快照。"""

    def __init__(self, sessions_dir: str): ...

    # ---- Session CRUD ----

    def create_session(self, system_message: dict) -> str:
        """一个事务内：建 schema → insert sessions 行 → insert system message → 返回 id。
        system_message 是真实的 Agent system prompt（调用者传入，非空占位符）。"""

    def load_session(self, session_id: str) -> SessionSnapshot:
        """同一连接+事务内读取完整快照（messages + permissions + todos）。"""

    def list_sessions(self) -> list[SessionSummary]:
        """按 updated_at DESC 排序，只读 metadata。使用 logger 记录损坏文件，不打印终端文本。"""

    def delete_session(self, session_id: str) -> None:
        """os.remove() 物理删除 .db 文件。"""

    def rename_session(self, session_id: str, title: str) -> None:
        """UPDATE sessions SET title = ? WHERE id = ?"""

    # ---- 消息 ----

    def append_message(self, session_id: str, message: dict) -> None:
        """事务内：分配 seq → INSERT → 更新 metadata → COMMIT。"""

    # ---- 权限 ----

    def save_grant(self, session_id: str, grant: PermissionGrant) -> None:
        """INSERT OR IGNORE INTO permissions。"""

    # ---- Todo ----

    def save_todos(self, session_id: str, todos: list[dict]) -> None:
        """DELETE + INSERT 整体替换（同一事务）。"""

    # ---- 生命周期 ----

    def cleanup_if_empty(self, session_id: str) -> None:
        """SELECT COUNT(*) WHERE role='user' = 0 → os.remove()。"""

    def close(self) -> None:
        """显式清理（当前实现：无连接缓存，无需操作）。"""
```

**Repository 规约**:
- 每个公开方法使用 `contextlib.closing(sqlite3.connect(...))`，不缓存连接
- 写事务使用 `with conn:`；需要 seq 分配的在事务开头 `BEGIN IMMEDIATE`
- 每次连接启用 `PRAGMA foreign_keys = ON`
- Schema 初始化只有一个实现入口
- 所有 session id 输入先通过 `uuid.UUID()` 校验 + 路径越界检查
- 捕获 `sqlite3.Error` → 转换为领域异常（`SessionNotFound` / `SessionCorrupted` / `SessionPersistenceError`），保留异常链
- 不打印交互文本
- 所有 SQL 使用参数化查询

**附属数据结构**（同文件）:

```python
@dataclass
class SessionSummary:
    id: str
    title: str
    updated_at: str
    message_count: int

@dataclass
class SessionSnapshot:
    id: str
    title: str
    updated_at: str
    message_count: int
    messages: list[dict]                  # 按 seq 排序，仅含 4 字段
    permissions: list[PermissionGrant]    # 显式类型
    todos: list[dict]                     # 按 position 排序
```

## 3. PermissionEngine 扩展（`tooling/permission/engine.py`）

已在 006-permission-refactor 中完成。Session 持久化依赖以下公开接口（不新增方法）：

```python
class PermissionEngine:
    def set_grant_listener(
        self, listener: Callable[[PermissionGrant], None] | None
    ) -> None:
        """安装 grant 持久化回调。Controller.close() 时传入 None 注销。"""

    def replace_session_rules(self, grants: list[PermissionGrant]) -> None:
        """原子替换全部会话规则。不触发 grant_listener。grants=[] 即清空。"""

    def allow_for_session(self, result: EvalResult) -> PermissionGrant:
        """用户选择 '始终允许' → 创建 grant → 触发 listener → 安装运行时规则。"""
```

`replace_session_rules([])` 等价于 `clear_session_rules()`。Controller.close() 调用 `replace_session_rules([])` + `set_grant_listener(None)`。

## 4. TodoWrite 扩展（`tools/todo_write.py`）

Session 持久化需要的最小公共接口：

```python
# ---- 状态快照与替换 ----

def snapshot_todos() -> list[dict]:
    """返回 CURRENT_TODOS 的浅拷贝快照（用于持久化）。"""

def replace_todos(todos: list[dict]) -> None:
    """原子替换 Todo 列表（clear + extend，不重新绑定引用）。
    空列表也必须替换——覆盖旧 session 的 Todo 状态。"""

# ---- Reminder Handle ----

class TodoReminderHandle:
    """封装 reminder 计数器。不直接访问全局 HOOKS。"""

    def reset(self) -> None:
        """重置计数器为零（幂等）。"""

    def dispose(self) -> None:
        """调用 Hook disposers 注销 PreLLMCall + PostRound 回调（幂等）。"""

def register_todo_hooks() -> TodoReminderHandle:
    """注册 reminder hooks 并返回 handle。"""
```

**语义**:
- `replace_todos()` 使用 `clear()` + `extend()`，不重新绑定 `CURRENT_TODOS` 引用
- `TodoReminderHandle` 只保存 `register_hook()` 返回的 disposer 函数，不导入或访问 `HOOKS` 字典
- `reset()` 和 `dispose()` 均为幂等操作

## 5. Agent 接口扩展（`agent/agent.py`）

```python
class Agent:
    def run(
        self,
        messages: list[dict],
        on_message: Callable[[dict], None] | None = None,
    ) -> str:
        """核心循环。

        Args:
            messages: working context
            on_message: 每条新消息的完整 sink。调用 sink 后 Agent 不再 append。
                        None → 使用默认 messages.append。
        """
```

**契约**:
- LLM 返回的 SDK message 通过 `agent.utils.normalize_message()` 归一化为 dict
- `on_message` 的 dict 只含 `role`、`content`、`tool_calls`、`tool_call_id` 四个字段
- SubAgent 永远不传入 `on_message`
- Agent 不再定义 `_normalize_message()` 方法

## 6. agent.utils —— 消息归一化（`agent/utils.py`）

```python
def normalize_message(message) -> dict:
    """将 SDK message 转为仅含 role/content/tool_calls/tool_call_id 的兼容字典。

    这是项目中消息归一化的唯一入口。合并了旧 Agent._normalize_message() 和
    filter_assistant_message() 的职责。
    """
```

## 7. Hook 接口（`hooks.py`）

```python
def register_hook(event: str, callback) -> Callable[[], None]:
    """注册 Hook 回调，返回幂等 disposer。
    调用 disposer() 即从 HOOKS 中移除该回调。重复调用无害。"""

def trigger_hooks(event: str, *args) -> dict | None:
    """触发指定事件的所有回调。返回值语义不变。"""
```

## 8. Conversation 构造与启动接口

```python
class Conversation:
    def __init__(
        self,
        agent: Agent,
        session_manager: SessionManager | None = None,
        permission_engine: PermissionEngine | None = None,
    ): ...

    def start(self, resume: bool = False) -> None:
        """启动 REPL。
        
        resume=False: 创建新 session，进入 REPL。
        resume=True:  显示 session 列表供选择恢复；无历史或取消则创建新 session。
                      启动选择期间不存在 active session。
        """
```

**模式**:
- **生产模式**: 同时传入 `session_manager` + `permission_engine` → 完整的持久化 + 权限隔离
- **Transient 模式**: 两者均为 `None` → 不创建 Controller 或数据库，不支持 `/resume`
- 只提供其中一个依赖时立即报错，禁止静默退化为 transient

## 9. UI 模块（`agent/ui.py`）

零业务依赖的纯 I/O 模块。返回数据或 action，不调用 Controller、SessionManager 或 Conversation。

```python
def select_session(sessions: list[SessionSummary]) -> str | None:
    """交互式列表选择。返回 session_id 或 None (取消)。"""

def confirm_delete(title: str) -> bool:
    """"Are you sure? [y/N]" → True/False。"""

def prompt_rename(current_title: str) -> str:
    """输入新标题，空输入返回原标题。"""

def show_actions_menu() -> str | None:
    """[R]esume / [D]elete / [R]ename / [C]ancel → 返回小写动作名。"""
```

## 10. 异常层次

```python
# agent/session_manager.py
class SessionNotFound(Exception): ...            # 数据库文件不存在
class SessionCorrupted(Exception): ...           # schema 不兼容或文件损坏
class SessionPersistenceError(Exception): ...    # 写入/读取失败，链式包装 sqlite3.Error

# agent/session_controller.py
class ActiveSessionDeletionError(Exception): ...  # 尝试删除当前 active session
```

**处理规则**:
- Repository 抛出 `SessionNotFound` / `SessionCorrupted` / `SessionPersistenceError`（不打印交互文本）
- Controller 抛出 `ActiveSessionDeletionError`（禁止删除 active）
- Controller 不吞持久化异常，不提交半完成状态
- Conversation 在 CLI 边界转为用户可读消息
- resume/switch 失败时保留原 active session
