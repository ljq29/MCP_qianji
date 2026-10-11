from __future__ import annotations

import os
import time


class AuthFailureLimiter:
    """Fixed-window per-IP counter for failed authentication attempts."""

    def __init__(self, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._failures: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        entries = [t for t in self._failures.get(key, []) if now - t < self._window]
        if entries:
            self._failures[key] = entries
        else:
            # Drop empty buckets so the map does not grow unboundedly with the
            # number of distinct client IPs seen over the process lifetime.
            self._failures.pop(key, None)
        return entries

    def allowed(self, key: str) -> bool:
        return len(self._prune(key, time.monotonic())) < self._limit

    def record(self, key: str) -> None:
        now = time.monotonic()
        entries = self._prune(key, now)
        entries.append(now)
        self._failures[key] = entries


class MCPAuthFailureLimiter(AuthFailureLimiter):
    def __init__(self, limit: int | None = None, window: float | None = None) -> None:
        super().__init__(
            limit if limit is not None else int(os.environ.get("QIANJI_MCP_AUTH_FAILURE_LIMIT", "10")),
            window if window is not None else float(os.environ.get("QIANJI_MCP_AUTH_FAILURE_WINDOW", "60")),
        )


class RestAuthFailureLimiter(AuthFailureLimiter):
    def __init__(self, limit: int | None = None, window: float | None = None) -> None:
        super().__init__(
            limit if limit is not None else int(os.environ.get("QIANJI_AUTH_FAILURE_LIMIT", "30")),
            window if window is not None else float(os.environ.get("QIANJI_AUTH_FAILURE_WINDOW", "300")),
        )


def mask_secret_identifier(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    if "@" in text:
        local, domain = text.split("@", 1)
        local_prefix = local[:2] if len(local) > 2 else local[:1]
        if "." in domain:
            domain_name, suffix = domain.rsplit(".", 1)
            domain_mask = f"{domain_name[:1]}***.{suffix}" if domain_name else f"***.{suffix}"
        else:
            domain_mask = f"{domain[:1]}***" if domain else "***"
        return f"{local_prefix}***@{domain_mask}"
    if len(text) <= 4:
        return text[:1] + "***"
    return f"{text[:2]}***{text[-2:]}"
