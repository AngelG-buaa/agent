"""上下文压缩管线 —— 四层渐进式压缩，每轮 LLM 调用前自动运行。

执行顺序: L3 → L1 → L2 → L4（L3 必须在 L2 之前，因为 L2 会替换掉大内容）。
原则: 便宜的先跑，贵的后跑（L1-L3 零 API 调用，L4 一次 API 调用）。

参考: Claude Code autoCompact / s08_context_compact 四层管线设计。
"""

import json
import os
from pathlib import Path

from config import compaction as cfg
from agent.utils import (
    get_role,
    get_content,
    get_tool_calls,
    get_tool_call_id,
    set_content,
    to_serializable,
)

# ---------------------------------------------------------------------------
# 目录常量
# ---------------------------------------------------------------------------

_WORKDIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL_RESULTS_DIR = _WORKDIR / ".task_outputs" / "tool-results"

# ---------------------------------------------------------------------------
# 阈值（从 CompactionConfig 读取，此处作为模块级快捷引用）
# ---------------------------------------------------------------------------

CONTEXT_LIMIT = cfg.context_limit
MAX_MESSAGES_SNIP = cfg.max_messages_snip
HEAD_COUNT = cfg.head_count
KEEP_RECENT = cfg.keep_recent_tool_results
MIN_CONTENT_LENGTH = cfg.min_content_length
TOOL_RESULT_BUDGET_BYTES = cfg.tool_result_budget_bytes
PERSIST_THRESHOLD = cfg.persist_threshold
PERSIST_PREVIEW_CHARS = cfg.persist_preview_chars
SUMMARY_MAX_TOKENS = cfg.summary_max_tokens
SUMMARY_RETRY_COUNT = cfg.summary_retry_count
SUMMARY_INPUT_CAP = cfg.summary_input_cap

PLACEHOLDER = "[Earlier tool result compacted. Re-run if needed.]"


# ---------------------------------------------------------------------------
# Compact 专属判断 Helper
# ---------------------------------------------------------------------------

def _has_tool_calls(msg) -> bool:
    """消息是 assistant 且包含 tool_calls。"""
    return get_role(msg) == "assistant" and bool(get_tool_calls(msg))


def _is_tool_for_ids(msg, tc_ids: set) -> bool:
    """消息是 tool 结果，且 tool_call_id 属于给定集合。"""
    return get_role(msg) == "tool" and get_tool_call_id(msg) in tc_ids


def _has_tool_call_id(msg, tc_id: str) -> bool:
    """消息是 assistant 且 tool_calls 中包含给定的 id。"""
    if get_role(msg) != "assistant":
        return False
    return any(tc["id"] == tc_id for tc in get_tool_calls(msg))


def _estimate_size(messages: list) -> int:
    """估算消息列表的字符数。先归一化再计算。"""
    serializable = [to_serializable(m) for m in messages]
    return len(str(serializable))


def _ensure_dir(path: Path) -> None:
    """确保目录存在，不存在则创建。"""
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# L3: tool_result_budget — 大结果持久化到磁盘 (0 API)
# ---------------------------------------------------------------------------

