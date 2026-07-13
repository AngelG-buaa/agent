"""PermissionEngine 完整测试 —— 覆盖 US-1 到 US-5 + engine 内部校验。"""

import pytest
from unittest.mock import MagicMock, patch

from tooling.permission.engine import PermissionEngine, PermissionGrant, EvalResult
from tooling.permission.policy import PermissionRule, RuleBehavior
from tooling.permission.exceptions import (
    NonPersistablePermission,
    InvalidPermissionGrant,
    InvalidPermissionRule,
)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _make_ask_rule(tool_name="bash", rule_content="rm *", rule_id="policy-ask-rm"):
    """创建一条可用于持久化的 ASK 策略规则。"""
    return PermissionRule(
        tool_name=tool_name,
        rule_behavior=RuleBehavior.ASK,
        rule_content=rule_content,
        message=f"确认: {tool_name} {rule_content}",
        condition=lambda t, p: rule_content.rstrip("*").strip() in str(p),
        rule_id=rule_id,
    )


# ═══════════════════════════════════════════════════════════════
# US-1: Executor Isolation
# ═══════════════════════════════════════════════════════════════


class TestExecutorIsolation:
    """两个 executor 使用不同 PermissionEngine，互不干扰 (US-1)。"""

    def test_two_executors_independent_engines(self):
        """executor A (deny engine) 不影响 executor B (allow engine) 执行工具。"""
        from tooling.executor import ToolExecutor

        deny_engine = PermissionEngine(default_behavior="deny")
        allow_engine = PermissionEngine(default_behavior="allow")

        def allow_approver(tool_name, params, reason):
            return {"decision": "allow"}

        executor_a = ToolExecutor(permission_engine=deny_engine, approver=allow_approver)
        executor_b = ToolExecutor(permission_engine=allow_engine, approver=allow_approver)

        from tools.calculator import CalculatorTool
        executor_a.register(CalculatorTool())
        executor_b.register(CalculatorTool())

        # A: deny engine → 拒绝
        result_a = executor_a.execute("calculator", {"expression": "1+1"})
        assert "error" in result_a

        # B: allow engine → 成功（不受 A 影响）
        result_b = executor_b.execute("calculator", {"expression": "1+1"})
        assert "error" not in result_b

    def test_session_rules_isolated_between_engines(self):
        """engine A 的 session grant 不影响 engine B。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")

        engine_a = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")
        engine_b = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        # engine_a 安装 session grant
        eval_a = engine_a.evaluate("bash", {"command": "rm file.txt"})
        engine_a.allow_for_session(eval_a)
        assert len(engine_a._session_rules) == 1

        # engine_b 完全不受影响
        assert len(engine_b._session_rules) == 0


# ═══════════════════════════════════════════════════════════════
# US-2: Permission Not in Global Hook
# ═══════════════════════════════════════════════════════════════


class TestPermissionNotInGlobalHook:
    """权限检查不再通过全局 PreToolUse Hook (US-2)。"""

    def test_no_permission_callback_registered_in_main_py(self):
        """main.py 不调用 register_hook('PreToolUse', ...) 注册权限 callback。"""
        import ast
        import pathlib

        main_path = pathlib.Path(__file__).parent.parent / "main.py"
        source = main_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                is_register_hook = False
                if hasattr(func, 'id') and func.id == 'register_hook':
                    is_register_hook = True
                elif hasattr(func, 'attr') and func.attr == 'register_hook':
                    is_register_hook = True

                if is_register_hook:
                    args = [ast.unparse(a) for a in node.args]
                    if any("PreToolUse" in a for a in args):
                        raise AssertionError(
                            f"main.py 不应调用 register_hook('PreToolUse', ...): "
                            f"line {node.lineno}"
                        )

    def test_executor_uses_internal_authorization(self):
        """ToolExecutor 使用内部 _authorize_tool_call 而非全局 Hook 做权限检查。"""
        from tooling.executor import ToolExecutor

        engine = PermissionEngine(default_behavior="deny")
        approver = MagicMock()

        executor = ToolExecutor(permission_engine=engine, approver=approver)

        mock_tool = MagicMock()
        mock_tool.run.return_value = {"result": "ok"}
        executor.register(mock_tool)

        with patch('tooling.executor.trigger_hooks') as mock_trigger:
            result = executor.execute("mock_tool", {})
            assert "error" in result
            # DENY 在 approver 之前，approver 不应被调用
            approver.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# US-3: Grant/Listener
# ═══════════════════════════════════════════════════════════════


class TestGrantListener:
    """grant_listener 仅在 allow_for_session() 新授权时触发 (US-3)。"""

    def test_listener_called_on_new_grant(self):
        """allow_for_session() 成功 → listener 调用一次且参数正确。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="allow")

        grants_received = []
        engine.set_grant_listener(lambda g: grants_received.append(g))

        eval_result = engine.evaluate("bash", {"command": "rm file.txt"})
        assert eval_result.behavior == RuleBehavior.ASK
        assert eval_result.matched_rule is not None

        grant = engine.allow_for_session(eval_result)
        assert len(grants_received) == 1
        assert grants_received[0].tool_name == "bash"
        assert grants_received[0].rule_content == "rm *"
        assert isinstance(grant, PermissionGrant)
        assert grant.tool_name == "bash"
        assert grant.rule_content == "rm *"

    def test_listener_not_called_on_replace(self):
        """replace_session_rules() 不触发 listener。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="allow")

        grants_received = []
        engine.set_grant_listener(lambda g: grants_received.append(g))

        engine.replace_session_rules([PermissionGrant("bash", "rm *")])
        assert len(grants_received) == 0

    def test_listener_not_called_on_clear(self):
        """replace_session_rules([]) 不触发 listener。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="allow")

        eval_result = engine.evaluate("bash", {"command": "rm file.txt"})
        engine.allow_for_session(eval_result)

        grants_received = []
        engine.set_grant_listener(lambda g: grants_received.append(g))
        engine.replace_session_rules([])
        assert len(grants_received) == 0

    def test_fallback_ask_raises_non_persistable(self):
        """fallback ASK (matched_rule=None) → NonPersistablePermission。"""
        engine = PermissionEngine(default_behavior="ask")  # 无策略规则

        eval_result = engine.evaluate("bash", {"command": "ls"})
        assert eval_result.behavior == RuleBehavior.ASK
        assert eval_result.matched_rule is None

        with pytest.raises(NonPersistablePermission):
            engine.allow_for_session(eval_result)

    def test_allow_for_session_non_ask_raises_value_error(self):
        """传入非 ASK 的 EvalResult → ValueError。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="allow")

        # 直接放行 (ALLOW)
        eval_result = engine.evaluate("bash", {"command": "echo hello"})
        # echo hello 匹配不到 rm * 的 ASK 规则，走 fallback allow
        if eval_result.behavior == RuleBehavior.ALLOW:
            with pytest.raises(ValueError):
                engine.allow_for_session(eval_result)

    def test_listener_persist_before_install(self):
        """listener 抛异常 → 内存规则不安装。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="allow")

        def failing_listener(grant):
            raise RuntimeError("DB write failed")

        engine.set_grant_listener(failing_listener)

        eval_result = engine.evaluate("bash", {"command": "rm file.txt"})

        with pytest.raises(RuntimeError, match="DB write failed"):
            engine.allow_for_session(eval_result)

        # 内存规则未被安装
        assert len(engine._session_rules) == 0

    def test_allow_for_session_without_listener(self):
        """未设置 listener → 正常安装规则（无持久化）。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="allow")
        # 不设置 listener

        eval_result = engine.evaluate("bash", {"command": "rm file.txt"})
        grant = engine.allow_for_session(eval_result)

        assert isinstance(grant, PermissionGrant)
        assert len(engine._session_rules) == 1


# ═══════════════════════════════════════════════════════════════
# US-4: Replace Session Rules
# ═══════════════════════════════════════════════════════════════


class TestReplaceSessionRules:
    """replace_session_rules 全量替换 + 原子性 (US-4)。"""

    def test_replace_replaces_all_rules(self):
        """全量替换：旧 rule 完全被新 grants 替代。"""
        ask_a = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        ask_b = _make_ask_rule("write_file", "*", "policy-ask-write")
        engine = PermissionEngine(policy_rules=[ask_a, ask_b], default_behavior="deny")

        # 安装 grant_a
        eval_a = engine.evaluate("bash", {"command": "rm file.txt"})
        engine.allow_for_session(eval_a)
        assert ("bash", "rm *") in engine._session_rules

        # 替换为 grant_b
        engine.replace_session_rules([PermissionGrant("write_file", "*")])
        assert len(engine._session_rules) == 1
        assert ("write_file", "*") in engine._session_rules
        assert ("bash", "rm *") not in engine._session_rules

    def test_replace_empty_clears_all(self):
        """replace_session_rules([]) 清空所有 session rules。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        engine.replace_session_rules([PermissionGrant("bash", "rm *")])
        assert len(engine._session_rules) == 1

        engine.replace_session_rules([])
        assert len(engine._session_rules) == 0

    def test_invalid_grant_atomic_rollback(self):
        """无效 grant 导致整体失败，原 _session_rules 不变。"""
        ask_a = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_a], default_behavior="deny")

        # 先安装一条有效 grant
        engine.replace_session_rules([PermissionGrant("bash", "rm *")])
        assert len(engine._session_rules) == 1

        # 尝试替换：一个有效 + 一个无效
        with pytest.raises(InvalidPermissionGrant):
            engine.replace_session_rules([
                PermissionGrant("bash", "rm *"),         # 有效
                PermissionGrant("bash", "nonexistent"),  # 无效
            ])

        # 原规则不变
        assert len(engine._session_rules) == 1
        assert ("bash", "rm *") in engine._session_rules

    def test_duplicate_grants_in_list_auto_dedup(self):
        """重复 grant (同 tool_name + rule_content) → 自动去重。"""
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(policy_rules=[ask_rule], default_behavior="deny")

        engine.replace_session_rules([
            PermissionGrant("bash", "rm *"),
            PermissionGrant("bash", "rm *"),  # 重复
        ])
        assert len(engine._session_rules) == 1


