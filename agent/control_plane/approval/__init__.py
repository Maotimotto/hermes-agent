"""ApprovalGate public exports."""

from .gate import ApprovalGate, GateResult, fingerprint
from .policies import (
    ApprovalPolicy,
    DEFAULT_POLICIES,
    evaluate_policies,
)
from .runtime_adapter import (
    infer_action_kind,
    needs_approval,
    request_tool_approval,
)
from .types import (
    ApprovalDecision,
    ApprovalRequest,
    RiskLevel,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalPolicy",
    "ApprovalRequest",
    "DEFAULT_POLICIES",
    "GateResult",
    "RiskLevel",
    "evaluate_policies",
    "fingerprint",
    "infer_action_kind",
    "needs_approval",
    "request_tool_approval",
]
