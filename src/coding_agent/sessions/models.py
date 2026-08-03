"""Provider-independent session and run identifiers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NewType

from coding_agent.providers.base import ModelSelection

SessionId = NewType("SessionId", str)
RunId = NewType("RunId", str)


@dataclass(frozen=True, slots=True)
class Session:
    id: SessionId
    name: str
    model: ModelSelection
    created_at: datetime
    updated_at: datetime


__all__ = ["RunId", "Session", "SessionId"]
