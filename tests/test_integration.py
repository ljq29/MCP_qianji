from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from qianji import bill_ops
from qianji.client import QianjiClient


@dataclass(frozen=True)
class IntegrationContext:
    email: str
    password: str
    account_id: str | None
    db_path: Path


@pytest.fixture(scope="session")
def integration_ctx(tmp_path_factory):
    if os.environ.get("QIANJI_RUN_INTEGRATION") != "1":
        pytest.skip("set QIANJI_RUN_INTEGRATION=1 to run real Qianji integration tests")

    email = os.environ.get("QIANJI_TEST_EMAIL")
    password = os.environ.get("QIANJI_TEST_PASSWORD")
    account_id = os.environ.get("QIANJI_TEST_ACCOUNT_ID")
    if not email or not password:
        pytest.skip("set QIANJI_TEST_EMAIL and QIANJI_TEST_PASSWORD to run integration tests")

    raw_db_path = os.environ.get("QIANJI_TEST_DB_PATH")
    if raw_db_path:
        db_path = Path(raw_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        db_path = tmp_path_factory.mktemp("qianji-integration") / "integration.db"

    ctx = IntegrationContext(email=email, password=password, account_id=account_id, db_path=db_path)
    yield ctx

    async def cleanup():
        async with QianjiClient(db_path=ctx.db_path) as client:
            if client.current_account:
                await client.logout(client.current_account)

    asyncio.run(cleanup())


@pytest.mark.integration
def test_real_login_and_asset_list(integration_ctx):
    ctx = integration_ctx

    async def run():
        async with QianjiClient(db_path=ctx.db_path) as client:
            await client.login(ctx.email, ctx.password, account_id=ctx.account_id)
            result = await client.asset_list()
            assert result["ec"] == 200

    asyncio.run(run())


@pytest.mark.integration
def test_real_sync_pulls_all_bills_with_cursor(integration_ctx):
    """Full sync must populate the cache, and list_recent_bills must walk every
    bill via next_cursor — no truncation, count matches what sync reported."""
    ctx = integration_ctx

    async def run():
        async with QianjiClient(db_path=ctx.db_path) as client:
            await client.login(ctx.email, ctx.password, account_id=ctx.account_id)
            aid = client.current_account

            stats = await bill_ops.sync_bills_to_cache(client, book_id=-1, full=True)
            assert stats["truncated"] is False, "increase HARD_RANGE_MAX_PAGES if this trips"

            # Walk every page via the cursor and count.
            seen: set[int] = set()
            cursor = None
            while True:
                page = await bill_ops.list_recent_bills(client, book_id=-1, limit=200, cursor=cursor)
                for item in page["items"]:
                    seen.add(item["id"])
                cursor = page["next_cursor"]
                if not page["has_more"]:
                    break

            # Cursor walk should reach every cached bill for the default book.
            cached = await client._session_mgr.cache_list_bills(aid, book_id=-1, limit=10**9)
            assert len(seen) == len(cached)
            # Every bill has a non-null currency.
            if seen:
                sample = await bill_ops.get_bill_by_id(client, next(iter(seen)))
                assert sample is not None
                assert sample["currency"] is not None

    asyncio.run(run())
