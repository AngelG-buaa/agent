"""权限模块异常类型。

Engine 抛出这些异常供 ToolExecutor / SessionController 按类型处理。
不继承内置 PermissionError，避免与操作系统权限异常混淆。
"""


class PermissionArchitectureError(Exception):
    """权限模块基础异常。"""


class NonPersistablePermission(PermissionArchitectureError):
    """当前授权决策无法持久化。

    抛出场景: fallback ASK（matched_rule=None）被用户选择"始终允许"。
    ToolExecutor 捕获后降级为单次 allow。
    """


class InvalidPermissionGrant(PermissionArchitectureError):
    """持久化 grant 无法映射到现有策略规则。

    抛出场景: replace_session_rules() 收到一个 (tool_name, rule_content)
    在当前 _policy_rules_by_key 中找不到对应策略规则的 grant。
    """


class InvalidPermissionRule(PermissionArchitectureError):
    """策略规则配置无效。

    抛出场景:
    - 策略规则集合中 (tool_name, rule_content) 存在重复
    - allow_for_session() 传入不属于当前 engine 的 matched_rule
    - matched_rule.rule_id 为 None 但需要持久化
    """
