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


def normalize_message(msg) -> dict:
    """将 SDK ChatCompletionMessage 或 dict 归一化为仅含 4 字段的纯 dict。

    这是项目中消息归一化的唯一入口。合并了旧 Agent._normalize_message()
    和 filter_assistant_message() 的职责。

    输出字段（值为 None/空时省略该键）:
        role: str           — 始终存在
        content: str | None — None 时省略
        tool_calls: list    — 空/None 时省略
        tool_call_id: str   — None 时省略
    """
    if isinstance(msg, dict):
        result: dict = {"role": msg.get("role", "")}
        if msg.get("content") is not None:
            result["content"] = msg["content"]
        if msg.get("tool_calls"):
            result["tool_calls"] = msg["tool_calls"]
        if msg.get("tool_call_id"):
            result["tool_call_id"] = msg["tool_call_id"]
        return result

    result: dict = {"role": getattr(msg, "role", "")}

    content = getattr(msg, "content", None)
    if content is not None:
        result["content"] = content

    if hasattr(msg, "tool_calls") and msg.tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": getattr(tc, "type", "function"),
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]

    if hasattr(msg, "tool_call_id") and msg.tool_call_id:
        result["tool_call_id"] = msg.tool_call_id

    return result


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
