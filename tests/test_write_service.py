from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from qianji.errors import TagFeatureDisabledError
from qianji.write_service import WriteService


class FakeSessionManager:
    def __init__(self):
        self.bills: dict[int, dict] = {}
        self.deleted: list[int] = []
        self.settings: dict[tuple[str, str], str] = {}

    async def cache_get_bill(self, account_id: str, bill_id: int):
        return self.bills.get(int(bill_id))

    async def cache_upsert_bills(self, account_id: str, bills: list[dict]):
        for bill in bills:
            self.bills[int(bill["id"])] = dict(bill)
        return len(bills)

    async def cache_delete_bills(self, account_id: str, bill_ids: list[int]):
        self.deleted.extend(int(bill_id) for bill_id in bill_ids)
        for bill_id in bill_ids:
            self.bills.pop(int(bill_id), None)
        return len(bill_ids)

    async def set_account_setting(self, account_id: str, key: str, value: str):
        self.settings[(account_id, key)] = str(value)

    async def delete_account_setting(self, account_id: str, key: str):
        self.settings.pop((account_id, key), None)

    async def get_account_setting(self, account_id: str, key: str):
        return self.settings.get((account_id, key))


class FakeWriteClient:
    def __init__(self):
        self.current_account = "a"
        self.session = SimpleNamespace(user_configs={"tagon": 1})
        self._session_mgr = FakeSessionManager()
        self.calls: list[tuple[str, dict]] = []
        self.bill_create_result = {"data": {"sync_result": {"bill": {"new_ids": [1781668838987000001]}}}}
        self.bill_update_result = {"data": {"sync_result": {"bill": {"update_ids": [1781668838987000002]}}}}
        self.bill_delete_result = {"data": {"sync_result": {"bill": {"del_ids": [1781668838987000002]}}}}

    @contextmanager
    def use_account(self, account_id: str | None):
        old = self.current_account
        if account_id:
            self.current_account = account_id
        try:
            yield
        finally:
            self.current_account = old

    async def asset_list(self):
        return {"data": {"list": [{"id": 201}, {"id": 202}, {"assetid": 203}]}}

    async def currency_list(self):
        return {"data": {"list": [{"symbol": "CNY", "baseprice": "1.000000"}]}}

    async def category_list(self, book_id=-1, cat_type=None):
        return {"data": {"list": [{"id": 101, "type": cat_type}, {"cateid": 102}]}}

    async def tag_list(self):
        return {"data": {"list": [{"id": "t1", "name": "咖啡"}]}}

    async def tag_submit(self, **kwargs):
        self.calls.append(("tag_submit", dict(kwargs)))
        return {"data": {"tag": {"id": kwargs.get("tag_id", "t-new"), "name": kwargs.get("name")}}}

    async def tag_delete(self, **kwargs):
        self.calls.append(("tag_delete", dict(kwargs)))
        return {"ok": True, **kwargs}

    async def tag_archive(self, **kwargs):
        self.calls.append(("tag_archive", dict(kwargs)))
        return {"ok": True, **kwargs}

    async def tag_submit_group(self, **kwargs):
        self.calls.append(("tag_submit_group", dict(kwargs)))
        return {"data": {"group": {"id": kwargs.get("group_id", "g-new")}}}

    async def tag_delete_group(self, **kwargs):
        self.calls.append(("tag_delete_group", dict(kwargs)))
        return {"ok": True, **kwargs}

    async def tag_reorder_tag(self, **kwargs):
        self.calls.append(("tag_reorder_tag", dict(kwargs)))
        return {"ok": True, **kwargs}

    async def tag_reorder_group(self, **kwargs):
        self.calls.append(("tag_reorder_group", dict(kwargs)))
        return {"ok": True, **kwargs}

    async def bill_create(self, **kwargs):
        self.calls.append(("bill_create", dict(kwargs)))
        return self.bill_create_result

    async def bill_update(self, bill: dict):
        self.calls.append(("bill_update", dict(bill)))
        return self.bill_update_result

    async def bill_delete(self, bill_ids: list[int]):
        self.calls.append(("bill_delete", {"bill_ids": list(bill_ids)}))
        return self.bill_delete_result

    async def create_refund(self, **kwargs):
        self.calls.append(("create_refund", dict(kwargs)))
        return {"ok": True, "refund_bill_id": 301, **kwargs}

    async def create_reimbursement(self, **kwargs):
        self.calls.append(("create_reimbursement", dict(kwargs)))
        return {"ok": True, "reimbursement_bill_id": 401, **kwargs}

    async def cancel_reimbursement(self, reimbursement_bill_ids: list[int]):
        self.calls.append(("cancel_reimbursement", {"reimbursement_bill_ids": list(reimbursement_bill_ids)}))
        return {"ok": True, "reimbursement_bill_ids": reimbursement_bill_ids}

    async def create_loan_asset(self, **kwargs):
        self.calls.append(("create_loan_asset", dict(kwargs)))
        return {"ok": True, "asset_id": 501, **kwargs}

    async def record_loan_collection(self, **kwargs):
        self.calls.append(("record_loan_collection", dict(kwargs)))
        return {"ok": True, "bill_id": 601, "interest_bill_id": 602, **kwargs}

    async def upload_bill_image(self, **kwargs):
        self.calls.append(("upload_bill_image", dict(kwargs)))
        return {"ok": True, "image_key": "_receipt.jpg", **kwargs}

    async def set_tags_enabled(self, enabled: bool):
        self.calls.append(("set_tags_enabled", {"enabled": enabled}))
        self.session.user_configs["tagon"] = 1 if enabled else 0
        return {"ok": True, "enabled": enabled}


