"""LLM 客户端薄封装 —— 切换 provider 只改这里。"""

from openai import OpenAI


class LLMClient:
    """OpenAI 兼容的 LLM 客户端。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def chat(self, messages: list, tools: list) -> tuple[str, object]:
        """发送请求，返回 (finish_reason, message)。"""
        r = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
        )
        return r.choices[0].finish_reason, r.choices[0].message
