"""LLM 消息工具 —— Agent ↔ LLM API 边界的消息格式转换。

只做一件事：把 LLM 返回的 assistant 消息对象转成干净的 dict，
去掉不应回传的字段（如 reasoning_content）。

设计原则：
  - 模块即命名空间，不创建无状态的工具类
  - 只放"LLM 消息边界"相关的处理，不混入业务逻辑
  - 可生长 —— 未来 PUA 清洗、token 计数等需求自然加到这里
"""


def filter_assistant_message(msg) -> dict:
    """将 LLM 返回的 assistant 消息转为干净的 dict。

    去掉 reasoning_content（模型内部独白），避免在后续轮次中
    重复发送，造成 O(N²) 级别的 token 浪费。

    只保留 OpenAI API 需要的字段：role, content, tool_calls。
    """
    d: dict = {"role": msg.role}
    if msg.content:
        d["content"] = msg.content
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return d
