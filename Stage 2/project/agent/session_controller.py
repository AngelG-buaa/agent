"""会话运行时状态及其生命周期管理。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agent.session_manager import (
    SessionManager,
    SessionError,
    SessionSummary,
)
from tooling.permission.engine import PermissionEngine, PermissionGrant
from tools.todo_write import (
    replace_todos,
    snapshot_todos,
    TodoReminderHandle,
    register_todo_hooks,
)
from hooks import register_hook


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ActiveSession:
    """ActiveSession 表示当前运行中的连续对话。

    messages 是提供给 Agent 的工作上下文，也是会话最核心的运行时状态。
    它只存在于内存中，由 SessionController 持有和替换。
    """
    id: str
    title: str
    messages: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SessionController
# ---------------------------------------------------------------------------

class SessionController:
    """SessionController 管理当前会话的生命周期和消息写入。

    它持有唯一的 ActiveSession，协调消息、权限和 Todo 的恢复与清理，
    并把具体的 SQLite 读写委托给 SessionManager。

    不变量:
      1. 任意时刻最多一个 active session
      2. 所有主会话原始消息只经过 append_message()
      3. 切换权限前用 replace_session_rules()，不触发 listener
      4. 切换 Todo 时空列表也必须覆盖旧状态
      5. resume/switch 成功后重置 reminder
      6. 取消或加载失败不改变 active session
      7. active session 在运行期间不可删除
      8. 切换到当前 active session 是无副作用操作
    """

    def __init__(
        self,
        session_manager: SessionManager,
        permission_engine: PermissionEngine,
        system_message: dict,
    ):
        self._mgr = session_manager
        self._engine = permission_engine
        self._system_message = system_message
        self.active: ActiveSession | None = None

        # Todo reminder hooks —— Controller 自行管理
        self._todo_handle = register_todo_hooks()
        # Todo persistence Hook disposer —— close 时注销
        self._todo_persistence_disposer: Callable[[], None] | None = None

        # 安装 grant 持久化回调
        self._engine.set_grant_listener(self._on_grant)
        # 注册 Todo persistence Hook
        self._register_todo_persistence()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start_new(self) -> ActiveSession:
        """创建并激活新会话，同时清空上一会话的权限和 Todo 状态。"""
        session_id = self._mgr.create_session(self._system_message)
        candidate = ActiveSession(
            id=session_id,
            title="Untitled",
            messages=[dict(self._system_message)],
        )

        self._engine.replace_session_rules([])
        replace_todos([])
        self._todo_handle.reset()

        self.active = candidate
        return self.active

    def resume(self, session_id: str) -> ActiveSession:
        """从完整快照恢复消息、权限和 Todo，并将其设为当前会话。"""
        snap = self._mgr.load_session(session_id)
        candidate = ActiveSession(
            id=snap.id,
            title=snap.title,
            messages=snap.messages,
        )

        self._engine.replace_session_rules(snap.permissions)
        replace_todos(snap.todos)
        self._todo_handle.reset()
        self.active = candidate
        return self.active

    def switch(self, session_id: str) -> ActiveSession:
        """切换到指定会话；目标已经激活时直接返回当前状态。"""
        if self.active is not None and session_id == self.active.id:
            return self.active
        return self.resume(session_id)

    def close(self) -> None:
        """结束当前会话，并释放权限监听器和 Todo Hook。"""
        if self.active is not None:
            self._mgr.cleanup_if_empty(self.active.id)

        self._engine.set_grant_listener(None)
        self._engine.replace_session_rules([])
        replace_todos([])

        if self._todo_persistence_disposer is not None:
            self._todo_persistence_disposer()
            self._todo_persistence_disposer = None

        self._todo_handle.dispose()

        self.active = None

    # ------------------------------------------------------------------
    # 消息出口
    # ------------------------------------------------------------------

    def append_message(self, message: dict) -> None:
        """将消息写入 SQLite，再追加到当前会话的 working context。"""
        # if self.active is None:
        #     raise SessionError("没有 active session")

        self._mgr.append_message(self.active.id, message)
        self.active.messages.append(message)
        if message.get("role") == "user" and self.active.title == "Untitled":
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                self.active.title = content.strip()[:50]

    # ------------------------------------------------------------------
    # 列表与管理
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[SessionSummary]:
        """返回可供终端菜单展示的会话摘要。"""
        return self._mgr.list_sessions()

    def rename(self, session_id: str, title: str) -> None:
        """重命名会话，并同步当前 ActiveSession 的标题。"""
        self._mgr.rename_session(session_id, title)
        if self.active is not None and session_id == self.active.id:
            self.active.title = title

    def delete(self, session_id: str) -> None:
        """删除非当前会话；当前运行中的会话不能被删除。"""
        if self.active is not None and session_id == self.active.id:
            raise SessionError(
                f"不能删除当前 active session: {self.active.title!r}"
            )
        self._mgr.delete_session(session_id)

    # ------------------------------------------------------------------
    # 权限回调
    # ------------------------------------------------------------------

    def _on_grant(self, grant: PermissionGrant) -> None:
        """接收本次用户交互产生的会话授权并交给 Repository 保存。"""
        if self.active is None:
            raise SessionError("没有 active session，无法保存权限 grant")
        self._mgr.save_grant(self.active.id, grant)

    # ------------------------------------------------------------------
    # Todo 持久化 Hook（内部注册）
    # ------------------------------------------------------------------

    def _register_todo_persistence(self) -> None:
        """注册 Todo 写入后的持久化回调，并保存其注销函数。"""
        controller_self = self

        def on_post_tool_use(tool_name: str, params: dict, result: dict):
            if (
                tool_name == "todo_write"
                and controller_self.active is not None
                and "error" not in result
            ):
                controller_self._mgr.save_todos(
                    controller_self.active.id,
                    snapshot_todos(),
                )

        self._todo_persistence_disposer = register_hook(
            "PostToolUse", on_post_tool_use
        )
