"""权限审批核心模块 —— 三道闸门 + 多来源规则合并。

设计参考 Claude Code 权限架构：
  - 两维度: 行为闸门(deny→ask→allow) × 规则来源(有序合并)
  - 所有来源的规则合并为 deny/ask/allow 三池
  - 按闸门顺序检查，首次匹配即返回
  - deny 池永远最先检查，因此 deny 规则不可被 allow 撤销

来源合并规则:
  - 同行为规则来自各源的并集(非替换)
  - PolicySettings 的 deny 规则永远在 deny 池首位
"""

import fnmatch
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ============================================================
@dataclass
class PermissionRule:
    """单条权限规则。

    Args:
        behavior: "deny" | "ask" | "allow"
        tool_names: 适用的工具名列表，["*"] 表示所有工具
        condition: 判断函数 (tool_name, params) -> bool
        message: 命中时给用户的说明文字
        rule_id: 唯一标识（Session 来源用于增删）
    """
    behavior: str
    tool_names: list[str]
    condition: Callable[[str, dict], bool]
    message: str
    rule_id: str | None = None


# ============================================================
# 🔮 预留扩展: PermissionMode
# 用途: 支持 default / acceptEdits / plan / bypass / dontAsk / auto 六种全局模式
# 参考: Claude Code 的 permissions.defaultMode
# 当前: 仅实现 default 模式（交互式审批），其他模式通过 PermissionEngine.default_behavior 字段预留
# 实现时: 在 PermissionEngine.__init__ 增加 mode 参数，根据模式调整 default_behavior 和审批流程
# ============================================================


# ============================================================
# 🔮 预留扩展: AutoApprover
# 用途: 在 ASK 之前调用 LLM 分类器自动判断安全性，
#       命中则跳过人工审批直接放行
# 参考: Claude Code 的 YoloClassifier / classifyYoloAction
# 当前: 未实现，所有 ASK 一律走终端人工审批
# 实现时: 在 PermissionEngine.evaluate() 中 ASK 返回前
#         插入 auto_approver.evaluate(tool_name, params) 调用，
#         若返回 ALLOW 则直接放行，若返回 DENY 则拒绝，返回 ASK 则继续人工审批
# ============================================================


# ============================================================
# 🔮 预留扩展: Permission Bubbling
# 用途: 子 Agent 的权限弹窗冒泡到父 Agent 终端
# 参考: Claude Code AgentTool fork 时设置 permissionMode='bubble'
# 当前: 本项目 Agent 无法 fork 子 Agent，不实现此特性
# 实现时: 在 PermissionRule 增加 bubble: bool 字段，
#         PermissionEngine 增加 mode="local"|"bubble" 参数
# ============================================================


# ---------------------------------------------------------------------------
# 条件构造器（工厂函数）
# ---------------------------------------------------------------------------

def cmd_contains(keyword: str) -> Callable[[str, dict], bool]:
    """bash 命令中包含指定关键词（大小写不敏感）。"""
    def condition(_tool_name: str, params: dict) -> bool:
        command = params.get("command", "")
        return keyword.lower() in command.lower()
    return condition


def cmd_matches_pattern(pattern: str) -> Callable[[str, dict], bool]:
    """bash 命令匹配 shell 风格通配符。"""
    def condition(_tool_name: str, params: dict) -> bool:
        command = params.get("command", "")
        return fnmatch.fnmatch(command, pattern)
    return condition


def path_contains(segment: str) -> Callable[[str, dict], bool]:
    """文件路径中包含指定片段（大小写不敏感）。"""
    def condition(_tool_name: str, params: dict) -> bool:
        path = params.get("path", "")
        return segment.lower() in path.lower()
    return condition


def path_matches_pattern(pattern: str) -> Callable[[str, dict], bool]:
    """文件路径匹配 shell 风格通配符。"""
    def condition(_tool_name: str, params: dict) -> bool:
        path = params.get("path", "")
        return fnmatch.fnmatch(path, pattern)
    return condition


