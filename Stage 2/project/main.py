"""
Agent + RAG —— 工具选择 → 权限检查 → 工具执行 → 返回带引用的最终答案。

结构：config → llm_client → tool → tool_registry → tool_executor → agent
           → rag/ (parser, chunker, embedder, vector_store, retriever)
           → tools/ (9 个工具: 6 安全 + 3 敏感/破坏性)
           → tool_permission (5 来源 + 三池评估)
           → main

离线 Ingestion：python index_cli.py <file_path>
（刚性流程，不经过 Agent）"""

from llm_client import LLMClient
from tool_registry import ToolRegistry
from tool_executor import ToolExecutor
from tool_permission import (
    PermissionEngine,
    PolicySettingsSource,
    ProjectSettingsSource,
    LocalSettingsSource,
)
from agent import Agent
from config import llm as llm_cfg, WORKDIR
from prompts import SYSTEM_PROMPT
from tools import register_all


if __name__ == "__main__":
    # 1. 创建 LLM 客户端
    llm = LLMClient(llm_cfg.api_key, llm_cfg.base_url, llm_cfg.model)

    # 2. 注册工具
    registry = ToolRegistry()
    register_all(registry, include_dangerous=True)

    # 3. 构建权限引擎（4 个来源 + ExtraRules）
    engine = PermissionEngine(
        sources=[
            PolicySettingsSource(workdir=WORKDIR),
            ProjectSettingsSource(WORKDIR),
            LocalSettingsSource(WORKDIR),
        ],
        default_behavior="allow",
    )

    # 4. 创建工具执行器（包装 registry + 权限）
    executor = ToolExecutor(registry, engine)

    # 5. 创建 Agent（传入 executor 替代裸 registry，Agent 代码零改动）
    agent = Agent(llm, executor, system_prompt=SYSTEM_PROMPT, max_steps=15)

    # 6. 运行
    question = "搜一个性价比很高的手机"
    print(f"👤 用户: {question}\n")
    answer = agent.run(question)
    print(f"\n🤖 MyAgent: {answer}")
