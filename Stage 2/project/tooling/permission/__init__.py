"""权限审批模块 —— 实例级权限评估 + 内置安全策略。

公共 API:
  - PermissionEngine       — 权限评估引擎
  - PermissionGrant        — 可持久化的用户授权数据契约
  - create_engine          — 工厂函数（自动组装内置策略）
  - PermissionRule         — 规则数据模型
  - RuleBehavior           — 规则行为枚举
  - EvalResult             — 权限评估结果
  - PermissionArchitectureError — 权限异常基类
  - NonPersistablePermission    — fallback ASK 不可持久化
  - InvalidPermissionGrant      — grant 无效
"""

from __future__ import annotations
from pathlib import Path

from .engine import EvalResult, PermissionEngine, PermissionGrant
from .exceptions import (
    PermissionArchitectureError,
    NonPersistablePermission,
    InvalidPermissionGrant,
)
from .policy import PermissionRule, RuleBehavior, build_rules


def create_engine(
    project_root: str | Path | None = None,
    default_behavior: str = "allow",
) -> PermissionEngine:
    """创建 PermissionEngine，自动加载内置安全策略。

    Args:
        project_root: 项目根目录（安全边界 + 策略配置目录），None 则使用 cwd
        default_behavior: 所有规则未命中时的默认行为
    """
    root = Path(project_root) if project_root else Path.cwd()
    return PermissionEngine(
        policy_rules=build_rules(root),
        default_behavior=default_behavior,
    )
