"""User approval contracts independent of any interface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from coding_agent.tools.contracts import ToolEffect


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    run_id: str
    call_id: str
    tool_name: str
    effect: ToolEffect
    summary: str


class ApprovalProvider(Protocol):
    def request(self, request: ApprovalRequest) -> ApprovalStatus: ...


class UnavailableApprovalProvider:
    """Safe default used by non-interactive graph callers."""

    def request(self, request: ApprovalRequest) -> ApprovalStatus:
        return ApprovalStatus.UNAVAILABLE
