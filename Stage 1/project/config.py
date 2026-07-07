"""集中管理 LLM 配置。切换 provider 只改这一个文件。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str


deepseek = LLMConfig(
    api_key="sk-c4ac52844c2d4985bb3eea5d81cf89b5",
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
)