def cmd_contains_any(keywords: list[str]) -> Callable[[str, dict], bool]:
    """bash 命令中包含任意一个关键词。"""
    def condition(_tool_name: str, params: dict) -> bool:
        command = params.get("command", "").lower()
        return any(kw.lower() in command for kw in keywords)
    return condition


def cmd_contains_pipe_exec() -> Callable[[str, dict], bool]:
    """bash 命令中含 curl/wget 管道执行（curl ... | sh 或 wget ... | bash）。"""
    def condition(_tool_name: str, params: dict) -> bool:
        command = params.get("command", "").lower()
        has_download = "curl" in command or "wget" in command
        has_pipe_exec = "| sh" in command or "| bash" in command or "|sh" in command or "|bash" in command
        return has_download and has_pipe_exec
    return condition


def path_outside_workdir(workdir: Path) -> Callable[[str, dict], bool]:
    """文件路径解析后不在 WORKDIR 子树内。"""
    def condition(_tool_name: str, params: dict) -> bool:
        path_str = params.get("path", "")
        try:
            resolved = (workdir / path_str).resolve()
            return not resolved.is_relative_to(workdir.resolve())
        except (ValueError, OSError):
            return True  # 无法解析时保守处理：视为在工作区外
    return condition


# ---------------------------------------------------------------------------
# RuleSource 抽象基类
# ---------------------------------------------------------------------------

class RuleSource(ABC):
    """规则来源抽象基类。

    每个来源实现 get_rules()，返回其贡献的规则列表。
    所有来源的规则由 PermissionEngine 合并到三池中。
    """

    @abstractmethod
    def get_rules(self) -> list[PermissionRule]:
        """返回该来源的所有规则。"""
        ...


# ---------------------------------------------------------------------------
# PolicySettingsSource — 内置安全底线
# ---------------------------------------------------------------------------

