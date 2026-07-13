"""
Agent + RAG —— 工具选择 → 权限检查 → 工具执行 → 返回带引用的最终答案。

结构: config → llm_client → tool_executor → agent
        → rag/ (parser, chunker, embedder, vector_store, retriever)
        → tools/ (9 个工具: 6 安全 + 3 敏感/破坏性)
        → tooling/permission/ (权限包: RuleSource → PolicySettingsSource → Engine, 3 步管线)
        → main

离线 Ingestion: python index_cli.py <file_path>
（刚性流程，不经过 Agent）
"""

from agent.llm_client import LLMClient
from tooling.permission import create_engine
from tooling.executor import ToolExecutor, terminal_approver
from agent.agent import Agent
from agent.conversation import Conversation
from config import llm as llm_cfg, WORKDIR
from agent.prompts import SYSTEM_PROMPT
from tools import register_all
from tools.todo_write import register_todo_hooks


if __name__ == "__main__":
    # 1. 创建 LLM 客户端
    llm = LLMClient(llm_cfg.api_key, llm_cfg.base_url, llm_cfg.model)

    # 2. 创建权限引擎 + 工具执行器（显式组装，engine 可共享给 SessionController）
    engine = create_engine(project_root=WORKDIR, default_behavior="ask")
    executor = ToolExecutor(permission_engine=engine, approver=terminal_approver)
    register_all(executor, include_dangerous=True, workdir=WORKDIR, llm=llm)

    # 装配 todo_write 提醒 hooks（PreLLMCall + PostRound）
    register_todo_hooks()

    # 3. 创建 Agent
    agent = Agent(llm, executor, system_prompt=SYSTEM_PROMPT, max_steps=50)

    # 4. 启动交互式对话 REPL
    conv = Conversation(agent)
    conv.start()
