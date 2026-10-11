from __future__ import annotations

import asyncio
import time
from types import MethodType

import pytest

from qianji import bill_ops
from qianji.client import QianjiClient
from qianji.errors import UpstreamTimeoutError
from qianji.session import SessionData

TOTAL_BILLS = 488
PAGE_SIZE = 200


def _raw_bill(bill_id: int, *, book_id: int = -1, bill_time: int | None = None, **extra) -> dict:
    return {
        "id": bill_id,
        "userid": "user-a",
        "bookid": book_id,
        "type": bill_id % 3,
        "money": float(bill_id),
        "cateid": 100 + (bill_id % 5),
        "time": bill_time if bill_time is not None else 1_700_000_000 + bill_id,
        "createtime": 1_700_000_000 + bill_id,
        "updatetime": 1_700_000_000 + bill_id,
        "remark": f"bill-{bill_id}",
        "status": 1,
        "assetid": -1,
        "fromid": -1,
        "targetid": -1,
        "extra": {"tags": []},
        **extra,
    }


def _make_pages(bills: list[dict], page_size: int = PAGE_SIZE) -> list[dict]:
    """Build syncv2/pull-style page responses (newest first not required)."""
    pages = []
    for i in range(0, len(bills), page_size):
        chunk = bills[i : i + page_size]
        pages.append(chunk)
    return pages


@pytest.fixture
def client(tmp_path):
    c = QianjiClient(db_path=tmp_path / "cache.db")
    c._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd-a",
            utoken="token-a",
            user_id="user-a",
            user_name="a",
            devid="dev-a",
            devname="Device a",
        )
    }
    c._current_account = "a"

    async def fake_currency_list(self):
        return {"data": {"list": [{"symbol": "CNY", "baseprice": "1.000000"}]}}

    c.currency_list = MethodType(fake_currency_list, c)  # type: ignore[method-assign]
    yield c
    asyncio.run(c.aclose())


def _install_fake_sync(client: QianjiClient, bills: list[dict], page_size: int = PAGE_SIZE) -> dict:
    """Patch sync_pull to emulate the server-driven protocol (single book).

    The server returns the next pageoffset/pagesign for the client to echo, plus
    a per-book lasttimes. Returns a dict tracking calls and the exact request
    params seen, so tests can assert the client echoes the cursor (no local increment).
    """
    pages = _make_pages(bills, page_size)
    state: dict = {"calls": 0, "requests": []}

    async def fake_sync_pull(
        self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""
    ) -> dict:
        state["calls"] += 1
        state["requests"].append(
            {"book_id": book_id, "page_offset": page_offset, "page_sign": page_sign, "last_times": last_times}
        )
        idx = int(page_offset)  # client echoes the server-returned offset; first call is 0
        chunk = pages[idx] if 0 <= idx < len(pages) else []
        has_more = 1 if idx < len(pages) - 1 else 0
        max_update = max((b["updatetime"] for b in chunk), default=0)
        return {
            "data": {
                "changes": chunk,
                "deletes": [],
                "hasmore": has_more,
                "bookid": book_id,
                "pageoffset": idx + 1,
                "pagesign": f"sign-{idx + 1}",
                "lasttimes": {str(book_id): max_update},
            }
        }

    client.sync_pull = MethodType(fake_sync_pull, client)  # type: ignore[method-assign]
    return state


def test_sync_to_cache_then_paginate_all_488(client):
    bills = [_raw_bill(i) for i in range(1, TOTAL_BILLS + 1)]
    _install_fake_sync(client, bills)

    async def run():
        stats = await bill_ops.sync_bills_to_cache(client, book_id=-1, full=True)
        assert stats["synced"] == TOTAL_BILLS
        assert stats["truncated"] is False

        seen: set[int] = set()
        cursor = None
        pages = 0
        while True:
            result = await bill_ops.list_recent_bills(client, book_id=-1, limit=50, cursor=cursor)
            pages += 1
            for item in result["items"]:
                assert item["id"] not in seen  # no duplicates across pages
                seen.add(item["id"])
            cursor = result["next_cursor"]
            if not result["has_more"]:
                break
            assert pages < 100  # guard against infinite loop
        return seen

    seen = asyncio.run(run())
    assert len(seen) == TOTAL_BILLS
    assert seen == set(range(1, TOTAL_BILLS + 1))


def test_list_recent_bills_newest_first(client):
    bills = [_raw_bill(i, bill_time=1000 + i) for i in range(1, 11)]
    _install_fake_sync(client, bills)

    async def run():
        await bill_ops.sync_bills_to_cache(client, full=True)
        result = await bill_ops.list_recent_bills(client, limit=5)
        return [item["occurred_at"] for item in result["items"]]

    times = asyncio.run(run())
    assert times == sorted(times, reverse=True)