# ═══════════════════════════════════════════════════════════════
# US-5: Deny Priority
# ═══════════════════════════════════════════════════════════════


class TestDenyPriority:
    """DENY 规则始终优先于 SESSION ALLOW (US-5)。"""

    def test_deny_overrides_session_allow(self):
        """同一工具不同操作：session allow "rm *" 不覆盖 deny "sudo *"。"""
        deny_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.DENY,
            rule_content="sudo *", message="禁止提权",
            condition=lambda t, p: "sudo" in p.get("command", ""),
            rule_id="policy-deny-sudo",
        )
        ask_rule = _make_ask_rule("bash", "rm *", "policy-ask-rm")
        engine = PermissionEngine(
            policy_rules=[deny_rule, ask_rule],
            default_behavior="allow",
        )

        # 安装 session allow for "rm *"
        engine.replace_session_rules([PermissionGrant("bash", "rm *")])

        # "rm file.txt" → session allow 生效
        r1 = engine.evaluate("bash", {"command": "rm file.txt"})
        assert r1.behavior == RuleBehavior.ALLOW

        # "sudo rm -rf /" → deny 优先
        r2 = engine.evaluate("bash", {"command": "sudo rm -rf /"})
        assert r2.behavior == RuleBehavior.DENY

    def test_deny_unaffected_by_session_rules(self):
        """DENY 规则不受 session grants 任何影响。"""
        deny_rule = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.DENY,
            rule_content="rm -rf /*", message="禁止递归删除根目录",
            condition=lambda t, p: "rm -rf /" in p.get("command", ""),
            rule_id="policy-deny-rm-root",
        )
        engine = PermissionEngine(policy_rules=[deny_rule], default_behavior="allow")

        # 即使 _session_rules 为空，DENY 也生效
        r = engine.evaluate("bash", {"command": "rm -rf /"})
        assert r.behavior == RuleBehavior.DENY


