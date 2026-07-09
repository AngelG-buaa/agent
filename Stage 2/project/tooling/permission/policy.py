"""权限规则定义 + 条件函数 + 内置安全策略。
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


# ============================================================
# RuleBehavior
# ============================================================


class RuleBehavior(Enum):
    """权限规则行为类型。"""
    DENY = "deny"
    ASK = "ask"
    ALLOW = "allow"


# ============================================================
# PermissionRule
# ============================================================


@dataclass
class PermissionRule:
    """单条权限规则。

    Args:
        tool_name: 工具名或 "*"（所有工具）
        rule_behavior: deny / ask / allow
        rule_content: 规则摘要，用于去重和显示
        message: 命中时给用户的说明文字
        rule_id: 唯一标识（会话规则用于 revoke）
        condition: 匹配函数 (tool_name, params) -> bool
    """

    tool_name: str
    rule_behavior: RuleBehavior
    rule_content: str
    message: str
    condition: Callable[[str, dict], bool]
    rule_id: str | None = None

    def matches(self, tool_name: str, params: dict) -> bool:
        """检查此规则是否匹配给定的工具调用。"""
        if self.tool_name not in ("*", tool_name):
            return False
        try:
            return self.condition(tool_name, params)
        except Exception:
            # 条件函数异常时，按规则类型决定匹配结果：
            # deny/ask → True（宁可误拦/多问，不可漏过）
            # allow   → False（宁可不多授权，不可误放行）
            return self.rule_behavior != RuleBehavior.ALLOW


# ============================================================
# 条件函数
# ============================================================


def _cmd_contains_any(keywords: list[str]) -> Callable[[str, dict], bool]:
    def condition(_tool_name: str, params: dict) -> bool:
        command = params.get("command", "").lower()
        return any(kw.lower() in command for kw in keywords)
    return condition


def _cmd_contains(keyword: str) -> Callable[[str, dict], bool]:
    return _cmd_contains_any([keyword])


def _path_contains_any(segments: list[str]) -> Callable[[str, dict], bool]:
    def condition(_tool_name: str, params: dict) -> bool:
        path = params.get("path", "").lower()
        return any(s.lower() in path for s in segments)
    return condition


def _path_outside_dir(base_dir: Path) -> Callable[[str, dict], bool]:
    def condition(_tool_name: str, params: dict) -> bool:
        path_str = params.get("path", "")
        try:
            resolved = (base_dir / path_str).resolve()
            return not resolved.is_relative_to(base_dir.resolve())
        except (ValueError, OSError):
            return True
    return condition


# ============================================================
# 内置安全策略 (19 条规则)
# ============================================================


def build_rules(workspace_dir: Path) -> list[PermissionRule]:
    """构建全部内置 deny/ask 规则。"""

    rules: list[PermissionRule] = []

    # ── Deny: bash 危险命令 ──

    deny_specs = [
        ("bash", "rm -rf /*",       "禁止递归删除根目录",       "policy-deny-rm-root",       _cmd_contains("rm -rf /")),
        ("bash", "sudo *",          "禁止提权操作",              "policy-deny-sudo",          _cmd_contains("sudo")),
        ("bash", "shutdown|reboot", "禁止关机/重启操作",         "policy-deny-shutdown",      _cmd_contains_any(["shutdown", "reboot", "halt", "poweroff"])),
        ("bash", "mkfs *",          "禁止格式化文件系统",        "policy-deny-mkfs",          _cmd_contains("mkfs")),
        ("bash", "dd if=*",         "禁止磁盘直接写入",          "policy-deny-dd",            _cmd_contains("dd if=")),
        ("bash", "> /dev/*",        "禁止覆写磁盘设备",          "policy-deny-device",        lambda _t, p: "> /dev/sda" in p.get("command", "") or "> /dev/nvme" in p.get("command", "")),
        ("bash", "fork bomb",       "禁止 fork bomb",            "policy-deny-forkbomb",      _cmd_contains(":(){ :|:& };:")),
        ("bash", "python -c *",    "禁止 Python 单行执行（绕过检测）",    "policy-deny-python-c",     _cmd_contains_any(["python -c", "python3 -c", "python -c "])),
        ("bash", "node -e *",      "禁止 Node.js 单行执行（绕过检测）",  "policy-deny-node-e",       _cmd_contains("node -e")),
        ("bash", "perl -e *",      "禁止 Perl 单行执行（绕过检测）",     "policy-deny-perl-e",       _cmd_contains("perl -e")),
        ("bash", "ruby -e *",      "禁止 Ruby 单行执行（绕过检测）",     "policy-deny-ruby-e",       _cmd_contains("ruby -e")),
        ("bash", "powershell -c *","禁止 PowerShell 单行执行（绕过检测）","policy-deny-powershell-c", _cmd_contains_any(["powershell -c", "powershell -Command", "pwsh -c", "pwsh -Command"])),
    ]

    for tool, content, msg, rid, cond in deny_specs:
        rules.append(PermissionRule(tool, RuleBehavior.DENY, content, msg, cond, rule_id=rid))

    # ── Deny: 文件工具系统路径 ──

    for tool in ("write_file", "edit_file"):
        rules.append(PermissionRule(
            tool, RuleBehavior.DENY, "/etc/* | /proc/* | /sys/* | /boot/*",
            "禁止写入系统目录", _path_contains_any(["/etc/", "/proc/", "/sys/", "/boot/"]),
            rule_id=f"policy-deny-system-{tool}",
        ))
        rules.append(PermissionRule(
            tool, RuleBehavior.DENY, "~/.ssh/* | .ssh/*",
            "禁止篡改 SSH 密钥", _path_contains_any(["~/.ssh/", ".ssh/"]),
            rule_id=f"policy-deny-ssh-{tool}",
        ))

    # ── Ask: 文件工具工作区边界 ──

    for tool in ("write_file", "edit_file"):
        rules.append(PermissionRule(
            tool, RuleBehavior.ASK, "*",
            "文件操作超出工作区范围", _path_outside_dir(workspace_dir),
            rule_id=f"policy-ask-outside-{tool}",
        ))

    # ── Ask: 文件工具受保护路径 ──

    for tool in ("write_file", "edit_file"):
        for pattern, rid_prefix in [
            (".git/*", "policy-ask-protected-git"),
            (".claude/*", "policy-ask-protected-claude"),
            (".vscode/*", "policy-ask-protected-vscode"),
        ]:
            rules.append(PermissionRule(
                tool, RuleBehavior.ASK, pattern,
                f"写入项目受保护路径: {pattern}", _path_contains_any([pattern.rstrip("/*")]),
                rule_id=f"{rid_prefix}-{tool}",
            ))

    # ── Ask: bash 敏感操作 ──

    ask_specs = [
        ("bash", "curl|wget | sh",    "下载并管道执行脚本",        "policy-ask-pipe",
         lambda _t, p: (
             any(x in p.get("command", "").lower() for x in ("curl", "wget"))
             and any(x in re.sub(r'\s+', ' ', p.get("command", "")) for x in ("| sh", "| bash"))
         )),
        ("bash", "git push --force *","强制推送",                  "policy-ask-force-push",  _cmd_contains("git push --force")),
        ("bash", "docker rm|rmi *",   "删除 Docker 容器/镜像",     "policy-ask-docker-rm",   _cmd_contains_any(["docker rm", "docker rmi"])),
        ("bash", "npm -g | pip inst", "全局安装软件包",            "policy-ask-install",
         lambda _t, p: (
             "npm install -g" in p.get("command", "").lower()
             or ("pip install" in p.get("command", "").lower() and "--user" not in p.get("command", ""))
         )),
        ("bash", "rm *",              "执行文件删除命令",          "policy-ask-rm",
         lambda _t, p: "rm " in p.get("command", "") and "docker " not in p.get("command", "").lower()),
        ("bash", "> /etc/*",          "重定向写入系统配置",        "policy-ask-etc",
         lambda _t, p: "> /etc/" in p.get("command", "") or ">> /etc/" in p.get("command", "")),
        ("bash", "chmod 777 *",      "修改为全员可写权限",        "policy-ask-chmod777",    _cmd_contains("chmod 777")),
        ("bash", "chown *",          "修改文件所有者",            "policy-ask-chown",       _cmd_contains("chown")),
    ]

    for tool, content, msg, rid, cond in ask_specs:
        rules.append(PermissionRule(tool, RuleBehavior.ASK, content, msg, cond, rule_id=rid))

    return rules