def test_cursor_handles_equal_bill_times(client):
    # Many bills sharing one bill_time -> keyset cursor must not lose/dup rows.
    bills = [_raw_bill(i, bill_time=5000) for i in range(1, 21)]
    _install_fake_sync(client, bills)

    async def run():
        await bill_ops.sync_bills_to_cache(client, full=True)
        seen: list[int] = []
        cursor = None
        while True:
            result = await bill_ops.list_recent_bills(client, limit=7, cursor=cursor)
            seen.extend(item["id"] for item in result["items"])
            cursor = result["next_cursor"]
            if not result["has_more"]:
                break
        return seen

    seen = asyncio.run(run())
    assert sorted(seen) == list(range(1, 21))
    assert len(seen) == len(set(seen))  # no dupes


def test_get_bills_range_filters_by_time(client):
    bills = [_raw_bill(i, bill_time=1000 + i * 10) for i in range(1, 21)]
    _install_fake_sync(client, bills)

    async def run():
        await bill_ops.sync_bills_to_cache(client, full=True)
        return await bill_ops.get_bills_range(client, start_at=1050, end_at=1150, limit=50)

    result = asyncio.run(run())
    for item in result["items"]:
        assert 1050 <= item["occurred_at"] <= 1150


def test_get_bill_by_id_finds_oldest(client):
    bills = [_raw_bill(i) for i in range(1, TOTAL_BILLS + 1)]
    _install_fake_sync(client, bills)

    async def run():
        await bill_ops.sync_bills_to_cache(client, full=True)
        return await bill_ops.get_bill_by_id(client, 1)  # oldest bill

    bill = asyncio.run(run())
    assert bill is not None
    assert bill["id"] == 1
    assert "raw" in bill


def test_deletes_remove_from_cache(client):
    bills = [_raw_bill(i) for i in range(1, 6)]

    async def fake_sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        if page_offset == 0:
            return {
                "data": {
                    "changes": bills,
                    "deletes": [2, 4],
                    "hasmore": 0,
                    "bookid": book_id,
                    "pageoffset": 1,
                    "pagesign": "s1",
                    "lasttimes": {str(book_id): 1_700_000_010},
                }
            }
        return {"data": {"changes": [], "deletes": [], "hasmore": 0}}

    client.sync_pull = MethodType(fake_sync_pull, client)  # type: ignore[method-assign]

    async def run():
        stats = await bill_ops.sync_bills_to_cache(client, full=True)
        result = await bill_ops.list_recent_bills(client, limit=50)
        return stats, {item["id"] for item in result["items"]}

    stats, ids = asyncio.run(run())
    assert stats["deleted"] == 2
    assert ids == {1, 3, 5}


def test_sync_echoes_server_cursor_not_local_increment(client):
    """Client must send back the server's pageoffset/pagesign verbatim."""
    bills = [_raw_bill(i) for i in range(1, 11)]
    state = _install_fake_sync(client, bills, page_size=4)  # 3 pages: 4,4,2

    asyncio.run(bill_ops.sync_bills_to_cache(client, full=True))

    reqs = state["requests"]
    assert len(reqs) == 3
    # First page: offset 0, empty pagesign.
    assert reqs[0]["page_offset"] == 0
    assert reqs[0]["page_sign"] == ""
    # Subsequent pages echo the server's returned offset/sign (1->"sign-1", 2->"sign-2").
    assert reqs[1]["page_offset"] == 1
    assert reqs[1]["page_sign"] == "sign-1"
    assert reqs[2]["page_offset"] == 2
    assert reqs[2]["page_sign"] == "sign-2"


def test_full_sync_sends_multibook_lasttimes_on_increment(client):
    """After a full sync, an incremental sync sends the stored per-book lasttimes."""
    bills = [_raw_bill(i) for i in range(1, 4)]
    state = _install_fake_sync(client, bills, page_size=10)

    async def run():
        await bill_ops.sync_bills_to_cache(client, full=True)
        state["requests"].clear()
        await bill_ops.sync_bills_to_cache(client, book_id=-1, full=False)

    asyncio.run(run())
    # Incremental request carries the stored watermark map, not {"-1": 0}.
    assert state["requests"][0]["last_times"] == {"-1": 1_700_000_003}


