"""权限评估引擎 —— 3 步管线。

engine 只负责评估流程：
  1. 合并会话规则 + 策略规则
  2. 显式覆盖：DENY 保留，SESSION ALLOW 覆盖同 key 的 POLICY ASK
  3. 按 Gate 1 (deny) → Gate 2 (allow) → Gate 3 (ask) → fallback 顺序评估

engine 不包含任何具体的安全策略规则或条件匹配逻辑。
"""

from dataclasses import dataclass
from typing import Callable

from .policy import PermissionRule, RuleBehavior
from .exceptions import NonPersistablePermission, InvalidPermissionGrant


@dataclass(frozen=True)
class PermissionGrant:
    """一条可持久化的用户授权。

    (tool_name, rule_content) 是稳定自然键，唯一对应一条策略 ASK 规则。
    不含 condition 闭包、不含 rule_id——这是 PermissionEngine 与
    SessionController/Repository 之间的纯数据契约。
    """
    tool_name: str
    rule_content: str


@dataclass
class EvalResult:
    """权限评估结果。"""
    behavior: RuleBehavior
    reason: str | None = None
    matched_rule: PermissionRule | None = None  # None = fallback to default_behavior


class PermissionEngine:
    """权限引擎：显式覆盖 + 3 步管线。

    管线:
      Gate 1: deny 规则 → 拒绝
      Gate 2: allow 规则 → 放行 (含 session + policy)
      Gate 3: ask 规则 → 审批（已被 session grant 覆盖的排除）
      Fallback: default_behavior
    """

    def __init__(
        self,
        policy_rules: list[PermissionRule] | None = None,
        default_behavior: str = "allow",
    ):
        self._policy_rules = list(policy_rules or [])
        self.default_behavior = default_behavior

        # 索引 (tool_name, rule_content) → PermissionRule
        self._policy_rules_by_key: dict[tuple[str, str], PermissionRule] = {}
        # 索引 rule_id → PermissionRule (诊断用 + allow_for_session 归属验证)
        self._policy_rules_by_id: dict[str, PermissionRule] = {}

        for rule in self._policy_rules:
            key = (rule.tool_name, rule.rule_content)
            if key in self._policy_rules_by_key:
                raise ValueError(
                    f"策略规则自然键重复: tool_name={rule.tool_name!r}, "
                    f"rule_content={rule.rule_content!r}"
                )
            self._policy_rules_by_key[key] = rule
            if rule.rule_id is not None:
                self._policy_rules_by_id[rule.rule_id] = rule

        # 会话规则 —— key 为 (tool_name, rule_content)
        self._session_rules: dict[tuple[str, str], PermissionRule] = {}
        self._grant_listener: Callable[[PermissionGrant], None] | None = None

    # ---- Grant Listener ----

    def set_grant_listener(
        self,
        listener: Callable[[PermissionGrant], None] | None,
    ) -> None:
        """安装/移除 grant 持久化回调。

        仅在 allow_for_session() 成功创建新 grant 时调用。
        replace_session_rules() 永远不会触发此回调。
        """
        self._grant_listener = listener

    # ---- 会话规则管理 ----

    def allow_for_session(self, result: EvalResult) -> PermissionGrant:
        """用户交互产生新授权，创建持久化 grant 并安装运行时规则。

        Preconditions:
            result.behavior == RuleBehavior.ASK
            result.matched_rule is not None (非 fallback)
            result.matched_rule.rule_id is not None
            result.matched_rule 属于当前 engine

        Raises:
            ValueError: behavior 不是 ASK
            NonPersistablePermission: matched_rule 为 None (fallback ASK)
            ValueError: rule_id 缺失或规则不属于当前 engine

        Returns:
            PermissionGrant(tool_name, rule_content)
        """
        if result.behavior != RuleBehavior.ASK:
            raise ValueError(
                f"只有 ASK 决策可以转换为 session grant，当前为 {result.behavior}"
            )

        source = result.matched_rule
        if source is None:
            raise NonPersistablePermission(
                "fallback ASK 没有稳定的策略范围，不能创建 session grant"
            )

        if not source.rule_id:
            raise ValueError(
                "可持久化的 ASK 规则必须具有稳定 rule_id"
            )

        # 防止传入其他 engine 或已经失效的规则
        registered = self._policy_rules_by_id.get(source.rule_id)
        if registered is not source:
            raise ValueError(
                f"规则不属于当前 PermissionEngine: {source.rule_id}"
            )

        grant = PermissionGrant(
            tool_name=source.tool_name,
            rule_content=source.rule_content,
        )

        session_rule = self._build_session_rule(grant)

        # 先持久化，后安装内存规则
        if self._grant_listener is not None:
            self._grant_listener(grant)

        key = (grant.tool_name, grant.rule_content)
        self._session_rules[key] = session_rule

        return grant

    def replace_session_rules(
        self,
        grants: list[PermissionGrant],
    ) -> None:
        """替换当前所有会话规则。

        先构建候选集合，全部成功后再原子替换。任一 grant 无效则
        整体失败，保留原 _session_rules 不变。不触发 grant_listener。

        Raises:
            InvalidPermissionGrant: 任一 grant 找不到对应策略规则
        """
        candidate: dict[tuple[str, str], PermissionRule] = {}

        for grant in grants:
            key = (grant.tool_name, grant.rule_content)
            if key not in candidate:  # 自动去重：后者覆盖前者
                candidate[key] = self._build_session_rule(grant)

        self._session_rules = candidate

    def _build_session_rule(self, grant: PermissionGrant) -> PermissionRule:
        """根据 grant 从策略索引中找到原 ASK 规则，复用其 condition。

        Raises:
            InvalidPermissionGrant: 找不到对应策略规则
        """
        key = (grant.tool_name, grant.rule_content)
        policy_rule = self._policy_rules_by_key.get(key)
        if policy_rule is None:
            raise InvalidPermissionGrant(
                f"找不到对应的策略规则: tool_name={grant.tool_name!r}, "
                f"rule_content={grant.rule_content!r}"
            )
        return PermissionRule(
            tool_name=grant.tool_name,
            rule_behavior=RuleBehavior.ALLOW,
            rule_content=grant.rule_content,
            message=policy_rule.message,
            condition=policy_rule.condition,
            rule_id=None,  # session rules 不需要 rule_id
        )

    # ---- 权限评估 ----

    def evaluate(self, tool_name: str, params: dict) -> EvalResult:
        """评估工具调用权限。"""
        deny_pool, allow_pool, ask_pool = self._collect_all_rules()

        # Gate 1: deny
        for r in deny_pool:
            if r.matches(tool_name, params):
                return EvalResult(RuleBehavior.DENY, r.message, matched_rule=r)

        # Gate 2: allow (session + policy)
        for r in allow_pool:
            if r.matches(tool_name, params):
                return EvalResult(RuleBehavior.ALLOW, matched_rule=r)

        # Gate 3: ask → 审批
        for r in ask_pool:
            if r.matches(tool_name, params):
                return EvalResult(RuleBehavior.ASK, r.message, matched_rule=r)

        # Fallback
        return EvalResult(RuleBehavior(self.default_behavior))

    def _collect_all_rules(self) -> tuple[list[PermissionRule], list[PermissionRule], list[PermissionRule]]:
        """合并策略规则 + 会话规则，显式覆盖。

        DENY: 始终保留（安全优先）
        ALLOW: session rules + policy ALLOW
        ASK: 仅保留未被 session grant 覆盖的
        """
        granted_keys = set(self._session_rules.keys())

        deny = [
            rule for rule in self._policy_rules
            if rule.rule_behavior == RuleBehavior.DENY
        ]

        allow = [
            *self._session_rules.values(),
            *[
                rule for rule in self._policy_rules
                if rule.rule_behavior == RuleBehavior.ALLOW
            ],
        ]

        ask = [
            rule for rule in self._policy_rules
            if (
                rule.rule_behavior == RuleBehavior.ASK
                and (rule.tool_name, rule.rule_content) not in granted_keys
            )
        ]

        return deny, allow, ask
