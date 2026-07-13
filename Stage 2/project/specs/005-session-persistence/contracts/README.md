# Contracts: Session 持久化

**Feature**: 005-session-persistence
**Date**: 2026-07-13

## 1. SessionManager Public Interface

```python
class SessionManager:
    """
    会话持久化管理器。

    每 session 一个独立的 SQLite .db 文件，存储在 .myagent/sessions/{uuid}.db。
    在 main.py 创建，通过 Hook 系统与 Conversation 桥接。
    不知道 Conversation、Agent、Hook 系统的存在——只暴露公共方法，由 main.py 注册 hook 回调调用。
    """

    def __init__(self, sessions_dir: str):
        """sessions_dir: 项目目录下的 .myagent/sessions/ 绝对路径"""
        ...

    # ── Session CRUD ──────────────────────────────────────

    def create_session(self) -> str:
        """
        创建新 session。
        Returns: session UUID。
        副作用: 在 sessions_dir 创建 {uuid}.db 并初始化 schema。
        """

    def list_sessions(self) -> list[SessionSummary]:
        """
        列出所有 session。
        Returns: SessionSummary 列表，按 updated_at 降序。
        对已损坏/不存在的 .db 文件跳过并打印警告。
        """

    def delete_session(self, session_id: str) -> None:
        """
        删除 session。
        副作用: os.remove({session_id}.db)。
        Raises: FileNotFoundError if .db missing (shouldn't happen per caller guard)。
        """

    def rename_session(self, session_id: str, new_title: str) -> None:
        """更新 session 标题。"""

    # ── Message persistence ───────────────────────────────

    def save_message(self, session_id: str, message: dict | object) -> None:
        """
        持久化单条消息。
        message 为 dict 或 OpenAI SDK ChatCompletionMessage 对象。
        通过 agent/utils.py 的消息访问器 (get_role, get_content 等) 提取字段。
        Raises: sqlite3.Error on write failure (不静默吞异常)。
        """

    def load_messages(self, session_id: str) -> list[dict]:
        """
        加载 session 全部消息，按 seq 排序。
        Returns: 可直接赋给 Agent.run(messages=...) 的 list[dict]。
        对 tool 消息通过 tool_call_id 从 assistant 消息的工具调用 JSON 中反查 tool_name。
        """

    # ── Permission persistence ────────────────────────────

    def save_permission(self, session_id: str, tool_name: str) -> None:
        """记录 session 级 allow 决策。INSERT OR IGNORE (PRIMARY KEY 约束防重复)。"""

    def load_permissions(self, session_id: str) -> set[str]:
        """返回该 session 中已 allow 的工具名集合。"""

    # ── Todo persistence ───────────────────────────────────

    def save_todos(self, session_id: str, todos: list[dict]) -> None:
        """
        全量替换当前 todo 列表。
        先 DELETE 该 session 的全部 todo 记录，再 INSERT 新列表。
        """

    def load_todos(self, session_id: str) -> list[dict]:
        """加载 session 的 todo 列表。"""

    # ── Lifecycle ──────────────────────────────────────────

    def cleanup_if_empty(self, session_id: str) -> None:
        """检查 session 是否为空对话（无 user 消息），是则 os.remove(.db)。"""

    def close(self) -> None:
        """关闭数据库连接。"""


@dataclass
class SessionSummary:
    """Session 列表条目（轻量，不含消息体）"""
    id: str
    title: str
    updated_at: str
    message_count: int
```

## 2. CLI Contract

### `python main.py`

- 行为：创建新 session（`Conversation.start()` → `SessionManager.create_session()`），进入 REPL
- 退出：正常退出（`/exit`）或 Ctrl+C → `SessionManager.cleanup_if_empty()` + `close()`

### `python main.py --resume`

- 行为：调用 `SessionManager.list_sessions()`
- 若列表为空：提示 "No saved sessions found." → 自动进入新 session（同 `main.py`）
- 若列表非空：展示交互式选择 UI → 用户选择 session → 恢复消息/权限/Todo → 进入 REPL

### REPL 内 `/resume`

- 行为：保存当前 session（更新 metadata）→ `list_sessions()` → 展示选择 UI → 切换
- 切换后：当前 Conversation 的 messages 替换为目标 session 的 messages

## 3. Session List UI Contract

```
┌─────────────────────────────────────┐
│  Sessions                          │
│                                    │
│  > 帮我写一个HTTP服务器    07-13    │  ← 箭头键上下移动
│    列出所有Python文件      07-12    │
│    重构数据库模块          07-11    │
│                                    │
│  ───────────────────────────────   │
│  Enter: Select  D: Delete         │
│  R: Rename      Q: Cancel         │
└─────────────────────────────────────┘
```

- 箭头键导航（↑↓），高亮当前选中行（`>` 前缀或反色）
- Enter 选中 = 恢复该 session
- D = 删除确认提示 "Are you sure? [y/N]"
- R = 输入新标题
- Q = 返回/取消
- 实现：Windows `msvcrt.getch()` / Unix `termios` + `tty`，纯标准库
