# Quickstart: Permission 系统实例级重构

**Date**: 2026-07-13

## Prerequisites

- Python 3.12+
- pytest
- 项目根目录: `Stage 2/project/`

## Validation Scenarios

### VS-1: 双 Executor 隔离验证 (US-1)

```bash
cd Stage 2/project
D:/Miniconda/envs/llm/python -m pytest tests/test_permission_engine.py::TestExecutorIsolation -v
```

**Expected**: 两个 executor 各自使用不同的 PermissionEngine，executor A 的 deny 不影响 executor B 执行工具。所有隔离测试通过。

### VS-2: Grant/Listener 接口验证 (US-3)

```bash
D:/Miniconda/envs/llm/python -m pytest tests/test_permission_engine.py::TestGrantListener -v
```

**Expected**: `allow_for_session()` 触发 listener 一次且参数正确；`replace_session_rules()` 不触发 listener；fallback ASK 抛出 `NonPersistablePermission`。

### VS-3: Session Rules 全量替换验证 (US-4)

```bash
D:/Miniconda/envs/llm/python -m pytest tests/test_permission_engine.py::TestReplaceSessionRules -v
```

**Expected**: `replace_session_rules()` 原子替换；无效 grant 抛 `InvalidPermissionGrant` 且原规则不变；空列表清空所有规则。

### VS-4: Deny 优先级验证 (US-5)

```bash
D:/Miniconda/envs/llm/python -m pytest tests/test_permission_engine.py::TestDenyPriority -v
```

**Expected**: DENY 规则始终优先于 SESSION ALLOW；即使 session 中已有授权，DENY 仍然拒绝。

### VS-5: 全局 Hook 无权限 callback (US-2)

```bash
# 搜索确认生产代码不再注册权限 callback 到全局 Hook
cd Stage 2/project
grep -rn "register_hook.*PreToolUse" main.py tooling/ --include="*.py" | grep -v test
```

**Expected**: 无匹配结果（`main.py` 不再调用 `register_hook("PreToolUse", ...)` 注册权限 callback）。

### VS-6: 全量测试回归

```bash
cd Stage 2/project
D:/Miniconda/envs/llm/python -m pytest tests/ -q
```

**Expected**: 所有现有测试 + 新权限测试全部通过。

### VS-7: 策略规则自然键重复检测

```bash
D:/Miniconda/envs/llm/python -m pytest tests/test_permission_engine.py::TestPolicyRuleValidation -v
```

**Expected**: 如果策略规则中 `(tool_name, rule_content)` 重复，Engine 初始化抛出 `InvalidPermissionRule`。

## Manual Smoke Test

```bash
cd Stage 2/project
D:/Miniconda/envs/llm/python -c "
from pathlib import Path
from tooling.permission import create_engine
from tooling.executor import ToolExecutor, terminal_approver

# 注意: 这会在终端提示审批（按 n 拒绝即可验证链路）
engine = create_engine(default_behavior='ask')
executor = ToolExecutor(permission_engine=engine, approver=terminal_approver)

# 验证工具注册和 schemas 导出正常
from tools.calculator import CalculatorTool
executor.register(CalculatorTool())
schemas = executor.get_schemas()
assert len(schemas) >= 1, f'Expected >=1 schema, got {len(schemas)}'
print(f'OK: {len(schemas)} tool schemas exported')
"
```

**Expected**: 输出 `OK: N tool schemas exported`（N ≥ 1），无异常。
