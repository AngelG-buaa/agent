"""权限评估引擎 —— 3 步管线。

engine 只负责评估流程：
  1. 合并会话规则 + 策略规则
  2. 按 RuleBehavior 分入 deny/allow/ask 三池
  3. 按 Gate 1 (deny) → Gate 2 (allow) → Gate 3 (ask) → fallback 顺序评估

engine 不包含任何具体的安全策略规则或条件匹配逻辑。
"""

from dataclasses import dataclass

from .policy import PermissionRule, RuleBehavior


@dataclass
class EvalResult:
    """权限评估结果。"""
    behavior: RuleBehavior
    reason: str | None = None
    rule: PermissionRule | None = None  # 命中的规则（session 预授权时需要）


class PermissionEngine:
    """权限引擎：UNION 合并 + 3 步管线。

    管线:
      Gate 1: deny 规则 → 拒绝
      Gate 2: allow 规则 → 放行 (含会话预授权)
      Gate 3: ask 规则 → 审批
      Fallback: default_behavior
    """

    def __init__(
        self,
        policy_rules: list[PermissionRule] | None = None,
        default_behavior: str = "allow",
    ):
        self._policy_rules = list(policy_rules or [])
        self.default_behavior = default_behavior
        self._session_rules: dict[str, PermissionRule] = {}
        self._session_counter = 0

    # ---- 会话规则 ----

    def allow_for_session(self, tool_name: str, rule_content: str, message: str = "") -> str:
        """添加会话级预授权规则。返回 rule_id 供 revoke_session_rule() 撤销。

        rule_content 必须显式指定（无默认值），防止无意中全放行。
        会话规则进入 Gate 2 (allow 池), 优先级高于内置策略。
        """
        self._session_counter += 1
        rid = f"session-{self._session_counter}"

        # 会话规则的匹配: rule_content 作为子串在任意参数值中查找
        search = rule_content.rstrip("*").strip()

        def _cond(_t: str, p: dict) -> bool:
            return any(search in str(v) for v in p.values())

        self._session_rules[rid] = PermissionRule(
            tool_name=tool_name,
            rule_behavior=RuleBehavior.ALLOW,
            rule_content=rule_content,
            message=message or f"会话预授权: {tool_name} {rule_content}",
            condition=_cond,
            rule_id=rid,
        )
        return rid

    def revoke_session_rule(self, rule_id: str) -> bool:
        """撤销会话级规则。返回 True 表示删除成功。"""
        return self._session_rules.pop(rule_id, None) is not None

    def clear_session_rules(self) -> None:
        """清空所有会话规则（如新对话开始时）。"""
        self._session_rules.clear()

    # ---- 权限评估 ----

    def evaluate(self, tool_name: str, params: dict) -> EvalResult:
        """评估工具调用权限。"""
        deny_pool, allow_pool, ask_pool = self._collect_all_rules()

        # Gate 1: deny
        for r in deny_pool:
            if r.matches(tool_name, params):
                return EvalResult(RuleBehavior.DENY, r.message, r)

        # Gate 2: allow (会话预授权)
        for r in allow_pool:
            if r.matches(tool_name, params):
                return EvalResult(RuleBehavior.ALLOW, rule=r)

        # Gate 3: ask → 审批
        for r in ask_pool:
            if r.matches(tool_name, params):
                return EvalResult(RuleBehavior.ASK, r.message, r)

        # Fallback
        return EvalResult(RuleBehavior(self.default_behavior))

    def _collect_all_rules(self) -> tuple[list[PermissionRule], list[PermissionRule], list[PermissionRule]]:
        """合并会话规则 + 缓存策略规则，去重后按类型分入三池。"""
        seen: set[tuple[str, RuleBehavior, str]] = set()
        deny: list[PermissionRule] = []
        allow: list[PermissionRule] = []
        ask: list[PermissionRule] = []

        # 会话规则优先加入，重复时优先于内置策略（先入的胜出）。
        all_rules: list[PermissionRule] = []
        all_rules.extend(self._session_rules.values())   # 会话规则（allow_for_session() 动态添加）
        all_rules.extend(self._policy_rules)             # 策略规则（构造时缓存，不变）

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
