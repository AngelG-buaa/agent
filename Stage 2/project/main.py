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
from tooling.executor import build_tool_executor
from agent.agent import Agent
from config import llm as llm_cfg, WORKDIR
from agent.prompts import SYSTEM_PROMPT
from tools import register_all
from hooks import trigger_hooks


if __name__ == "__main__":
    # 1. 创建 LLM 客户端
    llm = LLMClient(llm_cfg.api_key, llm_cfg.base_url, llm_cfg.model)

    # 2. 创建工具执行器（内部: ToolRegistry + PreToolUse permission_hook）
    executor = build_tool_executor(project_root=WORKDIR)
    register_all(executor, include_dangerous=True, workdir=WORKDIR)

    # 3. 创建 Agent
    agent = Agent(llm, executor, system_prompt=SYSTEM_PROMPT, max_steps=15)

    # Hook: 会话启动（memory 初始化、session 记录等）
    trigger_hooks("SessionStart")

    # 4. 运行
    question = "新建一个文件，之后删除它"
    print(f"👤 用户: {question}\n")
    answer = agent.run(question)
    print(f"\n🤖 MyAgent: {answer}")
