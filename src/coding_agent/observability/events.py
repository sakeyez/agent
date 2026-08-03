"""Sanitized events emitted during agent and tool runs."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event: str
    run_id: str
    call_id: str
    tool: str
    summary: str
    policy_decision: str
    approved: bool | None = None
    status: str | None = None
    error_code: str | None = None
    duration_ms: int | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            **{key: value for key, value in asdict(self).items() if value is not None},
        }


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class NullAuditSink:
    def record(self, event: AuditEvent) -> None:
        return None


class JsonlAuditSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(event.as_record(), ensure_ascii=True, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(payload + "\n")
