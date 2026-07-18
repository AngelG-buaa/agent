"""LLM 客户端薄封装 —— 切换 provider 只改这里。"""

from openai import OpenAI
import json

class LLMClient:
    """OpenAI 兼容的 LLM 客户端。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def chat(self, messages: list, tools: list, max_tokens: int | None = None) -> tuple[str, object]:
        """发送请求，返回 (finish_reason, message)。max_tokens 可选，默认不限制。"""
        # 打印实际发送的 HTTP body（JSON 格式）
        # body = {"model": self.model, "messages": messages, "tools": tools}
        # print("\n===== 发给 LLM 的 JSON body =====")
        # print(f"{json.dumps(body, ensure_ascii=False, indent=2, default=lambda o: o.model_dump() if hasattr(o, 'model_dump') else str(o))")
        # print("===== end =====\n")
        kwargs = {"model": self.model, "messages": messages, "tools": tools}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        r = self._client.chat.completions.create(**kwargs)
        return r.choices[0].finish_reason, r.choices[0].message