def test_server_driven_multibook_walk(client):
    """A single bookid=-1 sync follows the server switching books mid-pagination."""
    book_default = [_raw_bill(i, book_id=-1, bill_time=1000 + i) for i in range(1, 4)]
    book_travel = [_raw_bill(100 + i, book_id=123, bill_time=2000 + i) for i in range(1, 3)]

    async def fake_sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        # Page 0 (first call): default book, server says switch to book 123 next.
        if page_sign == "":
            return {
                "data": {
                    "changes": book_default,
                    "deletes": [],
                    "hasmore": 1,
                    "bookid": 123,
                    "pageoffset": 0,
                    "pagesign": "to-travel",
                    "lasttimes": {"-1": 1003},
                }
            }
        # Page 1: book 123, done.
        return {
            "data": {
                "changes": book_travel,
                "deletes": [],
                "hasmore": 0,
                "bookid": 123,
                "pageoffset": 1,
                "pagesign": "end",
                "lasttimes": {"-1": 1003, "123": 2002},
            }
        }

    client.sync_pull = MethodType(fake_sync_pull, client)  # type: ignore[method-assign]

    async def run():
        stats = await bill_ops.sync_bills_to_cache(client, book_id=-1, full=True)
        default_page = await bill_ops.list_recent_bills(client, book_id=-1, limit=50)
        travel_page = await bill_ops.list_recent_bills(client, book_id=123, limit=50)
        # both books' watermarks persisted
        times = await client._session_mgr.get_all_bill_sync_times("a")
        return stats, default_page, travel_page, times

    stats, default_page, travel_page, times = asyncio.run(run())
    assert stats["synced"] == 5
    assert sorted(stats["books"]) == [-1, 123]
    assert {b["id"] for b in default_page["items"]} == {1, 2, 3}
    assert {b["id"] for b in travel_page["items"]} == {101, 102}
    assert times == {"-1": 1003, "123": 2002}


def test_bill_currency_falls_back_to_account_default(client):
    bills = [_raw_bill(1)]  # no extra.curr
    _install_fake_sync(client, bills)

    async def fake_currency_list(self):
        return {"data": {"list": [{"symbol": "CNY", "baseprice": "1.000000"}, {"symbol": "USD", "baseprice": "6.77"}]}}

    client.currency_list = MethodType(fake_currency_list, client)  # type: ignore[method-assign]

    async def run():
        await bill_ops.sync_bills_to_cache(client, full=True)
        return await bill_ops.list_recent_bills(client, limit=10)

    result = asyncio.run(run())
    item = result["items"][0]
    assert item["currency"] == "CNY"
    assert item["currency_source"] == "account_default"


def test_ttl_not_expired_skips_resync(client, monkeypatch):
    import qianji.bill_ops as ops

    monkeypatch.setattr(ops, "DEFAULT_BILL_CACHE_TTL", 300)
    bills = [_raw_bill(i) for i in range(1, 4)]
    state = _install_fake_sync(client, bills)

    async def run():
        await bill_ops.list_recent_bills(client, limit=10)  # first call: full sync
        calls_after_first = state["calls"]
        await bill_ops.list_recent_bills(client, limit=10)  # within TTL: no resync
        return calls_after_first, state["calls"]

    first, second = asyncio.run(run())
    assert second == first  # no extra sync_pull within TTL


def test_ttl_expired_triggers_incremental(client, monkeypatch):
    import qianji.bill_ops as ops

    monkeypatch.setattr(ops, "DEFAULT_BILL_CACHE_TTL", 60)
    bills = [_raw_bill(i) for i in range(1, 4)]
    state = _install_fake_sync(client, bills)

    async def run():
        await bill_ops.list_recent_bills(client, limit=10)
        first = state["calls"]
        # Force the cache to look stale.
        await client._session_mgr.set_bill_sync_state("a", -1, 0, 1.0)
        await bill_ops.list_recent_bills(client, limit=10)
        return first, state["calls"]

    first, second = asyncio.run(run())
    assert second > first  # stale cache triggered a resync


def test_refresh_forces_resync_within_ttl(client, monkeypatch):
    import qianji.bill_ops as ops

    monkeypatch.setattr(ops, "DEFAULT_BILL_CACHE_TTL", 300)
    bills = [_raw_bill(i) for i in range(1, 4)]
    state = _install_fake_sync(client, bills)

    async def run():
        await bill_ops.list_recent_bills(client, limit=10)
        first = state["calls"]
        await bill_ops.list_recent_bills(client, limit=10, refresh=True)
        return first, state["calls"]

    first, second = asyncio.run(run())
    assert second > first  # refresh bypasses TTL


def test_ttl_zero_disables_autosync(client, monkeypatch):
    import qianji.bill_ops as ops

    monkeypatch.setattr(ops, "DEFAULT_BILL_CACHE_TTL", 0)
    bills = [_raw_bill(i) for i in range(1, 4)]
    state = _install_fake_sync(client, bills)

    async def run():
        await bill_ops.list_recent_bills(client, limit=10)
        first = state["calls"]
        await client._session_mgr.set_bill_sync_state("a", -1, 0, 1.0)  # very stale
        await bill_ops.list_recent_bills(client, limit=10)
        return first, state["calls"]

    first, second = asyncio.run(run())
    assert second == first  # ttl=0 never auto-resyncs


