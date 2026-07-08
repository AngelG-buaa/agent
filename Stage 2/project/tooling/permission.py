"""权限审批核心模块 —— 多来源规则 UNION 合并 + 3 步评估管线。

职责边界:
  - 本模块: 权限规则的收集、合并、评估。对外暴露 PermissionEngine 和工厂函数。
  - 不负责: I/O（审批交互由 ToolExecutor 负责）、工具执行、Agent 循环。
"""

import fnmatch
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


# ============================================================
# RuleBehavior 枚举
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
        rule_content: shell glob pattern 或条件描述
        message: 命中时给用户的说明文字
        rule_id: 唯一标识（Session 来源用于增删）
        condition: 复杂条件函数，为 None 时用 rule_content 做 fnmatch 匹配
    """

    tool_name: str
    rule_behavior: RuleBehavior
    rule_content: str
    message: str
    rule_id: str | None = None
    condition: Callable[[str, dict], bool] | None = None

    def matches(self, tool_name: str, params: dict, target: str = "") -> bool:
        """检查此规则是否匹配给定的工具调用。"""
        if self.tool_name != "*" and tool_name != self.tool_name:
            return False
        try:
            if self.condition is not None:
                return self.condition(tool_name, params)
            return _match_content(self.rule_content, target)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 条件构造器
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# RuleSource 抽象基类
# ---------------------------------------------------------------------------


class RuleSource(ABC):
    """规则来源抽象基类。所有来源的规则由 PermissionEngine 做 UNION 合并。"""

    @abstractmethod
    def get_rules(self) -> list[PermissionRule]:
        ...


# ---------------------------------------------------------------------------
# PolicySettingsSource — 内置安全策略
# ---------------------------------------------------------------------------


def _build_policy_rules(workspace_dir: Path) -> list[PermissionRule]:
    """构建内置安全策略规则。"""

    rules: list[PermissionRule] = []

    # ================================================================
    # Deny — 绝对拒绝
    # ================================================================

    deny_specs = [
        ("bash", "rm -rf /*",       "禁止递归删除根目录",       "policy-deny-rm-root",       _cmd_contains("rm -rf /")),
        ("bash", "sudo *",          "禁止提权操作",              "policy-deny-sudo",          _cmd_contains("sudo")),
        ("bash", "shutdown|reboot", "禁止关机/重启操作",         "policy-deny-shutdown",      _cmd_contains_any(["shutdown", "reboot", "halt", "poweroff"])),
        ("bash", "mkfs *",          "禁止格式化文件系统",        "policy-deny-mkfs",          _cmd_contains("mkfs")),
        ("bash", "dd if=*",         "禁止磁盘直接写入",          "policy-deny-dd",            _cmd_contains("dd if=")),
        ("bash", "> /dev/*",        "禁止覆写磁盘设备",          "policy-deny-device",        lambda _t, p: "> /dev/sda" in p.get("command", "") or "> /dev/nvme" in p.get("command", "")),
        ("bash", "fork bomb",       "禁止 fork bomb",            "policy-deny-forkbomb",      _cmd_contains(":(){ :|:& };:")),
    ]

    for tool, content, msg, rid, cond in deny_specs:
        rules.append(PermissionRule(tool, RuleBehavior.DENY, content, msg, rid, condition=cond))

    # 文件工具: 系统路径 + SSH 路径 → deny
    for tool in ("write_file", "edit_file"):
        rules.append(PermissionRule(
            tool, RuleBehavior.DENY, "/etc/* | /proc/* | /sys/* | /boot/*",
            "禁止写入系统目录", f"policy-deny-system-{tool}",
            condition=_path_contains_any(["/etc/", "/proc/", "/sys/", "/boot/"]),
        ))
        rules.append(PermissionRule(
            tool, RuleBehavior.DENY, "~/.ssh/* | .ssh/*",
            "禁止篡改 SSH 密钥", f"policy-deny-ssh-{tool}",
            condition=_path_contains_any(["~/.ssh/", ".ssh/"]),
        ))

    # ================================================================
    # Ask — 需要用户确认
    # ================================================================

    # 文件工具: 超出工作区 → ask
    for tool in ("write_file", "edit_file"):
        rules.append(PermissionRule(
            tool, RuleBehavior.ASK, "*",
            "文件操作超出工作区范围", f"policy-ask-outside-{tool}",
            condition=_path_outside_dir(workspace_dir),
        ))

    # 文件工具: 项目受保护路径 → ask
    protected_patterns = [
        (".git/*",     "policy-ask-protected-git"),
        (".claude/*",  "policy-ask-protected-claude"),
        (".vscode/*",  "policy-ask-protected-vscode"),
    ]
    for tool in ("write_file", "edit_file"):
        for pattern, rid_prefix in protected_patterns:
            rules.append(PermissionRule(
                tool, RuleBehavior.ASK, pattern,
                f"写入项目受保护路径: {pattern}", f"{rid_prefix}-{tool}",
            ))

    # bash 内容匹配 ask 规则
    import re

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
        rules.append(PermissionRule(tool, RuleBehavior.ASK, content, msg, rid, condition=cond))

    return rules


class PolicySettingsSource(RuleSource):
    """内置安全策略来源。规则数据由 _build_policy_rules() 生成。"""

    def __init__(self, workspace_dir: str | Path):
        self._workspace_dir = Path(workspace_dir)

    def get_rules(self) -> list[PermissionRule]:
        return _build_policy_rules(self._workspace_dir)


# ---------------------------------------------------------------------------
# SessionSource — 运行时动态规则
# ---------------------------------------------------------------------------


class SessionSource(RuleSource):
    """运行时动态增删的规则来源。

    用法:
        engine.session.add(PermissionRule("write_file", RuleBehavior.ALLOW, "*", "本次对话允许"))
        engine.session.remove(rule_id)
    """

    def __init__(self):
        self._rules: dict[str, PermissionRule] = {}
        self._counter = 0

    def get_rules(self) -> list[PermissionRule]:
        return list(self._rules.values())

    def add(self, rule: PermissionRule) -> str:
        if rule.rule_id is None:
            self._counter += 1
            rule.rule_id = f"session-{self._counter}"
        self._rules[rule.rule_id] = rule
        return rule.rule_id

    def remove(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            del self._rules[rule_id]
            return True
        return False

    def clear(self) -> None:
        self._rules.clear()


# ---------------------------------------------------------------------------
# 规则匹配
# ---------------------------------------------------------------------------


def _match_content(pattern: str, target: str) -> bool:
    """rule_content shell glob 匹配 target 字符串。"""
    if pattern == "*":
        return True
    if not target:
        return False
    return fnmatch.fnmatch(target, pattern)


# ---------------------------------------------------------------------------
# PermissionEngine
# ---------------------------------------------------------------------------


class PermissionEngine:
    """权限引擎：UNION 合并 + 3 步管线。

    管线:
      Gate 1: deny 规则 → 拒绝
      Gate 2: allow 规则 → 放行 (Session 预授权)
      Gate 3: ask 规则 → 审批
      Fallback: default_behavior
    """

    def __init__(
        self,
        sources: list[RuleSource] | None = None,
        default_behavior: str = "allow",
    ):
        self.sources = sources or []
        self.default_behavior = default_behavior
        self.session = SessionSource()

    def evaluate(self, tool_name: str, params: dict, tool=None) -> tuple[str, str | None]:
        """评估工具调用权限。返回 (behavior, reason)。"""
        target = tool.permission_target(params) if tool else ""

        deny_pool, allow_pool, ask_pool = self._collect_all_rules()

        # Gate 1: deny
        for r in deny_pool:
            if r.matches(tool_name, params, target):
                return ("deny", r.message)

        # Gate 2: allow (Session 预授权)
        for r in allow_pool:
            if r.matches(tool_name, params, target):
                return ("allow", None)

        # Gate 3: ask → 审批
        for r in ask_pool:
            if r.matches(tool_name, params, target):
                return ("ask", r.message)

        # Fallback
        return (self.default_behavior, None)

    def _collect_all_rules(self) -> tuple[list[PermissionRule], list[PermissionRule], list[PermissionRule]]:
        """UNION 合并所有来源的规则，去重后返回三池。"""
        seen: set[tuple[str, RuleBehavior, str]] = set()
        deny: list[PermissionRule] = []
        allow: list[PermissionRule] = []
        ask: list[PermissionRule] = []

        all_rules: list[PermissionRule] = []
        all_rules.extend(self.session.get_rules())
        for s in self.sources:
            all_rules.extend(s.get_rules())

        for r in all_rules:
            key = (r.tool_name, r.rule_behavior, r.rule_content)
            if key in seen:
                continue
            seen.add(key)
            if r.rule_behavior == RuleBehavior.DENY:
                deny.append(r)
            elif r.rule_behavior == RuleBehavior.ALLOW:
                allow.append(r)
            elif r.rule_behavior == RuleBehavior.ASK:
                ask.append(r)

        return (deny, allow, ask)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def create_engine(
    project_root: str | Path | None = None,
    default_behavior: str = "allow",
) -> PermissionEngine:
    """创建 PermissionEngine，自动组装 PolicySettingsSource。

    Args:
        project_root: 项目根目录，同时用作安全边界和策略配置目录。
                      None 则使用 cwd。
        default_behavior: 未命中时的默认行为。
    """
    root = Path(project_root) if project_root else Path.cwd()

    return PermissionEngine(
        sources=[PolicySettingsSource(workspace_dir=root)],
        default_behavior=default_behavior,
    )
