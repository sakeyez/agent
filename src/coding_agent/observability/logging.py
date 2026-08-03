"""Application logging configuration and secret filtering."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

_SECRET_NAME = re.compile(r"(key|token|secret|password|credential|authorization|cookie)", re.I)
_INLINE_SECRET = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}|"
    r"((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+"
)


class SecretRedactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = tuple(
            sorted(
                {value for value in secrets if value and len(value) >= 4},
                key=len,
                reverse=True,
            )
        )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        extra_secrets: Iterable[str] = (),
    ) -> "SecretRedactor":
        source = os.environ if environment is None else environment
        values = [value for name, value in source.items() if _SECRET_NAME.search(name)]
        return cls([*values, *extra_secrets])

    def redact(self, value: str) -> str:
        result = value
        for secret in self._secrets:
            result = result.replace(secret, "***")

        def replace(match: re.Match[str]) -> str:
            prefix = match.group(1) or match.group(2) or ""
            return prefix + "***"

        return _INLINE_SECRET.sub(replace, result)


def sanitized_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environment is None else environment
    return {
        name: value
        for name, value in source.items()
        if not _SECRET_NAME.search(name) and not name.upper().startswith("GIT_")
    }