def test_create_expense_validates_resolves_tags_and_returns_unified_shape():
    async def run():
        client = FakeWriteClient()
        result = await WriteService(client).create_expense(
            amount=12.5,
            occurred_at="2026-04-12T19:30:00+08:00",
            asset_id=201,
            category_id=101,
            remark="coffee",
            tags=["咖啡"],
            images=["_receipt.jpg"],
        )
        return client, result

    client, result = asyncio.run(run())

    assert client.calls[0] == (
        "bill_create",
        {
            "money": 12.5,
            "bill_type": 0,
            "cateid": 101,
            "assetid": 201,
            "remark": "coffee",
            "book_id": -1,
            "bill_time": 1_775_993_400,
            "target_asset_id": -1,
            "tags": ["t1"],
            "images": ["_receipt.jpg"],
        },
    )
    assert result["ok"] is True
    assert result["command"] == "create_expense"
    assert result["resource"] == "bill"
    assert result["ids"] == {
        "bill_id": "1781668838987000001",
        "new_ids": ["1781668838987000001"],
    }
    assert result["data"]["amount"] == 12.5
    assert result["data"]["tag_ids"] == ["t1"]
    assert result["raw_result"] == client.bill_create_result


def test_create_expense_returns_business_error_when_upstream_does_not_confirm_new_id():
    async def run():
        client = FakeWriteClient()
        client.bill_create_result = {"data": {"sync_result": {"bill": {"new_ids": []}}}}
        return await WriteService(client).create_expense(
            amount=1,
            occurred_at="2026-04-12T19:30:00+08:00",
            asset_id=201,
            category_id=101,
        )

    result = asyncio.run(run())

    assert result["ok"] is False
    assert result["command"] == "create_expense"
    assert result["error_code"] == "upstream_not_confirmed"
    assert result["raw_result"] == {"data": {"sync_result": {"bill": {"new_ids": []}}}}


def test_update_expense_uses_cached_bill_and_returns_string_bill_id():
    async def run():
        client = FakeWriteClient()
        client._session_mgr.bills[1781668838987000002] = {
            "id": 1781668838987000002,
            "userid": "u",
            "bookid": -1,
            "type": 0,
            "money": 10.0,
            "cateid": 101,
            "time": 1_700_000_000,
            "createtime": 1_700_000_000,
            "updatetime": 1_700_000_000,
            "remark": "old",
            "status": 1,
            "assetid": 201,
            "fromid": -1,
            "targetid": -1,
            "extra": {"tags": []},
        }
        result = await WriteService(client).update_expense(
            bill_id="1781668838987000002",
            amount=11.0,
            occurred_at="2026-04-13T19:30:00+08:00",
            remark="new",
        )
        return client, result

    client, result = asyncio.run(run())

    assert client.calls[0][0] == "bill_update"
    assert client.calls[0][1]["money"] == 11.0
    assert client.calls[0][1]["remark"] == "new"
    assert client.calls[0][1]["time"] == 1_776_079_800
    assert result["ok"] is True
    assert result["ids"]["bill_id"] == "1781668838987000002"
    assert result["ids"]["update_ids"] == ["1781668838987000002"]


def test_delete_bill_returns_business_error_when_upstream_does_not_confirm_delete():
    async def run():
        client = FakeWriteClient()
        client.bill_delete_result = {"data": {"sync_result": {"bill": {"del_ids": []}}}}
        return await WriteService(client).delete_bill("1781668838987000002")

    result = asyncio.run(run())

    assert result["ok"] is False
    assert result["command"] == "delete_bill"
    assert result["ids"]["bill_id"] == "1781668838987000002"
    assert result["error_code"] == "upstream_not_confirmed"