def tool_result_budget(messages: list) -> None:
    """找到最近一轮 assistant tool_calls 对应的所有 tool 消息。

    如果总大小 > TOOL_RESULT_BUDGET_BYTES 且存在单个 > PERSIST_THRESHOLD，
    则按从大到小顺序持久化到磁盘，直到总量降到阈值以下。
    """
    # 1. 找到最近一条 assistant 消息（含 tool_calls）
    last_assistant_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if get_role(messages[i]) == "assistant" and get_tool_calls(messages[i]):
            last_assistant_idx = i
            break
    if last_assistant_idx is None:
        return

    # 2. 收集这轮 tool_calls 的所有 tool_call_id
    tc_ids = {tc["id"] for tc in get_tool_calls(messages[last_assistant_idx])}

    # 3. 找到这些 tool_call_id 对应的 tool 消息（在 assistant 消息之后）
    tool_msgs: list[tuple[int, object]] = []
    for i in range(last_assistant_idx + 1, len(messages)):
        if get_role(messages[i]) == "tool" and get_tool_call_id(messages[i]) in tc_ids:
            tool_msgs.append((i, messages[i]))

    if not tool_msgs:
        return

    # 4. 计算总大小
    total = sum(len(get_content(m)) for _, m in tool_msgs)
    if total <= TOOL_RESULT_BUDGET_BYTES:
        return

    # 5. 按内容从大到小排序
    tool_msgs.sort(key=lambda m: len(get_content(m[1])), reverse=True)

    # 6. 依次持久化，直到总量降到阈值以下
    _ensure_dir(TOOL_RESULTS_DIR)
    for _idx, msg in tool_msgs:
        content = get_content(msg)
        if len(content) <= PERSIST_THRESHOLD:
            continue

        tool_call_id = get_tool_call_id(msg) or "unknown"
        filepath = TOOL_RESULTS_DIR / f"{tool_call_id}.txt"
        filepath.write_text(content, encoding="utf-8")

        # 替换消息内容为预览 + 指针
        preview = content[:PERSIST_PREVIEW_CHARS]
        set_content(msg, (
            f"<persisted-output>\n"
            f"Full output: {filepath}\n"
            f"Preview:\n{preview}\n"
            f"</persisted-output>"
        ))

        # 重新计算总量
        total = sum(len(get_content(m)) for _, m in tool_msgs)
        if total <= TOOL_RESULT_BUDGET_BYTES:
            break


# ---------------------------------------------------------------------------
# L1: snip_compact — 消息截断 (0 API)
# ---------------------------------------------------------------------------

def snip_compact(messages: list, max_messages: int = MAX_MESSAGES_SNIP) -> None:
    """当消息总数 > max_messages 时，保留前 HEAD_COUNT 条和后 N 条。

    中间替换为一条 snipped 占位符。
    边界保护: 确保 tool_calls/tool 消息配对不被撕开。
    """
    if len(messages) <= max_messages:
        return

    keep_head = HEAD_COUNT
    keep_tail = max_messages - HEAD_COUNT

    head_end = keep_head
    tail_start = len(messages) - keep_tail

    # ---- 边界保护 1: head 最后一条是 assistant tool_calls ----
    if head_end > 0 and _has_tool_calls(messages[head_end - 1]):
        tc_ids = {tc["id"] for tc in get_tool_calls(messages[head_end - 1])}
        while head_end < len(messages) and _is_tool_for_ids(messages[head_end], tc_ids):
            head_end += 1

    # ---- 边界保护 2: tail 第一条是孤立的 tool 消息 ----
    if tail_start > 0 and get_role(messages[tail_start]) == "tool":
        tc_id = get_tool_call_id(messages[tail_start])
        if tc_id and tail_start > 0 and _has_tool_call_id(messages[tail_start - 1], tc_id):
            tail_start -= 1

    # ---- 检查是否重叠 ----
    if head_end >= tail_start:
        return

    # ---- 执行截断 ----
    snipped_count = tail_start - head_end
    snipped_msg: dict = {
        "role": "user",
        "content": f"[snipped {snipped_count} messages]",
    }
    messages[:] = messages[:head_end] + [snipped_msg] + messages[tail_start:]


# ---------------------------------------------------------------------------
# L2: micro_compact — 旧工具结果占位符化 (0 API)
# ---------------------------------------------------------------------------

def micro_compact(messages: list) -> None:
    """保留最新 KEEP_RECENT 个 tool 消息的完整内容。

    其余 content 长度 > MIN_CONTENT_LENGTH 的替换为占位符。
    短内容 (≤ MIN_CONTENT_LENGTH) 不受影响。
    """
    tool_indices = [
        i for i, msg in enumerate(messages)
        if get_role(msg) == "tool"
    ]

    if len(tool_indices) <= KEEP_RECENT:
        return

    for idx in tool_indices[:-KEEP_RECENT]:
        content = get_content(messages[idx])
        if len(content) > MIN_CONTENT_LENGTH:
            set_content(messages[idx], PLACEHOLDER)


