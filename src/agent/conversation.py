"""终端对话入口。"""

from __future__ import annotations

from agent.session_controller import SessionController
from agent.session_manager import SessionError
from tooling.permission.exceptions import InvalidPermissionGrant


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class Conversation:
    """Conversation 是终端交互与单轮用例的编排边界。

    它通过 SessionController 操作当前会话，并在普通 user turn 调用
    Memory recall；不持有 messages，也不实现持久化或检索规则。
    """

    def __init__(
        self,
        agent,
        session_manager,
        permission_engine,
        system_message: dict,
        memory_service,
    ):
        self.agent = agent
        self._memory_service = memory_service
        self._interrupted_once = False

        self._controller = SessionController(session_manager, permission_engine, system_message)

    # ------------------------------------------------------------------
    # REPL 主循环
    # ------------------------------------------------------------------

    def start(self, resume: bool = False) -> None:
        """启动终端交互，并在退出时释放会话运行时资源。"""
        try:
            if resume:
                self._session_menu(startup=True)
            else:
                self._start_new_session()

            self._repl_loop()
        except (
            SessionError,
            InvalidPermissionGrant,
        ) as exc:
            print(f"⚠️  Session 启动失败: {exc}")
        finally:
            try:
                self._controller.close()
            except Exception as exc:
                print(f"⚠️  Session 清理失败: {exc}")

    def _repl_loop(self) -> None:
        """读取用户输入，分发命令或执行一轮对话。"""
        while True:
            try:
                user_input = input("👤 你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 再见！")
                break

            # 空输入跳过
            if not user_input:
                continue

            # 元命令处理
            if user_input.lower() in ("/exit", "/quit", "exit"):
                print("👋 再见！")
                break

            if user_input.lower().startswith("/resume"):
                try:
                    self._session_menu(startup=False)
                except Exception as exc:
                    print(f"⚠️  Session 操作失败: {exc}")
                continue

            # 执行一轮对话
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
                print(f"\n❌ 发生错误: {exc}")
                print("请重试或输入 /exit 退出。")

    # ------------------------------------------------------------------
    # 单轮执行
    # ------------------------------------------------------------------

    def _run_turn(self, user_input: str) -> None:
        """记录用户消息，并让 Agent 使用当前会话上下文执行一轮。"""
        self._controller.append_message({
            "role": "user",
            "content": user_input,
        })

        request_context = None
        try:
            recall = self._memory_service.recall(user_input)
            request_context = recall.request_context
            for warning in recall.warnings:
                print(f"⚠️  Memory: {warning}")
        except Exception as exc:
            print(f"⚠️  Memory recall failed: {exc}")

        # Agent.run 的消息出口由 Controller 注入；Memory 只进入临时 request。
        answer = self.agent.run(
            self._controller.active.messages,
            on_message=self._controller.append_message,
            request_context=request_context,
        )

        print(f"\n🤖 Agent: {answer}\n")

    # ------------------------------------------------------------------
    # /resume 命令
    # ------------------------------------------------------------------

    def _session_menu(self, *, startup: bool) -> bool:
        """处理恢复、删除和重命名命令；不直接读写会话数据。"""
        import agent.ui as session_ui

        while True:
            sessions = self._controller.list_sessions()
            if not sessions:
                print("No saved sessions found.")
                if startup:
                    self._start_new_session()
                    return True
                return False

            selected_id = session_ui.select_session(sessions)
            if selected_id is None:
                if startup:
                    self._start_new_session()
                    return True
                return False

            target = next((item for item in sessions if item.id == selected_id), None)
            if target is None:
                continue

            action = session_ui.show_actions_menu()
            if action is None:
                if startup:
                    self._start_new_session()
                    return True
                return False

            if action == "resume":
                self._controller.switch(target.id)
                print(f"已切换到 session: {self._controller.active.title}")
                return True

            if action == "delete":
                if not session_ui.confirm_delete(target.title):
                    continue
                self._controller.delete(target.id)
                print(f"Deleted session: {target.title}")
                continue

            if action == "rename":
                new_title = session_ui.prompt_rename(target.title)
                if new_title != target.title:
                    self._controller.rename(target.id, new_title)
                    print(f"Renamed to: {new_title}")
                continue

    def _start_new_session(self) -> None:
        """请求 Controller 创建并激活一个新会话。"""
        self._controller.start_new()
        print("🤖 myAgent 已启动。输入 /exit 退出，Ctrl+C 中断当前操作。\n")
