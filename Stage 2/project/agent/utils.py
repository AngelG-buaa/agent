"""Agent 工具函数 —— 打印回调及其他公共工具。"""


def default_print_handler(name: str, args: dict) -> None:
    """主 Agent 工具调用输出格式：🔧 调用工具: name({args})"""
    print(f"  🔧 调用工具: {name}({args})")


def sub_print_handler(name: str, args: dict) -> None:
    """Sub-Agent 精简输出格式：[sub] name(key_param_summary)"""
    summary = _extract_key_param(name, args)
    print(f"  [sub] {name}({summary})")


def _extract_key_param(name: str, args: dict) -> str:
    """从工具参数中提取关键摘要，避免打印完整 JSON 淹没终端。

    为每种工具类型选择最有辨识度的参数值：
      - bash → 命令摘要（截断至 60 字符）
      - 文件工具 → 文件路径
      - glob/搜索 → 模式或查询（截断至 60 字符）
      - 无参数工具（calculator、get_time）→ 空字符串
    """
    if name == "bash":
        cmd = str(args.get("command", ""))
        return cmd[:60] + ("..." if len(cmd) > 60 else "")
    if name in ("read_file", "write_file", "edit_file", "read_chunk"):
        return str(args.get("file_path", args.get("path", "?")))
    if name in ("web_search", "search_knowledge"):
        return str(args.get("query", "?"))[:60]
    if name == "web_fetch":
        return str(args.get("url", "?"))[:60]
    # calculator, get_time 等无需关键参数的工具
    return ""


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


# ═══════════════════════════════════════════════════════════
# 通用消息访问器 —— 兼容 dict 和 OpenAI SDK ChatCompletionMessage
# ═══════════════════════════════════════════════════════════


def get_role(msg) -> str:
    """提取消息 role —— dict 用 ["role"]，SDK 对象用 .role。"""
    if isinstance(msg, dict):
        return msg.get("role", "")
    return getattr(msg, "role", "")


def get_content(msg) -> str:
    """提取消息 content —— dict 用 ["content"]，SDK 对象用 .content。"""
    if isinstance(msg, dict):
        return msg.get("content", "") or ""
    val = getattr(msg, "content", None)
    return val or ""


def get_tool_calls(msg) -> list[dict]:
    """提取 tool_calls，统一返回 list[dict]（含 "id" 键）。

    dict 格式: [{id, type, function: {name, arguments}}, ...]
    SDK 对象: ChatCompletionMessageFunctionToolCall → 转为同结构 dict。
    """
    if isinstance(msg, dict):
        return msg.get("tool_calls", []) or []
    tcs = getattr(msg, "tool_calls", None) or []
    result: list[dict] = []
    for tc in tcs:
        if isinstance(tc, dict):
            result.append(tc)
        else:
            result.append({
                "id": tc.id,
                "type": getattr(tc, "type", "function"),
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })
    return result


def get_tool_call_id(msg) -> str | None:
    """提取 tool_call_id —— dict 用 ["tool_call_id"]，SDK 对象用 .tool_call_id。"""
    if isinstance(msg, dict):
        return msg.get("tool_call_id")
    return getattr(msg, "tool_call_id", None)


def set_content(msg, content: str) -> None:
    """设置消息 content —— dict 用 ["content"]，SDK 对象用 .content。"""
    if isinstance(msg, dict):
        msg["content"] = content
    elif hasattr(msg, "content"):
        msg.content = content
    else:
        # 明确告知调用方这里出了问题，而不是偷偷忽略
        raise TypeError(f"Unsupported msg type: {type(msg)}")


def to_serializable(msg) -> dict:
    """将消息转为可 JSON 序列化的纯 dict（用于存档、LLM 调用）。"""
    if isinstance(msg, dict):
        return msg
    d: dict = {"role": get_role(msg)}
    content = get_content(msg)
    if content:
        d["content"] = content
    tcs = get_tool_calls(msg)
    if tcs:
        d["tool_calls"] = tcs
    tc_id = get_tool_call_id(msg)
    if tc_id:
        d["tool_call_id"] = tc_id
    return d
