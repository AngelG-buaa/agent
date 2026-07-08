"""
Agent + RAG —— 工具选择 → 权限检查 → 工具执行 → 返回带引用的最终答案。

结构：config → llm_client → tool → tool_registry → tool_executor → agent
           → rag/ (parser, chunker, embedder, vector_store, retriever)
           → tools/ (9 个工具: 6 安全 + 3 敏感/破坏性)
           → tool_permission (4 来源 UNION 合并 + 8 步管线)
           → main

离线 Ingestion：python index_cli.py <file_path>
（刚性流程，不经过 Agent）"""

from agent.llm_client import LLMClient
from tooling.registry import ToolRegistry
from tooling.executor import build_tool_executor
from agent.agent import Agent
from config import llm as llm_cfg, WORKDIR
from agent.prompts import SYSTEM_PROMPT
from tools import register_all


if __name__ == "__main__":
    # 1. 创建 LLM 客户端
    llm = LLMClient(llm_cfg.api_key, llm_cfg.base_url, llm_cfg.model)

    # 2. 注册工具
    registry = ToolRegistry()
    register_all(registry, include_dangerous=True, workdir=WORKDIR)

    # 3. 创建工具执行器（工厂函数封装全部权限组装细节）
    executor = build_tool_executor(registry, project_root=WORKDIR)

    # 4. 创建 Agent
    agent = Agent(llm, executor, system_prompt=SYSTEM_PROMPT, max_steps=15)

    # 5. 运行
    question = "搜一个性价比很高的手机"
    print(f"👤 用户: {question}\n")
    answer = agent.run(question)
    print(f"\n🤖 MyAgent: {answer}")
