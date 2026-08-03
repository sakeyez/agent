"""Central validation, policy, approval, timeout, and result normalization."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from pydantic import ValidationError

from coding_agent.observability import (
    AuditEvent,
    AuditSink,
    NullAuditSink,
    SecretRedactor,
)
from coding_agent.security import (
    ApprovalProvider,
    ApprovalRequest,
    ApprovalStatus,
    DefaultOperationPolicy,
    OperationPolicy,
    PolicyDecision,
    UnavailableApprovalProvider,
)
from coding_agent.tools.contracts import (
    ToolCall,
    ToolEffect,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolHandlerOutput,
)
from coding_agent.tools.registry import ToolRegistry

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        timeout_seconds: float = 10,
        max_output_bytes: int = 64 * 1024,
        policy: OperationPolicy | None = None,
        approval_provider: ApprovalProvider | None = None,
        audit_sink: AuditSink | None = None,
        redactor: SecretRedactor | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.registry = registry
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.policy = policy or DefaultOperationPolicy()
        self.approval_provider = approval_provider or UnavailableApprovalProvider()
        self.audit_sink = audit_sink or NullAuditSink()
        self.redactor = redactor or SecretRedactor()

    def execute(
        self, call: ToolCall, context: ToolExecutionContext
    ) -> ToolExecutionResult:
        started = time.monotonic()
        tool = self.registry.get(call.name)
        if tool is None:
            result = self._error(
                call,
                "unknown_tool",
                f"Unknown tool: {call.name}",
                started=started,
            )
            self._record_completion(call, context, call.name, result)
            return result

        try:
            arguments = tool.args_schema.model_validate(call.arguments)
        except ValidationError as error:
            summary = "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors(include_url=False, include_input=False)
            )
            result = self._error(
                call,
                "invalid_arguments",
                summary,
                started=started,
            )
            self._record_completion(call, context, call.name, result)
            return result

        policy = self.policy.evaluate(tool, arguments, context)
        summary = _CONTROL_CHARACTERS.sub(
            " ", self.redactor.redact(policy.summary)
        )[:500]
        if policy.decision is PolicyDecision.DENY:
            result = self._error(
                call,
                "policy_denied",
                policy.reason,
                started=started,
                policy_decision=policy.decision.value,
                approved=False,
            )
            self._record_completion(call, context, summary, result)
            return result

        approved: bool | None = None
        if policy.decision is PolicyDecision.REQUIRE_APPROVAL:
            try:
                approval = self.approval_provider.request(
                    ApprovalRequest(
                        run_id=context.run_id,
                        call_id=call.call_id,
                        tool_name=call.name,
                        effect=tool.effect,
                        summary=summary,
                    )
                )
            except Exception:
                approval = ApprovalStatus.UNAVAILABLE
            if approval is ApprovalStatus.UNAVAILABLE:
                result = self._error(
                    call,
                    "approval_required",
                    "Interactive approval is required for this operation",
                    started=started,
                    policy_decision=policy.decision.value,
                    approved=False,
                )
                self._record_completion(call, context, summary, result)
                return result
            if approval is ApprovalStatus.DENIED:
                result = self._error(
                    call,
                    "approval_denied",
                    "The user denied this operation",
                    started=started,
                    policy_decision=policy.decision.value,
                    approved=False,
                )
                self._record_completion(call, context, summary, result)
                return result
            approved = True

        if tool.effect is not ToolEffect.READ:
            try:
                self.audit_sink.record(
                    AuditEvent(
                        event="tool_authorized",
                        run_id=context.run_id,
                        call_id=call.call_id,
                        tool=call.name,
                        summary=summary,
                        policy_decision=policy.decision.value,
                        approved=approved,
                    )
                )
            except Exception:
                return self._error(
                    call,
                    "audit_unavailable",
                    "The operation was not executed because its audit record could not be written",
                    started=started,
                    policy_decision=policy.decision.value,
                    approved=approved,
                )

        timeout = tool.timeout_seconds or self.timeout_seconds
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{call.name}")
        future = pool.submit(tool.handler, arguments, context)
        try:
            raw_output = future.result(timeout=timeout)
        except FutureTimeoutError:
            future.cancel()
            result = self._error(
                call,
                "timeout",
                f"Tool exceeded the {timeout:g} second timeout",
                started=started,
                policy_decision=policy.decision.value,
                approved=approved,
            )
        except Exception as error:
            message = str(error).strip() or type(error).__name__
            workspace_root = str(context.workspace.root)
            message = message.replace(workspace_root, "<workspace>")
            message = message.replace(context.workspace.root.as_posix(), "<workspace>")
            result = self._error(
                call,
                "execution_error",
                message[:500],
                started=started,
                policy_decision=policy.decision.value,
                approved=approved,
            )
        else:
            output = (
                raw_output
                if isinstance(raw_output, ToolHandlerOutput)
                else ToolHandlerOutput(content=str(raw_output))
            )
            normalized, truncated = self._truncate(self.redactor.redact(output.content))
            result = ToolExecutionResult(
                call_id=call.call_id,
                name=call.name,
                content=normalized,
                is_error=output.is_error,
                truncated=truncated,
                error_code=output.error_code,
                duration_ms=self._duration_ms(started),
                policy_decision=policy.decision.value,
                approved=approved,
                metadata={
                    **(output.metadata or {}),
                    **(
                        {"operation_kind": policy.operation_kind}
                        if policy.operation_kind is not None
                        else {}
                    ),
                },
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        self._record_completion(call, context, summary, result)
        return result

    def _truncate(self, content: str) -> tuple[str, bool]:
        encoded = content.encode("utf-8")
        if len(encoded) <= self.max_output_bytes:
            return content, False
        marker = "\n[output truncated]\n"
        marker_bytes = marker.encode("utf-8")
        if len(marker_bytes) >= self.max_output_bytes:
            return marker_bytes[: self.max_output_bytes].decode("utf-8", errors="ignore"), True
        available = max(0, self.max_output_bytes - len(marker_bytes))
        head_size = available // 2
        tail_size = available - head_size
        head = encoded[:head_size].decode("utf-8", errors="ignore")
        tail = encoded[-tail_size:].decode("utf-8", errors="ignore") if tail_size else ""
        normalized = head + marker + tail
        while len(normalized.encode("utf-8")) > self.max_output_bytes:
            tail = tail[1:]
            normalized = head + marker + tail
        return normalized, True

    def _record_completion(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
        summary: str,
        result: ToolExecutionResult,
    ) -> None:
        try:
            self.audit_sink.record(
                AuditEvent(
                    event="tool_completed",
                    run_id=context.run_id,
                    call_id=call.call_id,
                    tool=call.name,
                    summary=summary,
                    policy_decision=result.policy_decision or "not_evaluated",
                    approved=result.approved,
                    status="error" if result.is_error else "success",
                    error_code=result.error_code,
                    duration_ms=result.duration_ms,
                )
            )
        except Exception:
            pass

    def _error(
        self,
        call: ToolCall,
        code: str,
        message: str,
        *,
        started: float,
        policy_decision: str | None = None,
        approved: bool | None = None,
    ) -> ToolExecutionResult:
        normalized, truncated = self._truncate(
            self.redactor.redact(f"[{code}] {message}")
        )
        return ToolExecutionResult(
            call_id=call.call_id,
            name=call.name,
            content=normalized,
            is_error=True,
            truncated=truncated,
            error_code=code,
            duration_ms=self._duration_ms(started),
            policy_decision=policy_decision,
            approved=approved,
        )

    @staticmethod
    def _duration_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))