# ═══════════════════════════════════════════════════════════════
# Engine Internal Validation
# ═══════════════════════════════════════════════════════════════


class TestPolicyRuleValidation:
    """Engine 初始化时的策略规则校验。"""

    def test_duplicate_natural_key_raises(self):
        """策略规则中 (tool_name, rule_content) 重复 → InvalidPermissionRule。"""
        r1 = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="same_content", message="规则1",
            condition=lambda t, p: True, rule_id="id1",
        )
        r2 = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ALLOW,
            rule_content="same_content", message="规则2",
            condition=lambda t, p: True, rule_id="id2",
        )

        with pytest.raises(InvalidPermissionRule):
            PermissionEngine(policy_rules=[r1, r2])

    def test_unique_natural_keys_pass(self):
        """不同 (tool_name, rule_content) → 正常初始化。"""
        r1 = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ASK,
            rule_content="rm *", message="确认删除",
            condition=lambda t, p: "rm " in p.get("command", ""),
            rule_id="id1",
        )
        r2 = PermissionRule(
            tool_name="bash", rule_behavior=RuleBehavior.ALLOW,
            rule_content="git status", message="允许 git status",
            condition=lambda t, p: "git status" in p.get("command", ""),
            rule_id="id2",
        )

        engine = PermissionEngine(policy_rules=[r1, r2])
        assert len(engine._policy_rules_by_key) == 2

    def test_real_build_rules_no_duplicates(self):
        """内置策略规则无自然键重复。"""
        from tooling.permission.policy import build_rules
        from pathlib import Path

        rules = build_rules(Path.cwd())
        engine = PermissionEngine(policy_rules=rules, default_behavior="ask")
        assert len(engine._policy_rules_by_key) == len(rules)

    def test_permission_grant_is_frozen(self):
        """PermissionGrant 是不可变数据类。"""
        grant = PermissionGrant("bash", "rm *")
        with pytest.raises(Exception):
            grant.tool_name = "write_file"  # type: ignore

    def test_create_engine_works(self):
        """create_engine 工厂函数正常工作。"""
        from tooling.permission import create_engine

        engine = create_engine(default_behavior="ask")
        assert engine.default_behavior == "ask"
        # 内置策略规则加载成功
        assert len(engine._policy_rules_by_key) > 0

    def test_eval_result_matched_rule_none_on_fallback(self):
        """fallback 时 matched_rule 为 None。"""
        engine = PermissionEngine(default_behavior="deny")
        result = engine.evaluate("bash", {"command": "ls"})
        assert result.behavior == RuleBehavior.DENY
        assert result.matched_rule is None
