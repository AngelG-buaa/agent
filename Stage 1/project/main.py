"""
最小 Agent —— 工具选择 → 工具执行 → 返回最终答案。
基于 DeepSeek API（兼容 OpenAI SDK）。

结构：llm_client → tool → tool_registry → agent → tools/ → main
"""

from llm_client import LLMClient
from tool_registry import ToolRegistry
from agent import Agent
from config import llm
from tools import register_all


if __name__ == "__main__":
    # 1. 创建 LLM 客户端
    llm = LLMClient(llm.api_key, llm.base_url, llm.model)

    # 2. 注册工具
    registry = ToolRegistry()
    register_all(registry)

    # 3. 创建 Agent
    agent = Agent(llm, registry, max_steps=10)

    # 4. 运行
    question = "现在几点？然后帮我算一下 123 * 456 等于多少。"
    print(f"👤 用户: {question}\n")
    answer = agent.run(question)
    print(f"\n🤖 DeepSeek: {answer}")