class PolicySettingsSource(RuleSource):
    """内置安全策略 —— 硬编码的 deny + ask 规则。

    这是安全底线，规则不可被用户移除。deny 规则在所有池中最先检查。
    """

    def __init__(self, workdir: str | Path | None = None):
        self._workdir = Path(workdir) if workdir else Path.cwd()

    def get_rules(self) -> list[PermissionRule]:
        rules: list[PermissionRule] = []

        # ---- deny 规则（不可覆盖）----

        rules.append(PermissionRule(
            "deny", ["bash"],
            cmd_contains("rm -rf /"),
            "禁止递归删除根目录",
            rule_id="policy-deny-rm-root",
        ))
        rules.append(PermissionRule(
            "deny", ["bash"],
            cmd_contains("sudo"),
            "禁止提权操作",
            rule_id="policy-deny-sudo",
        ))
        rules.append(PermissionRule(
            "deny", ["bash"],
            cmd_contains_any(["shutdown", "reboot", "halt", "poweroff"]),
            "禁止关机/重启操作",
            rule_id="policy-deny-shutdown",
        ))
        rules.append(PermissionRule(
            "deny", ["bash"],
            cmd_contains("mkfs"),
            "禁止格式化文件系统",
            rule_id="policy-deny-mkfs",
        ))
        rules.append(PermissionRule(
            "deny", ["bash"],
            cmd_contains("dd if="),
            "禁止磁盘直接写入",
            rule_id="policy-deny-dd",
        ))
        rules.append(PermissionRule(
            "deny", ["bash"],
            lambda t, p: "> /dev/sda" in p.get("command", "") or "> /dev/nvme" in p.get("command", ""),
            "禁止覆写磁盘设备",
            rule_id="policy-deny-device-overwrite",
        ))
        rules.append(PermissionRule(
            "deny", ["bash"],
            cmd_contains(":(){ :|:& };:"),
            "禁止 fork bomb",
            rule_id="policy-deny-forkbomb",
        ))

        # 文件工具 deny 规则
        for tool in ("write_file", "edit_file"):
            rules.append(PermissionRule(
                "deny", [tool],
                path_contains_any(["/etc/", "/proc/", "/sys/", "/boot/"]),
                "禁止写入系统目录",
                rule_id=f"policy-deny-system-dir-{tool}",
            ))
            rules.append(PermissionRule(
                "deny", [tool],
                path_contains_any(["~/.ssh/", ".ssh/"]),
                "禁止篡改 SSH 密钥",
                rule_id=f"policy-deny-ssh-{tool}",
            ))

        # ---- ask 规则（默认询问）----
        # 注意: 规则按添加顺序检查，首次命中即停。
        # 更具体的规则（docker、git push 等）应排在通用规则（rm）之前。

        for tool in ("write_file", "edit_file"):
            rules.append(PermissionRule(
                "ask", [tool],
                path_outside_workdir(self._workdir),
                "文件操作超出工作区范围",
                rule_id=f"policy-ask-outside-workdir-{tool}",
            ))

        # -- 高特异性规则（优先匹配）--

        rules.append(PermissionRule(
            "ask", ["bash"],
            cmd_contains_pipe_exec(),
            "下载并管道执行脚本 —— 严重安全风险",
            rule_id="policy-ask-pipe-exec",
        ))
        rules.append(PermissionRule(
            "ask", ["bash"],
            cmd_contains("git push --force"),
            "强制推送 —— 可能覆盖远程历史",
            rule_id="policy-ask-force-push",
        ))
        rules.append(PermissionRule(
            "ask", ["bash"],
            cmd_contains_any(["docker rm", "docker rmi"]),
            "删除 Docker 容器/镜像",
            rule_id="policy-ask-docker-rm",
        ))
        rules.append(PermissionRule(
            "ask", ["bash"],
            lambda t, p: ("npm install -g" in p.get("command", "").lower() or
                          ("pip install" in p.get("command", "").lower() and
                           "--user" not in p.get("command", "").lower())),
            "全局安装软件包",
            rule_id="policy-ask-global-install",
        ))

        # -- 通用规则（排在高特异性规则之后）--

        rules.append(PermissionRule(
            "ask", ["bash"],
            lambda t, p: "rm " in p.get("command", "") and "docker " not in p.get("command", "").lower(),
            "执行文件删除命令",
            rule_id="policy-ask-rm",
        ))
        rules.append(PermissionRule(
            "ask", ["bash"],
            lambda t, p: "> /etc/" in p.get("command", "") or ">> /etc/" in p.get("command", ""),
            "重定向写入系统配置目录",
            rule_id="policy-ask-redirect-etc",
        ))
        rules.append(PermissionRule(
            "ask", ["bash"],
            cmd_contains("chmod 777"),
            "修改为全员可写权限",
            rule_id="policy-ask-chmod777",
        ))
        rules.append(PermissionRule(
            "ask", ["bash"],
            cmd_contains("chown"),
            "修改文件所有者",
            rule_id="policy-ask-chown",
        ))

        return rules

        return rules


def path_contains_any(segments: list[str]) -> Callable[[str, dict], bool]:
    """文件路径中包含任意一个指定片段。"""
    def condition(_tool_name: str, params: dict) -> bool:
        path = params.get("path", "").lower()
        return any(s.lower() in path for s in segments)
    return condition


# ---------------------------------------------------------------------------
# JSON 文件规则来源（Project / Local 共用）
# ---------------------------------------------------------------------------

