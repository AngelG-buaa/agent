"""Conversation —— 多轮对话编排器，管理 REPL 外循环和跨轮状态。

位于 Agent 之上，负责：
  - REPL 外循环（input → agent.run → output → repeat）
  - 跨轮 messages[] 生命周期管理
  - system prompt 首轮插入
  - /exit 命令拦截
  - Ctrl+C 中断保护
  - API 异常兜底

不负责 Agent 核心循环（Think→Act→Observe）——完全由 Agent.run() 处理。
"""

from hooks import trigger_hooks


class Conversation:
    """多轮对话编排器。拥有 messages 生命周期，负责组装并传给 Agent.run()。"""

    def __init__(self, agent):
        self.agent = agent
        self.messages: list[dict] = []
        self._interrupted_once = False

    # ------------------------------------------------------------------
    # REPL 主循环
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动 REPL 主循环。"""
        trigger_hooks("SessionStart")
        print("🤖 myAgent 已启动。输入 /exit 退出，Ctrl+C 中断当前操作。\n")

        while True:
            try:
                user_input = input("👤 你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 再见！")
                break

            # 元命令处理
            if user_input.lower() in ("/exit", "/quit", "exit"):
                print("👋 再见！")
                break

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
        print(f"\n🤖 Agent: {answer}\n")
