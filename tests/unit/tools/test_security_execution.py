from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from coding_agent.observability import JsonlAuditSink, SecretRedactor
from coding_agent.security import ApprovalStatus
from coding_agent.tools import (
    ToolCall,
    ToolDefinition,
    ToolEffect,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
)
from coding_agent.workspace import WorkspaceContext


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixedApproval:
    def __init__(self, status: ApprovalStatus) -> None:
        self.status = status
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return self.status


class FailingAudit:
    def record(self, event) -> None:
        raise OSError("disk unavailable")


def _write_registry(handler=lambda _args, _context: "changed") -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition(
                name="write_test",
                description="write",
                args_schema=EmptyArguments,
                handler=handler,
                effect=ToolEffect.WRITE,
                summary_builder=lambda _args, _context: "change token=visible-secret",
            )
        ]
    )


def _context(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(WorkspaceContext.from_path(tmp_path), run_id="run-1")


def test_unavailable_and_denied_approval_do_not_execute(tmp_path: Path) -> None:
    calls = []
    registry = _write_registry(lambda _args, _context: calls.append(True) or "changed")

    unavailable = ToolExecutor(registry).execute(
        ToolCall("call-1", "write_test", {}), _context(tmp_path)
    )
    denied_provider = FixedApproval(ApprovalStatus.DENIED)
    denied = ToolExecutor(registry, approval_provider=denied_provider).execute(
        ToolCall("call-2", "write_test", {}), _context(tmp_path)
    )

    assert unavailable.error_code == "approval_required"
    assert denied.error_code == "approval_denied"
    assert calls == []


def test_side_effect_fails_closed_when_pre_audit_cannot_be_written(tmp_path: Path) -> None:
    calls = []
    result = ToolExecutor(
        _write_registry(lambda _args, _context: calls.append(True) or "changed"),
        approval_provider=FixedApproval(ApprovalStatus.APPROVED),
        audit_sink=FailingAudit(),
    ).execute(ToolCall("call-1", "write_test", {}), _context(tmp_path))

    assert result.error_code == "audit_unavailable"
    assert calls == []


def test_audit_records_sanitized_authorization_and_completion(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    result = ToolExecutor(
        _write_registry(lambda _args, _context: "result visible-secret"),
        approval_provider=FixedApproval(ApprovalStatus.APPROVED),
        audit_sink=JsonlAuditSink(audit_path),
        redactor=SecretRedactor(["visible-secret"]),
    ).execute(ToolCall("call-1", "write_test", {}), _context(tmp_path))

    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [record["event"] for record in records] == ["tool_authorized", "tool_completed"]
    assert all(record["run_id"] == "run-1" for record in records)
    assert "visible-secret" not in audit_path.read_text(encoding="utf-8")
    assert "visible-secret" not in result.content
