"""Policies and approvals for operations with side effects."""

from coding_agent.security.approvals import (
    ApprovalProvider,
    ApprovalRequest,
    ApprovalStatus,
    UnavailableApprovalProvider,
)
from coding_agent.security.policy import (
    DefaultOperationPolicy,
    OperationPolicy,
    PolicyDecision,
    PolicyResult,
    is_safe_validation_command,
)

__all__ = [
    "ApprovalProvider",
    "ApprovalRequest",
    "ApprovalStatus",
    "DefaultOperationPolicy",
    "OperationPolicy",
    "PolicyDecision",
    "PolicyResult",
    "is_safe_validation_command",
    "UnavailableApprovalProvider",
]
