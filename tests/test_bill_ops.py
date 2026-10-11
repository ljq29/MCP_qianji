from __future__ import annotations

import asyncio
from types import MethodType

import pytest

from qianji import bill_ops
from qianji.client import QianjiClient
from qianji.session import SessionData


@pytest.fixture
def client(tmp_path):
    c = QianjiClient(db_path=tmp_path / "ops.db")
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


def _raw_bill(bill_id: int) -> dict:
    return {
        "id": bill_id,
        "userid": "user-a",
        "bookid": -1,
        "type": 0,
        "money": 12.5,
        "cateid": 101,
        "time": 1_700_000_000 + bill_id,
        "createtime": 1_700_000_000 + bill_id,
        "updatetime": 1_700_000_000 + bill_id,
        "remark": f"bill-{bill_id}",
        "status": 1,
        "assetid": 7,
        "fromid": -1,
        "targetid": -1,
        "extra": {"tags": ["t1", "t2"]},
    }


def test_normalize_bill_shape():
    item = bill_ops.normalize_bill(_raw_bill(42))
    assert item["id"] == 42
    assert item["bill_type"] == "expense"
    assert item["type_code"] == 0
    assert item["amount"] == 12.5
    assert item["category_id"] == 101
    assert item["asset_id"] == 7
    assert item["tag_ids"] == ["t1", "t2"]
    assert "raw" not in item  # lists omit raw by default


def test_normalize_bill_includes_occurred_at_iso():
    item = bill_ops.normalize_bill(_raw_bill(42))
    assert item["occurred_at"] == 1_700_000_042
    assert item["occurred_at_iso"] == "2023-11-15T06:14:02+08:00"


def test_search_page_size_capped_at_500():
    assert bill_ops._search_page_size(5000) == 500
    assert bill_ops._search_page_size(500) == 500
    assert bill_ops._search_page_size(None) == bill_ops.DEFAULT_LIST_LIMIT


def test_search_bills_rejects_end_before_start(client):
    async def run():
        with pytest.raises(ValueError, match="end_at must be greater than or equal to start_at"):
            await bill_ops.search_bills(client, start_at=100, end_at=50)

    asyncio.run(run())


def test_get_bills_range_rejects_end_before_start(client):
    async def run():
        with pytest.raises(ValueError, match="end_at must be greater than or equal to start_at"):
            await bill_ops.get_bills_range(client, start_at=100, end_at=50)

    asyncio.run(run())


def test_normalize_bill_unknown_type():
    raw = _raw_bill(1)
    raw["type"] = 99
    item = bill_ops.normalize_bill(raw)
    assert item["bill_type"] == "type_99"
    assert item["type_code"] == 99


def test_normalize_bill_counted_flag():
    expense = _raw_bill(1)  # type 0
    assert bill_ops.normalize_bill(expense)["counted"] is True
    not_counted = _raw_bill(2)
    not_counted["type"] = 3  # NOT_COUNTED
    assert bill_ops.normalize_bill(not_counted)["counted"] is False


def test_apply_bill_patch_updates_and_clears_images():
    raw = _raw_bill(1)
    raw["images"] = ["_old.jpg"]

    updated = bill_ops.apply_bill_patch(raw, {"images": ["_new.jpg"]})
    cleared = bill_ops.apply_bill_patch(updated, {"images": []})

    assert updated["images"] == ["_new.jpg"]
    assert cleared["images"] == []


def test_cursor_roundtrip():
    cursor = bill_ops.encode_cursor(1234, 567)
    assert bill_ops.decode_cursor(cursor) == (1234, 567)
    assert bill_ops.decode_cursor(None) is None
    assert bill_ops.decode_cursor("") is None


def test_decode_cursor_rejects_garbage():
    for bad in ("!!not-base64!!", "aGVsbG8", bill_ops.encode_cursor(1, 2)[:-3] + "xyz"):
        with pytest.raises(ValueError, match="Invalid pagination cursor"):
            bill_ops.decode_cursor(bad)


def test_list_recent_bills_rejects_bad_cursor_without_sync(client):
    async def run():
        with pytest.raises(ValueError, match="Invalid pagination cursor"):
            await bill_ops.list_recent_bills(client, cursor="!!not-base64!!")

    asyncio.run(run())