# ---------------------------------------------------------------------------
# L4: compact_history — LLM 摘要 (1 API)
# ---------------------------------------------------------------------------

def compact_history(messages: list, llm) -> None:
    """调用 LLM 生成摘要，并用摘要替代历史消息。

    重试机制: 最多重试 SUMMARY_RETRY_COUNT 次，全部失败则降级跳过。
    Post-compact: 恢复 CURRENT_TODOS（若非空）。
    """
    # 1. 构建摘要 prompt（中文，5 维度）
    serializable = [to_serializable(m) for m in messages]
    conversation_text = json.dumps(serializable, ensure_ascii=False, default=str)
    if len(conversation_text) > SUMMARY_INPUT_CAP:
        conversation_text = conversation_text[:SUMMARY_INPUT_CAP]

    summary_prompt = (
        "请总结以下AI助手的对话历史，使工作可以继续。\n"
        "必须保留：\n"
        "1. 当前目标和任务\n"
        "2. 关键发现和决策\n"
        "3. 已读取和已修改的文件\n"
        "4. 剩余工作\n"
        "5. 用户约束和偏好\n"
        "请简洁但具体。\n\n"
        + conversation_text
    )

    # 2. 带重试的 LLM 摘要调用
    summary = None
    for attempt in range(SUMMARY_RETRY_COUNT + 1):
        try:
            _, response = llm.chat(
                messages=[{"role": "user", "content": summary_prompt}],
                tools=[],
                max_tokens=SUMMARY_MAX_TOKENS,
            )
            summary = response.content if hasattr(response, "content") else str(response)
            if summary:
                break
        except Exception:
            if attempt == SUMMARY_RETRY_COUNT:
                return
            continue

    if not summary:
        return

    # 3. 保留 system 消息，其余替换为摘要
    system_msg = messages[0] if messages and get_role(messages[0]) == "system" else None
    compact_msg: dict = {
        "role": "user",
        "content": f"[Compacted]\n\n{summary}",
    }
    messages[:] = [compact_msg]
    if system_msg:
        messages.insert(0, system_msg)

    # 4. Post-compact: 恢复 Todo 列表
    _restore_todos(messages)


def _restore_todos(messages: list) -> None:
    """L4 压缩后恢复 Todo 列表到 messages。"""
    try:
        from tools.todo_write import CURRENT_TODOS  # noqa: PLC0415
    except ImportError:
        return

    if not CURRENT_TODOS:
        return

    status_icons = {"pending": " ", "in_progress": "▸", "completed": "✓"}
    lines = ["## 当前任务进度（压缩后恢复）", ""]
    for t in CURRENT_TODOS:
        icon = status_icons.get(t.get("status", "pending"), " ")
        lines.append(f"- [{icon}] {t.get('content', '')}")

    messages.append({
        "role": "user",
        "content": "\n".join(lines),
    })


# ---------------------------------------------------------------------------
# compact_pipeline — 总调度入口
# ---------------------------------------------------------------------------

def compact_pipeline(messages: list, llm) -> None:
    """四层压缩管线，原地修改 messages。执行顺序 L3→L1→L2→L4。

    Agent.run() 每轮 LLM 调用前调用此函数。
    """
    # L3: 大结果持久化 —— 在 L2 之前执行，因为 L2 会替换掉大内容
    tool_result_budget(messages)

    # L1: 消息截断
    snip_compact(messages)

    # L2: 旧结果占位符 —— 在 L4 之前执行，尽量让 L4 不必触发
    micro_compact(messages)

    # L4: LLM 摘要 —— 前三层都挡不住时，发起 API 调用
    if _estimate_size(messages) > CONTEXT_LIMIT:
        print("[auto compact]")
        compact_history(messages, llm)
