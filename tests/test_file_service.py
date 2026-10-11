from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from qianji.server.app import public_router
from qianji.session import SessionManager


def test_file_retention_uses_default_env_then_db_precedence(tmp_path, monkeypatch):
    from qianji.file_service import (
        FILE_RETENTION_DAYS_KEY,
        initialize_file_retention_config,
        resolve_file_retention_days,
    )

    async def run():
        mgr = SessionManager(tmp_path / "files.db")
        try:
            assert await resolve_file_retention_days(mgr) == 7

            monkeypatch.setenv("QIANJI_FILE_RETENTION_DAYS", "14")
            await initialize_file_retention_config(mgr)
            assert await mgr.get_global_setting(FILE_RETENTION_DAYS_KEY) == "14"
            assert await resolve_file_retention_days(mgr) == 14

            monkeypatch.setenv("QIANJI_FILE_RETENTION_DAYS", "7")
            assert await resolve_file_retention_days(mgr) == 14
        finally:
            await mgr.close()

    asyncio.run(run())


def test_public_file_download_is_unauthenticated_and_hides_expired_files(tmp_path):
    from qianji.file_service import create_generated_file

    async def seed():
        mgr = SessionManager(tmp_path / "files.db")
        now = time.time()
        live = await create_generated_file(
            mgr,
            filename="report.svg",
            content=b"<svg></svg>",
            content_type="image/svg+xml",
            retention_days=1,
            now=now,
        )
        expired = await create_generated_file(
            mgr,
            filename="old.txt",
            content=b"expired",
            content_type="text/plain; charset=utf-8",
            retention_days=1,
            now=now - 200_000,
        )
        await mgr.close()
        return live["file_id"], expired["file_id"]

    live_id, expired_id = asyncio.run(seed())
    app = FastAPI()
    app.state.file_session_mgr = SessionManager(tmp_path / "files.db")
    app.include_router(public_router)

    with TestClient(app) as client:
        live = client.get(f"/files/{live_id}")
        expired = client.get(f"/files/{expired_id}")
        missing = client.get("/files/not-real")

    asyncio.run(app.state.file_session_mgr.close())

    assert live.status_code == 200
    assert live.content == b"<svg></svg>"
    assert live.headers["content-type"].startswith("image/svg+xml")
    assert "attachment" in live.headers["content-disposition"]
    assert expired.status_code == 404
    assert missing.status_code == 404


def test_create_generated_file_deletes_expired_file_rows(tmp_path):
    from qianji.file_service import create_generated_file

    async def run():
        mgr = SessionManager(tmp_path / "files.db")
        try:
            await mgr.store_generated_file(
                file_id="expired",
                filename="old.csv",
                content_type="text/csv",
                content=b"old",
                created_at=900.0,
                expires_at=999.0,
            )
            created = await create_generated_file(
                mgr,
                filename="new.csv",
                content=b"new",
                content_type="text/csv",
                retention_days=1,
                now=1_000.0,
            )
            expired_even_at_old_time = await mgr.get_generated_file("expired", now=0.0)
            live = await mgr.get_generated_file(created["file_id"], now=1_000.0)
            return expired_even_at_old_time, live
        finally:
            await mgr.close()

    expired, live = asyncio.run(run())

    assert expired is None
    assert live is not None
    assert live["content"] == b"new"