def test_refund_reimbursement_and_loan_results_stringify_bill_ids():
    async def run():
        client = FakeWriteClient()
        service = WriteService(client)
        refund = await service.create_refund(
            source_bill_id="1781668838987000001",
            amount=2.5,
            occurred_at="2026-04-12T19:30:00+08:00",
            asset_id=201,
            tags=["咖啡"],
        )
        reimbursement = await service.create_reimbursement(
            source_bill_ids=["1781668838987000001"],
            amount=2.5,
            occurred_at="2026-04-12T19:30:00+08:00",
            asset_id=201,
        )
        collection = await service.record_loan_collection(
            loan_asset_id=501,
            amount=10,
            occurred_at="2026-04-12T19:30:00+08:00",
            to_asset_id=201,
            interest_amount=1,
        )
        return client, refund, reimbursement, collection

    client, refund, reimbursement, collection = asyncio.run(run())

    assert next(call for call in client.calls if call[0] == "create_refund")[1]["source_bill_id"] == 1781668838987000001
    assert refund["ids"]["source_bill_id"] == "1781668838987000001"
    assert refund["ids"]["refund_bill_id"] == "301"
    assert reimbursement["ids"]["source_bill_ids"] == ["1781668838987000001"]
    assert reimbursement["ids"]["reimbursement_bill_id"] == "401"
    assert collection["ids"]["bill_id"] == "601"
    assert collection["ids"]["interest_bill_id"] == "602"
    assert next(call for call in client.calls if call[0] == "record_loan_collection")[1]["occurred_at"] == 1_775_993_400
    assert collection["data"]["occurred_at"] == "2026-04-12T19:30:00+08:00"


def test_tag_image_and_settings_writes_use_unified_shape():
    async def run():
        client = FakeWriteClient()
        service = WriteService(client)
        tag = await service.create_tag(name="交通", group_id="g1", color="#fff")
        group = await service.create_tag_group(name="项目")
        upload = await service.upload_bill_image(filename="r.png", content=b"abc", mime_type="image/png")
        enabled = await service.set_tags_enabled(True)
        ttl = await service.set_bill_cache_ttl(0)
        return client, tag, group, upload, enabled, ttl

    client, tag, group, upload, enabled, ttl = asyncio.run(run())

    assert tag["ok"] is True
    assert tag["command"] == "create_tag"
    assert tag["resource"] == "tag"
    assert group["resource"] == "tag_group"
    assert upload["data"]["image_key"] == "_receipt.jpg"
    assert enabled["data"]["enabled"] is True
    assert ttl["data"] == {"ttl_seconds": 0, "source": "account_override"}
    assert client._session_mgr.settings[("a", "bill_cache_ttl")] == "0"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"amount": 0, "occurred_at": "2026-04-12T19:30:00+08:00", "asset_id": 201, "category_id": 101}, "amount"),
        ({"amount": 1, "occurred_at": "2026-04-12T19:30:00", "asset_id": 201, "category_id": 101}, "timezone"),
        ({"amount": 1, "occurred_at": "2026-04-12T19:30:00+08:00", "asset_id": 999, "category_id": 101}, "asset"),
        ({"amount": 1, "occurred_at": "2026-04-12T19:30:00+08:00", "asset_id": 201, "category_id": 999}, "category"),
    ],
)
def test_create_expense_validation_errors(kwargs, message):
    async def run():
        await WriteService(FakeWriteClient()).create_expense(**kwargs)

    with pytest.raises(ValueError, match=message):
        asyncio.run(run())


def test_create_expense_propagates_tag_feature_disabled_error():
    async def run():
        client = FakeWriteClient()
        client.session.user_configs["tagon"] = 0
        await WriteService(client).create_expense(
            amount=1,
            occurred_at="2026-04-12T19:30:00+08:00",
            asset_id=201,
            category_id=101,
            tags=["咖啡"],
        )

    with pytest.raises(TagFeatureDisabledError):
        asyncio.run(run())


def test_upload_bill_image_rejects_invalid_image_key_result_shape_is_not_used_for_validation():
    async def run():
        await WriteService(FakeWriteClient()).create_expense(
            amount=1,
            occurred_at="2026-04-12T19:30:00+08:00",
            asset_id=201,
            category_id=101,
            images=["https://example.com/r.jpg"],
        )

    with pytest.raises(ValueError, match="image keys"):
        asyncio.run(run())