def test_get_bills_range_rejects_bad_cursor_without_sync(client):
    async def run():
        with pytest.raises(ValueError, match="Invalid pagination cursor"):
            await bill_ops.get_bills_range(client, start_at=0, end_at=100, cursor="!!not-base64!!")

    asyncio.run(run())


def test_update_bill_by_id_uses_cache_not_scan(client):
    """update_bill_by_id must locate via cache and never page-scan."""
    captured = {}

    async def fake_sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        # one page seeds the cache with bill 1
        if page_offset == 0:
            return {"data": {"changes": [_raw_bill(1)], "deletes": [], "hasmore": 0}}
        return {"data": {"changes": [], "deletes": [], "hasmore": 0}}

    async def fake_bill_update(self, bill: dict):
        captured["bill"] = bill
        return {"data": {"sync_result": {"bill": {"update_ids": [bill["id"]]}}}}

    client.sync_pull = MethodType(fake_sync_pull, client)  # type: ignore[method-assign]
    client.bill_update = MethodType(fake_bill_update, client)  # type: ignore[method-assign]

    async def run():
        # prime the cache (full sync), then update with NO scan params
        await bill_ops.sync_bills_to_cache(client, full=True)
        return await bill_ops.update_bill_by_id(client, 1, {"money": 999.0, "remark": "updated"})

    result = asyncio.run(run())
    assert result is not None
    assert captured["bill"]["money"] == 999.0
    assert captured["bill"]["remark"] == "updated"


def test_update_bill_by_id_writes_back_to_cache(client):
    async def fake_sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        if page_offset == 0:
            return {"data": {"changes": [_raw_bill(5)], "deletes": [], "hasmore": 0}}
        return {"data": {"changes": [], "deletes": [], "hasmore": 0}}

    async def fake_bill_update(self, bill: dict):
        return {"data": {"sync_result": {"bill": {"update_ids": [bill["id"]]}}}}

    async def fake_currency_list(self):
        return {"data": {"list": [{"symbol": "CNY", "baseprice": "1.000000"}]}}

    client.sync_pull = MethodType(fake_sync_pull, client)  # type: ignore[method-assign]
    client.bill_update = MethodType(fake_bill_update, client)  # type: ignore[method-assign]
    client.currency_list = MethodType(fake_currency_list, client)  # type: ignore[method-assign]

    async def run():
        await bill_ops.sync_bills_to_cache(client, full=True)
        await bill_ops.update_bill_by_id(client, 5, {"remark": "changed"})
        # read straight from cache to confirm write-back
        return await bill_ops.get_bill_by_id(client, 5)

    bill = asyncio.run(run())
    assert bill is not None
    assert bill["remark"] == "changed"


def test_update_bill_by_id_missing_returns_none(client):
    async def fake_sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        return {"data": {"changes": [], "deletes": [], "hasmore": 0}}

    client.sync_pull = MethodType(fake_sync_pull, client)  # type: ignore[method-assign]

    async def run():
        return await bill_ops.update_bill_by_id(client, 999, {"money": 1.0})

    assert asyncio.run(run()) is None


def test_create_bill_writes_cache_with_synced_status(client):
    """create_bill writes the new bill into the cache as status=1 (synced),
    while the upstream payload keeps status=2."""
    captured = {}

    async def fake_request(self, ctrl, act, params=None):
        captured["params"] = params
        return {"ec": 200, "data": {"sync_result": {"bill": {"new_ids": [1]}}}}

    client._request = MethodType(fake_request, client)  # type: ignore[method-assign]

    async def run():
        await client.bill_create(money=5.0, remark="x", book_id=-1)
        # find the created bill in cache (snowflake id), confirm status normalized to 1
        rows = await client._session_mgr.cache_list_bills("a", limit=10)
        return rows

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["status"] == 1
    # upstream payload (changelist) still carried status=2
    import json as _json

    sent = _json.loads(captured["params"]["v"])
    sent_bill = _json.loads(sent["bills"]["changelist"])[0]
    assert sent_bill["status"] == 2