class JSONFileRuleSource(RuleSource):
    """从 JSON 文件读取规则的来源。

    JSON 格式:
    {
        "deny": [
            {"tools": ["bash"], "pattern": "aws *"},
            {"tools": ["write_file"], "pattern": "*.env"}
        ],
        "ask": [...],
        "allow": [...]
    }

    pattern 使用 shell 风格通配符。
    对于 bash 工具，pattern 匹配 params["command"]；
    对于文件工具，pattern 匹配 params["path"]。
    """

    def __init__(self, file_path: str | Path, required: bool = False):
        self._file_path = Path(file_path)
        self._required = required

    def get_rules(self) -> list[PermissionRule]:
        if not self._file_path.exists():
            if self._required:
                raise FileNotFoundError(f"权限配置文件不存在: {self._file_path}")
            return []
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            if self._required:
                raise
            print(f"  ⚠ 权限配置文件解析失败: {self._file_path} — {exc}")
            return []

        return self._parse_rules(data)

    def _parse_rules(self, data: dict) -> list[PermissionRule]:
        rules: list[PermissionRule] = []
        for behavior in ("deny", "ask", "allow"):
            entries = data.get(behavior, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                tools = entry.get("tools", ["*"])
                pattern = entry.get("pattern", "*")
                rule = PermissionRule(
                    behavior=behavior,
                    tool_names=tools,
                    condition=self._pattern_to_condition(tools, pattern),
                    message=f"{self._file_path.name}: {pattern}",
                )
                rules.append(rule)
        return rules

    @staticmethod
    def _pattern_to_condition(tools: list[str], pattern: str) -> Callable[[str, dict], bool]:
        """将 JSON 中的 pattern 字符串转为条件函数。"""
        def condition(tool_name: str, params: dict) -> bool:
            if "bash" in tools or "*" in tools:
                command = params.get("command", "")
                if command and fnmatch.fnmatch(command, pattern):
                    return True
            write_tools = {"write_file", "edit_file"}
            if any(t in tools for t in write_tools) or "*" in tools:
                path = params.get("path", "")
                if path and fnmatch.fnmatch(path, pattern):
                    return True
            return False
        return condition


# ---------------------------------------------------------------------------
# ProjectSettingsSource / LocalSettingsSource
# ---------------------------------------------------------------------------

class ProjectSettingsSource(JSONFileRuleSource):
    """项目级权限配置 —— .claude/permissions.json（提交到 git）。"""

    def __init__(self, project_root: str | Path):
        super().__init__(Path(project_root) / ".claude" / "permissions.json", required=False)


class LocalSettingsSource(JSONFileRuleSource):
    """本地权限覆盖 —— .claude/permissions.local.json（gitignore，不提交）。"""

    def __init__(self, project_root: str | Path):
        super().__init__(Path(project_root) / ".claude" / "permissions.local.json", required=False)


# ============================================================
# 🔮 预留扩展: UserSettingsSource
# 用途: 从 ~/.agent/permissions.json 读取用户全局权限偏好
# 参考: Claude Code 的 userSettings (~/.claude/settings.json)
# 当前: 本项目为本地项目，ProjectSettings + LocalSettings 已覆盖需求；
#       将来若作为 pip 包分发，启用此来源读取用户级配置
# 实现时: 继承 JSONFileRuleSource，file_path = Path.home() / ".agent" / "permissions.json"
#         在 PermissionEngine.__init__ 的 sources 列表中按需插入
# ============================================================


# ============================================================
# 🔮 预留扩展: FlagSettingsSource
# 用途: 从环境变量解析权限规则（AGENT_ALLOW_TOOLS, AGENT_DENY_TOOLS, AGENT_ASK_TOOLS）
# 参考: Claude Code 的 flagSettings（--settings CLI 参数）
# 当前: 使用频率低，环境变量注入规则不如直接修改 permissions.json 直观
# 实现时: 实现 RuleSource，在 get_rules() 中解析 os.environ 的逗号分隔键值对
#        "tool_name:pattern" 格式 → PermissionRule
# ============================================================


# ---------------------------------------------------------------------------
# SessionSource — 运行时动态规则
# ---------------------------------------------------------------------------

class SessionSource(RuleSource):
    """运行时动态增删的规则来源。

    用法:
        engine.session.add(PermissionRule("allow", ["write_file"], ...))
        engine.session.remove(rule_id)
    """

    def __init__(self):
        self._rules: dict[str, PermissionRule] = {}
        self._counter = 0

    def get_rules(self) -> list[PermissionRule]:
        return list(self._rules.values())

    def add(self, rule: PermissionRule) -> str:
        """添加一条规则，返回 rule_id。"""
        if rule.rule_id is None:
            self._counter += 1
            rule.rule_id = f"session-{self._counter}"
        self._rules[rule.rule_id] = rule
        return rule.rule_id

    def remove(self, rule_id: str) -> bool:
        """按 rule_id 移除规则，返回是否成功。"""
        if rule_id in self._rules:
            del self._rules[rule_id]
            return True
        return False

    def clear(self) -> None:
        """清空所有运行时规则。"""
        self._rules.clear()


# ---------------------------------------------------------------------------
# PermissionEngine — 三池合并 + 评估
# ---------------------------------------------------------------------------

class PermissionEngine:
    """权限引擎：从多个来源收集规则 → 构建 deny/ask/allow 三池 → 依次评估。

    用法:
        engine = PermissionEngine(
            sources=[PolicySettingsSource(workdir=WORKDIR),
                     ProjectSettingsSource(PROJECT_ROOT),
                     LocalSettingsSource(PROJECT_ROOT)],
            extra_rules=[],
            tool_registry=registry,
        )
        behavior, reason = engine.evaluate("bash", {"command": "rm file.txt"})
    """

    def __init__(
        self,
        sources: list[RuleSource] | None = None,
        extra_rules: list[PermissionRule] | None = None,
        default_behavior: str = "allow",
    ):
        self.sources = sources or []
        self.extra_rules = extra_rules or []
        self.default_behavior = default_behavior
        self.session = SessionSource()

    def evaluate(self, tool_name: str, params: dict) -> tuple[str, str | None]:
        """评估工具调用是否需要审批。

        解析顺序（与 CC 一致）:
          1. deny 规则 — 从所有来源收集，绝对优先，不可覆盖
          2. ask/allow 规则 — 按来源优先级逐源检查：
             Session > LocalSettings > ProjectSettings > PolicySettings > ExtraRules
             每个源内 ask 优先于 allow
          3. 无命中 → 默认行为

        Returns:
            (behavior, reason): behavior 为 "deny" | "ask" | "allow"，
                                reason 为命中规则的消息（allow 时为 None）
        """
        # 构建完整来源链（高优先级在前）
        ordered_sources = self._ordered_sources()

        # Gate 1: deny 规则 — 所有来源的 deny 规则，任意命中即拒绝
        for source in ordered_sources:
            for rule in source.get_rules():
                if rule.behavior == "deny" and self._matches(rule, tool_name, params):
                    return ("deny", rule.message)
        for rule in self.extra_rules:
            if rule.behavior == "deny" and self._matches(rule, tool_name, params):
                return ("deny", rule.message)

        # Gate 2-3: ask/allow 按来源优先级，源内 ask > allow
        for source in ordered_sources:
            # 先检查该源的 ask 规则
            for rule in source.get_rules():
                if rule.behavior == "ask" and self._matches(rule, tool_name, params):
                    return ("ask", rule.message)
            # 再检查该源的 allow 规则
            for rule in source.get_rules():
                if rule.behavior == "allow" and self._matches(rule, tool_name, params):
                    return ("allow", None)

        # ExtraRules (最低优先级): ask → allow
        for rule in self.extra_rules:
            if rule.behavior == "ask" and self._matches(rule, tool_name, params):
                return ("ask", rule.message)
        for rule in self.extra_rules:
            if rule.behavior == "allow" and self._matches(rule, tool_name, params):
                return ("allow", None)

        # 无命中 → 默认行为
        return (self.default_behavior, None)

    def _ordered_sources(self) -> list[RuleSource]:
        """返回按优先级排序的来源列表（高优先级在前）。

        优先级: Session > LocalSettings > ProjectSettings > PolicySettings
        来源列表 [PolicySettings, ProjectSettings, LocalSettings] 中，
        索引越大的优先级越高。Session 始终最高。
        """
        # sources 列表按优先级升序排列（PolicySettings 优先级最低，排在前面）
        # 反转后即为降序（高优先级在前），Session 插入最前
        ordered = list(reversed(self.sources))
        ordered.insert(0, self.session)
        return ordered

    @staticmethod
    def _matches(rule: PermissionRule, tool_name: str, params: dict) -> bool:
        """检查单条规则是否匹配工具调用。"""
        name_match = rule.tool_names == ["*"] or tool_name in rule.tool_names
        if not name_match:
            return False
        try:
            return rule.condition(tool_name, params)
        except Exception:
            return False
