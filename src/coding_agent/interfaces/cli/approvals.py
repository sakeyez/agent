"""Interactive approval adapter for side-effecting tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import TextIO

from coding_agent.security import ApprovalRequest, ApprovalStatus


class CliApprovalProvider:
    def __init__(self, input_fn: Callable[[str], str], output: TextIO) -> None:
        self.input_fn = input_fn
        self.output = output

    def request(self, request: ApprovalRequest) -> ApprovalStatus:
        self.output.write(f"\n需要批准 [{request.tool_name}]：{request.summary}\n")
        self.output.flush()
        try:
            answer = self.input_fn("批准执行？[y/N] ").strip().casefold()
        except (EOFError, StopIteration):
            answer = ""
        approved = answer in {"y", "yes", "是"}
        self.output.write("已批准。\n" if approved else "已拒绝。\n")
        self.output.flush()
        return ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
