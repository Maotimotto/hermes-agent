"""Approval 请求 / 决策类型定义。

提供 ApprovalGate 统一审批层的核心数据结构：
- RiskLevel：风险等级枚举
- ApprovalRequest：审批请求
- ApprovalDecision：审批决策
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


class RiskLevel(str, Enum):
    """风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ApprovalRequest:
    """审批请求。

    Attributes:
        id: 唯一请求 ID（自动生成 apr_xxxx）。
        action_kind: 动作类别（shell / file_write / network / secret / tool）。
        action_payload: 动作载荷（如命令字符串、文件路径等）。
        risk: 风险等级。
        fingerprint: 幂等指纹，用于记忆决策匹配。
        session_id: 关联的 session ID。
        turn_id: 关联的 turn ID。
        created_at: 创建时间（ISO 8601）。
    """

    action_kind: str
    action_payload: dict[str, Any] | None = None
    risk: RiskLevel = RiskLevel.MEDIUM
    fingerprint: str = ""
    id: str = field(default_factory=lambda: f"apr_{uuid.uuid4().hex[:22]}")
    session_id: str = ""
    turn_id: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


@dataclass
class ApprovalDecision:
    """审批决策。

    Attributes:
        id: 决策 ID。
        request_id: 关联的审批请求 ID。
        decision: 决策结果（allow / deny / remember）。
        decided_by: 决策者（user / resolver / remembered / auto）。
        decided_at: 决策时间（ISO 8601）。
        ttl_until: 记忆过期时间（ISO 8601，仅 remember 时有值）。
    """

    request_id: str
    decision: Literal["allow", "deny", "remember"]
    id: str = field(default_factory=lambda: f"dec_{uuid.uuid4().hex[:22]}")
    decided_by: str = "resolver"
    decided_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    ttl_until: str | None = None
