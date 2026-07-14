"""myAgent CLI 入口 —— 组装 LLM → Executor → Agent → Conversation，启动 REPL。

main.py 只做参数解析和对象装配，不实现 session 用例。

用法:
    python main.py           # 新建 session
    python main.py --resume  # 查看历史并恢复
"""

import argparse
import os

from agent.llm_client import LLMClient
from tooling.permission import create_engine
from tooling.executor import ToolExecutor, terminal_approver
from agent.agent import Agent
from agent.conversation import Conversation
from agent.session_manager import SessionManager
from config import llm as llm_cfg, WORKDIR
from agent.prompts import SYSTEM_PROMPT
from tools import register_all

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="myAgent — LLM Agent")
    parser.add_argument("--resume", action="store_true", help="恢复历史 session")
    args = parser.parse_args()

    # 1. 创建 LLM 客户端
    llm = LLMClient(llm_cfg.api_key, llm_cfg.base_url, llm_cfg.model)

    # 2. 创建权限引擎 + 工具执行器
    engine = create_engine(project_root=WORKDIR, default_behavior="ask")
    executor = ToolExecutor(permission_engine=engine, approver=terminal_approver)
    register_all(executor, include_dangerous=True, workdir=WORKDIR, llm=llm)

    # 3. 创建 SessionManager
    sessions_dir = os.path.join(WORKDIR, ".myagent", "sessions")
    session_mgr = SessionManager(sessions_dir=sessions_dir)

    # 4. 创建 Agent
    agent = Agent(llm, executor, system_prompt=SYSTEM_PROMPT, max_steps=50)

    # 5. 创建 Conversation（注入 SessionManager + PermissionEngine + system_message）
    conv = Conversation(
        agent,
        session_manager=session_mgr,
        permission_engine=engine,
        system_message={"role": "system", "content": SYSTEM_PROMPT},
    )

    # 6. 启动
    conv.start(resume=args.resume)
