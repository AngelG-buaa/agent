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
from tooling.executor import ToolExecutor, TerminalApprover
from agent.agent import Agent
from agent.conversation import Conversation
from agent.session_manager import SessionManager
from config import llm as llm_cfg, WORKDIR
from memory import create_memory_service
from agent.prompts import SYSTEM_PROMPT
from tools import register_all
from terminal.io import IOBackend

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="myAgent — LLM Agent")
    parser.add_argument("--resume", action="store_true", help="恢复历史 session")
    args = parser.parse_args()

    # 0. 创建 IOBackend（所有输出的统一网关）
    io = IOBackend.terminal()

    # 1. 创建 LLM 客户端
    llm = LLMClient(llm_cfg.api_key, llm_cfg.base_url, llm_cfg.model)

    # Memory 是项目级服务，由工厂函数装配，register_all() 需要它来注册 memory_write。
    memory_service = create_memory_service()

    # 2. 创建权限引擎 + 工具执行器
    engine = create_engine(project_root=WORKDIR, default_behavior="allow")
    executor = ToolExecutor(permission_engine=engine, approver=TerminalApprover(input_reader=io.input, output=io.output))
    register_all(
        executor,
        include_dangerous=True,
        workdir=WORKDIR,
        llm=llm,
        memory_service=memory_service,
        output=io.output,
    )

    # 3. 创建 SessionManager
    sessions_dir = os.path.join(WORKDIR, ".myagent", "sessions")
    session_mgr = SessionManager(sessions_dir=sessions_dir)

    # 4. 创建 Agent
    agent = Agent(llm, executor, system_prompt=SYSTEM_PROMPT, max_steps=100,
                  io_backend=io)

    # 5. 创建 Conversation（注入 SessionManager + PermissionEngine + system_message）
    conv = Conversation(
        agent,
        session_manager=session_mgr,
        permission_engine=engine,
        system_message={"role": "system", "content": SYSTEM_PROMPT},
        memory_service=memory_service,
    )

    # 6. 启动
    conv.start(resume=args.resume)
