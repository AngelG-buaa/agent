"""Session 列表交互式 UI —— 箭头键选择，纯标准库实现。

Windows: msvcrt.getch(), Unix: termios + tty。
不依赖 SessionManager、Conversation 或任何其他模块。
"""

from __future__ import annotations

import os
import sys


def _get_key() -> str:
    """读取单个按键。跨平台兼容。"""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getch()
        if ch == b"\xe0":
            ch2 = msvcrt.getch()
            arrow_map = {b"H": "UP", b"P": "DOWN", b"K": "LEFT", b"M": "RIGHT"}
            return arrow_map.get(ch2, f"\xe0{ch2.decode('latin-1')}")
        try:
            return ch.decode("utf-8")
        except UnicodeDecodeError:
            return ch.decode("latin-1")
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.buffer.read(1)
            if ch == b"\x1b":
                seq = sys.stdin.buffer.read(2)
                if seq == b"[A":
                    return "UP"
                elif seq == b"[B":
                    return "DOWN"
                elif seq == b"[C":
                    return "RIGHT"
                elif seq == b"[D":
                    return "LEFT"
                return f"ESC{seq.decode('latin-1')}"
            if ch == b"\r":
                return "ENTER"
            return ch.decode("utf-8")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _render_list(sessions: list, selected_idx: int) -> None:
    """渲染 session 列表。"""
    # 清屏
    print("\033[2J\033[H", end="")
    print("┌──────────────────────────────────────────────────┐")
    print("│  Sessions  (↑↓: Navigate  Enter: Select  Q: Cancel)│")
    print("├──────────────────────────────────────────────────┤")
    if not sessions:
        print("│  (no sessions)                                   │")
    else:
        for i, s in enumerate(sessions):
            prefix = " >" if i == selected_idx else "  "
            title = s.title[:45] if s.title else "Untitled"
            updated = s.updated_at[:10] if s.updated_at else "N/A"
            line = f"{prefix} {title:<45s} {updated}"
            # 补足到固定宽度
            print(f"│{line:<50s}│")
    print("└──────────────────────────────────────────────────┘")


def select_session(sessions: list) -> str | None:
    """交互式 session 列表选择。

    Args:
        sessions: SessionSummary 列表（只读纯数据，不 import SessionManager）。

    Returns:
        选中的 session_id，或 None（取消/退出）。
    """
    if not sessions:
        print("No saved sessions found.")
        return None

    idx = 0
    while True:
        _render_list(sessions, idx)
        key = _get_key()

        if key == "UP":
            idx = (idx - 1) % len(sessions)
        elif key == "DOWN":
            idx = (idx + 1) % len(sessions)
        elif key in ("ENTER", "\r", "\n"):
            return sessions[idx].id
        elif key.lower() in ("q",):
            return None
        elif key == "\x03":  # Ctrl+C
            return None


def confirm_delete(title: str) -> bool:
    """删除确认提示。返回 True 表示确认。"""
    response = input(f"Delete session '{title}'? Are you sure? [y/N] ").strip().lower()
    return response in ("y", "yes")


def prompt_rename(current_title: str) -> str:
    """重命名输入。返回新标题（空输入 = 不变）。"""
    new_title = input(f"New title [{current_title}]: ").strip()
    return new_title if new_title else current_title


def show_actions_menu() -> str | None:
    """选中 session 后的操作菜单。返回 'resume'/'delete'/'rename'/None。"""
    print("\n[R]esume  [D]elete  Re[N]ame  [C]ancel")
    try:
        choice = input("Action: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if choice in ("r", "resume"):
        return "resume"
    if choice in ("d", "delete"):
        return "delete"
    if choice in ("n", "rename"):
        return "rename"
    return None