def test_account_override_beats_env_default(client, monkeypatch):
    import qianji.bill_ops as ops

    monkeypatch.setattr(ops, "DEFAULT_BILL_CACHE_TTL", 300)

    async def run():
        # No override -> env default 300.
        assert await bill_ops.resolve_cache_ttl(client) == 300
        await client._session_mgr.set_account_setting("a", bill_ops.BILL_CACHE_TTL_KEY, "0")
        assert await bill_ops.resolve_cache_ttl(client) == 0
        # Bad override falls back to env default.
        await client._session_mgr.set_account_setting("a", bill_ops.BILL_CACHE_TTL_KEY, "notint")
        assert await bill_ops.resolve_cache_ttl(client) == 300
        # Clearing override falls back to env default.
        await client._session_mgr.delete_account_setting("a", bill_ops.BILL_CACHE_TTL_KEY)
        assert await bill_ops.resolve_cache_ttl(client) == 300

    asyncio.run(run())


def test_incremental_sync_missing_lasttimes_preserves_existing_watermark(client):
    """A response without lasttimes must not reset a book's non-zero watermark to 0."""

    async def fake_sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        return {
            "data": {
                "changes": [],
                "deletes": [],
                "hasmore": 0,
                "bookid": book_id,
                "pageoffset": 1,
                "pagesign": "s1",
            }
        }

    client.sync_pull = MethodType(fake_sync_pull, client)  # type: ignore[method-assign]

    async def run():
        await client._session_mgr.set_bill_sync_state("a", 5, 12345, time.time())
        await bill_ops.sync_bills_to_cache(client, book_id=5, full=False)
        preserved = await client._session_mgr.get_bill_sync_state("a", 5)
        await bill_ops.sync_bills_to_cache(client, book_id=6, full=False)
        fresh = await client._session_mgr.get_bill_sync_state("a", 6)
        return preserved, fresh

    (preserved_time, _), (fresh_time, fresh_synced_at) = asyncio.run(run())
    assert preserved_time == 12345
    assert fresh_time == 0
    assert fresh_synced_at > 0


def test_concurrent_ensure_bill_cache_runs_one_full_sync(client):
    bills = [_raw_bill(i) for i in range(1, 6)]
    calls = 0

    async def fake_sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {
            "data": {
                "changes": bills,
                "deletes": [],
                "hasmore": 0,
                "bookid": book_id,
                "pageoffset": 1,
                "pagesign": "s1",
                "lasttimes": {str(book_id): 1_700_000_005},
            }
        }

    client.sync_pull = MethodType(fake_sync_pull, client)  # type: ignore[method-assign]

    async def run():
        await asyncio.gather(*(bill_ops.ensure_bill_cache(client) for _ in range(5)))
        return calls

    assert asyncio.run(run()) == 1


def test_base_currency_cached_with_ttl(client):
    calls = 0

    async def fake_currency_list(self):
        nonlocal calls
        calls += 1
        return {"data": {"list": [{"symbol": "USD", "baseprice": "1.000000"}]}}

    client.currency_list = MethodType(fake_currency_list, client)  # type: ignore[method-assign]

    async def run():
        first = await bill_ops.get_base_currency(client)
        second = await bill_ops.get_base_currency(client)
        client._base_currency_cache["a"] = (first, time.time() - bill_ops.BASE_CURRENCY_CACHE_TTL - 1)
        third = await bill_ops.get_base_currency(client)
        return first, second, third, calls

    first, second, third, total_calls = asyncio.run(run())
    assert first == second == third == "USD"
    assert total_calls == 2


def test_base_currency_falls_back_to_cny_only_when_no_base_currency(client):
    async def fake_currency_list(self):
        return {"data": {"list": [{"symbol": "USD", "baseprice": "6.77"}]}}

    client.currency_list = MethodType(fake_currency_list, client)  # type: ignore[method-assign]

    assert asyncio.run(bill_ops.get_base_currency(client)) == "CNY"


def test_base_currency_propagates_upstream_errors(client):
    async def fake_currency_list(self):
        raise UpstreamTimeoutError(0, "boom")

    client.currency_list = MethodType(fake_currency_list, client)  # type: ignore[method-assign]

    async def run():
        with pytest.raises(UpstreamTimeoutError):
            await bill_ops.get_base_currency(client)

    asyncio.run(run())
