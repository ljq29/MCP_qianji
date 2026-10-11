from __future__ import annotations

import os
import secrets
import time
from typing import Any

FILE_RETENTION_DAYS_KEY = "file_retention_days"
FILE_RETENTION_ENV = "QIANJI_FILE_RETENTION_DAYS"
DEFAULT_FILE_RETENTION_DAYS = 7
SECONDS_PER_DAY = 86400


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


async def resolve_file_retention_days(session_mgr: Any) -> int:
    db_value = await session_mgr.get_global_setting(FILE_RETENTION_DAYS_KEY)
    if (days := _positive_int(db_value)) is not None:
        return days
    if (days := _positive_int(os.environ.get(FILE_RETENTION_ENV))) is not None:
        return days
    return DEFAULT_FILE_RETENTION_DAYS


async def initialize_file_retention_config(session_mgr: Any) -> None:
    if await session_mgr.get_global_setting(FILE_RETENTION_DAYS_KEY) is not None:
        return
    if (days := _positive_int(os.environ.get(FILE_RETENTION_ENV))) is not None:
        await session_mgr.set_global_setting(FILE_RETENTION_DAYS_KEY, str(days))


def new_file_id() -> str:
    return secrets.token_urlsafe(24)


async def create_generated_file(
    session_mgr: Any,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    retention_days: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    created_at = time.time() if now is None else float(now)
    days = retention_days if retention_days is not None else await resolve_file_retention_days(session_mgr)
    expires_at = created_at + max(1, int(days)) * SECONDS_PER_DAY
    file_id = new_file_id()
    await session_mgr.delete_expired_generated_files(now=created_at)
    await session_mgr.store_generated_file(
        file_id=file_id,
        filename=filename,
        content_type=content_type,
        content=content,
        created_at=created_at,
        expires_at=expires_at,
    )
    return {
        "file_id": file_id,
        "filename": filename,
        "content_type": content_type,
        "created_at": created_at,
        "expires_at": expires_at,
    }


def build_download_url(file_id: str, *, base_url: str = "") -> str:
    path = f"/files/{file_id}"
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"
