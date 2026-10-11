from __future__ import annotations

import asyncio

import pytest
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError

from qianji import mcp_server, write_service
from qianji.errors import TagFeatureDisabledError


class _BusinessFeatureMcpClient:
    def __init__(self):
        self.current_account: str | None = None
        self.calls: list[tuple[str, dict]] = []

    def use_account(self, account_id: str | None):
        class _Ctx:
            def __enter__(ctx_self):
                self.current_account = account_id

            def __exit__(ctx_self, exc_type, exc, tb):
                self.current_account = None

        return _Ctx()

    async def create_refund(self, **kwargs):
        self.calls.append(("create_refund", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "refund_bill_id": 301, **kwargs}

    async def update_refund(self, **kwargs):
        self.calls.append(("update_refund", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def create_reimbursement(self, **kwargs):
        self.calls.append(("create_reimbursement", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "reimbursement_bill_id": 501, **kwargs}

    async def update_reimbursement(self, **kwargs):
        self.calls.append(("update_reimbursement", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def cancel_reimbursement(self, reimbursement_bill_ids):
        self.calls.append(
            (
                "cancel_reimbursement",
                {"account_id": self.current_account, "reimbursement_bill_ids": reimbursement_bill_ids},
            )
        )
        return {"ok": True, "reimbursement_bill_ids": reimbursement_bill_ids}

    async def budget_list(self, **kwargs):
        self.calls.append(("budget_list", {"account_id": self.current_account, **kwargs}))
        return {"data": {"list": [{"id": 1, "name": "月预算"}]}}

    async def upload_bill_image(self, **kwargs):
        self.calls.append(("upload_bill_image", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "image_key": "_image-key", **kwargs}

    async def tag_submit(self, **kwargs):
        self.calls.append(("tag_submit", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def tag_delete(self, **kwargs):
        self.calls.append(("tag_delete", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def tag_archive(self, **kwargs):
        self.calls.append(("tag_archive", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def tag_submit_group(self, **kwargs):
        self.calls.append(("tag_submit_group", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def tag_delete_group(self, **kwargs):
        self.calls.append(("tag_delete_group", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def tag_reorder_tag(self, **kwargs):
        self.calls.append(("tag_reorder_tag", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def tag_reorder_group(self, **kwargs):
        self.calls.append(("tag_reorder_group", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def create_loan_asset(self, **kwargs):
        self.calls.append(("create_loan_asset", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "asset_id": 701, **kwargs}

    async def update_loan_asset(self, **kwargs):
        self.calls.append(("update_loan_asset", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def record_loan_collection(self, **kwargs):
        self.calls.append(("record_loan_collection", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "bill_id": 801, "interest_bill_id": 802, **kwargs}

    async def update_loan_collection(self, **kwargs):
        self.calls.append(("update_loan_collection", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def create_debt_asset(self, **kwargs):
        self.calls.append(("create_debt_asset", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "asset_id": 901, **kwargs}

    async def update_debt_asset(self, **kwargs):
        self.calls.append(("update_debt_asset", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def record_debt_repayment(self, **kwargs):
        self.calls.append(("record_debt_repayment", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "bill_id": 1001, "interest_bill_id": 1002, **kwargs}

    async def update_debt_repayment(self, **kwargs):
        self.calls.append(("update_debt_repayment", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def add_loan_principal(self, **kwargs):
        self.calls.append(("add_loan_principal", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "bill_id": 811, **kwargs}

    async def update_loan_principal(self, **kwargs):
        self.calls.append(("update_loan_principal", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}

    async def add_debt_principal(self, **kwargs):
        self.calls.append(("add_debt_principal", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, "bill_id": 1011, **kwargs}

    async def update_debt_principal(self, **kwargs):
        self.calls.append(("update_debt_principal", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}


class _NormalBillMcpClient:
    def __init__(self):
        self.current_account: str | None = None
        self.calls: list[tuple[str, dict]] = []

    def use_account(self, account_id: str | None):
        class _Ctx:
            def __enter__(ctx_self):
                self.current_account = account_id

            def __exit__(ctx_self, exc_type, exc, tb):
                self.current_account = None

        return _Ctx()

    async def asset_list(self):
        return {"data": {"list": [{"id": 201}, {"id": 202}]}}

    async def category_list(self, book_id=-1, cat_type=None):
        return {"data": {"list": [{"id": 101, "type": cat_type}]}}

    async def bill_create(self, **kwargs):
        self.calls.append(("bill_create", {"account_id": self.current_account, **kwargs}))
        return {"data": {"sync_result": {"bill": {"new_ids": [701]}}}}


class _AuthBoundaryMcpClient:
    def __init__(self):
        self.current_account: str | None = None
        self.calls: list[tuple[str, dict]] = []
        self._accounts = [
            {"account_id": "a", "email": "a@example.com", "user_name": "A", "devname": "Device A"},
            {"account_id": "b", "email": "b@example.com", "user_name": "B", "devname": "Device B"},
        ]

    def use_account(self, account_id: str | None):
        class _Ctx:
            def __enter__(ctx_self):
                self.current_account = account_id

            def __exit__(ctx_self, exc_type, exc, tb):
                self.current_account = None

        return _Ctx()

    async def login(self, email: str, password: str, account_id: str | None = None):
        self.calls.append(("login", {"email": email, "password": password, "account_id": account_id}))
        self.current_account = account_id or email
        return {"data": {"user": {"email": email, "name": email.split("@", 1)[0]}}}

    def list_accounts(self):
        return list(self._accounts)

    async def tag_submit(self, **kwargs):
        self.calls.append(("tag_submit", {"account_id": self.current_account, **kwargs}))
        return {"ok": True, **kwargs}


def _access_token(account_id: str) -> AccessToken:
    return AccessToken(
        token="token",
        client_id=account_id,
        scopes=["qianji"],
        subject=account_id,
        claims={"account_id": account_id, "email": f"{account_id}@example.com"},
    )


def test_mcp_tool_rejects_account_id_different_from_bearer_subject(monkeypatch):
    client = _AuthBoundaryMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: _access_token("a"))

    async def run():
        with pytest.raises(ToolError, match="Cannot access a different Qianji account"):
            await mcp_server.mcp.call_tool(
                "create_tag",
                {"name": "差旅", "account_id": "b"},
            )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert client.calls == []


def test_mcp_tool_defaults_empty_account_id_to_bearer_subject(monkeypatch):
    client = _AuthBoundaryMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: _access_token("a"))

    async def run():
        return await mcp_server.mcp.call_tool("create_tag", {"name": "差旅"})

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert client.calls == [("tag_submit", {"account_id": "a", "name": "差旅", "group_id": ""})]


def test_mcp_list_accounts_returns_only_bearer_subject(monkeypatch):
    client = _AuthBoundaryMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("read")
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: _access_token("a"))

    async def run():
        return await mcp_server.mcp.call_tool("list_accounts", {})

    try:
        _, structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert structured == {
        "accounts": [{"account_id": "a", "email": "a@example.com", "user_name": "A", "devname": "Device A"}]
    }


def test_mcp_login_rejects_account_different_from_bearer_subject(monkeypatch):
    client = _AuthBoundaryMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("read")
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: _access_token("a"))

    async def run():
        with pytest.raises(ToolError, match="Cannot access a different Qianji account"):
            await mcp_server.mcp.call_tool("login", {"email": "b@example.com", "password": "secret", "account_id": "b"})

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert client.calls == []


def test_mcp_normal_bill_create_forwards_images_to_bill_create():
    client = _NormalBillMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        return await mcp_server.mcp.call_tool(
            "create_expense",
            {
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 201,
                "category_id": 101,
                "account_id": "a",
                "images": ["_receipt.jpg"],
            },
        )

    try:
        _, structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert structured["command"] == "create_expense"
    assert structured["resource"] == "bill"
    assert structured["ids"] == {"bill_id": "701", "new_ids": ["701"]}
    assert structured["data"]["images"] == ["_receipt.jpg"]
    assert (
        "bill_create",
        {
            "account_id": "a",
            "money": 12.5,
            "bill_type": 0,
            "cateid": 101,
            "assetid": 201,
            "remark": "",
            "book_id": -1,
            "bill_time": 1_775_993_400,
            "target_asset_id": -1,
            "tags": None,
            "images": ["_receipt.jpg"],
        },
    ) in client.calls


def test_mcp_normal_bill_update_forwards_empty_images_as_update(monkeypatch):
    client = _NormalBillMcpClient()
    captured: dict[str, object] = {}
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def fake_get_bill_by_id(client_arg, bill_id, *, book_id=-1, refresh=False):
        return {
            "id": bill_id,
            "raw": {
                "id": bill_id,
                "type": 0,
                "assetid": 201,
                "cateid": 101,
                "extra": {},
                "images": ["_old.jpg"],
            },
        }

    async def fake_update_bill_by_id(client_arg, bill_id, patch, *, book_id=-1):
        captured["bill_id"] = bill_id
        captured["patch"] = patch
        return {"ok": True}

    monkeypatch.setattr(write_service, "_get_bill_by_id", fake_get_bill_by_id)
    monkeypatch.setattr(write_service, "update_bill_by_id", fake_update_bill_by_id)

    async def run():
        return await mcp_server.mcp.call_tool(
            "update_expense",
            {
                "bill_id": 701,
                "images": [],
                "account_id": "a",
            },
        )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert captured == {"bill_id": 701, "patch": {"images": []}}


def test_mcp_bill_id_string_inputs_reach_client_without_precision_loss(monkeypatch):
    client = _BusinessFeatureMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("advanced")
    source_bill_id = "1781668838987000000"
    refund_bill_id = "1781668838987000001"
    reimbursement_bill_id = "1781668838987000002"
    update_bill_id = "1781668838987000003"

    async def fake_get_bill_by_id(client_arg, bill_id, *, book_id=-1, refresh=False):
        return {
            "id": bill_id,
            "raw": {
                "id": bill_id,
                "type": 0,
                "assetid": 201,
                "cateid": 101,
                "extra": {},
            },
        }

    async def fake_update_bill_by_id(client_arg, bill_id, patch, *, book_id=-1):
        return {"ok": True, "bill_id": bill_id, "patch": patch}

    monkeypatch.setattr(write_service, "_get_bill_by_id", fake_get_bill_by_id)
    monkeypatch.setattr(write_service, "update_bill_by_id", fake_update_bill_by_id)

    async def run():
        await mcp_server.mcp.call_tool(
            "create_refund",
            {
                "source_bill_id": source_bill_id,
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_refund",
            {
                "source_bill_id": source_bill_id,
                "refund_bill_id": refund_bill_id,
                "amount": 13.5,
                "occurred_at": "2026-04-13T19:30:00+08:00",
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_reimbursement",
            {
                "reimbursement_bill_id": reimbursement_bill_id,
                "source_bill_ids": [source_bill_id],
                "amount": 66.0,
                "occurred_at": "2026-04-15T19:30:00+08:00",
                "account_id": "a",
            },
        )
        update_expense_result = await mcp_server.mcp.call_tool(
            "update_expense",
            {
                "bill_id": update_bill_id,
                "amount": 10.0,
                "account_id": "a",
            },
        )
        return update_expense_result[1]

    try:
        update_result = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert next(call for name, call in client.calls if name == "create_refund")["source_bill_id"] == int(source_bill_id)
    assert next(call for name, call in client.calls if name == "update_refund")["refund_bill_id"] == int(refund_bill_id)
    assert next(call for name, call in client.calls if name == "update_reimbursement")["reimbursement_bill_id"] == int(
        reimbursement_bill_id
    )
    assert update_result["ids"]["bill_id"] == update_bill_id


def test_mcp_bill_id_outputs_are_strings(monkeypatch):
    large_bill_id = "1781668838987000000"
    client = _NormalBillMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("full")

    async def fake_get_bill_by_id(client_arg, bill_id, *, book_id=-1, refresh=False):
        return {
            "id": bill_id,
            "asset_id": 201,
            "category_id": 101,
            "book_id": -1,
            "raw": {"id": bill_id, "assetid": 201, "cateid": 101, "bookid": -1},
        }

    async def fake_search_bills(client_arg, **kwargs):
        return {
            "items": [
                {
                    "id": int(large_bill_id),
                    "asset_id": 201,
                    "category_id": 101,
                    "book_id": -1,
                    "raw": {"id": int(large_bill_id), "assetid": 201, "cateid": 101, "bookid": -1},
                }
            ],
            "page": 1,
        }

    monkeypatch.setattr(mcp_server, "_get_bill_by_id", fake_get_bill_by_id)
    monkeypatch.setattr(mcp_server, "_search_bills", fake_search_bills)

    async def run():
        create_result = await mcp_server.mcp.call_tool(
            "create_expense",
            {
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 201,
                "category_id": 101,
                "account_id": "a",
            },
        )
        get_result = await mcp_server.mcp.call_tool(
            "get_bill_by_id",
            {
                "bill_id": large_bill_id,
                "account_id": "a",
            },
        )
        search_result = await mcp_server.mcp.call_tool(
            "search_bills",
            {
                "bill_id": large_bill_id,
                "account_id": "a",
            },
        )
        return create_result[1], get_result[1], search_result[1]

    try:
        create_structured, get_structured, search_structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert create_structured["ids"]["bill_id"] == "701"
    assert create_structured["ids"]["new_ids"] == ["701"]
    assert create_structured["data"]["asset_id"] == 201
    assert get_structured["bill"]["id"] == large_bill_id
    assert get_structured["bill"]["raw"]["id"] == large_bill_id
    assert get_structured["bill"]["asset_id"] == 201
    assert search_structured["items"][0]["id"] == large_bill_id
    assert search_structured["items"][0]["raw"]["id"] == large_bill_id
    assert search_structured["items"][0]["category_id"] == 101


def test_mcp_business_feature_tools_forward_to_client_methods():
    client = _BusinessFeatureMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("advanced")

    async def run():
        await mcp_server.mcp.call_tool(
            "create_refund",
            {
                "source_bill_id": 101,
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 201,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_refund",
            {
                "source_bill_id": 101,
                "refund_bill_id": 301,
                "amount": 13.5,
                "occurred_at": "2026-04-13T19:30:00+08:00",
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "create_reimbursement",
            {
                "source_bill_ids": [401, 402],
                "amount": 88.0,
                "occurred_at": "2026-04-14T19:30:00+08:00",
                "asset_id": 202,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_reimbursement",
            {
                "reimbursement_bill_id": 501,
                "source_bill_ids": [401],
                "amount": 66.0,
                "occurred_at": "2026-04-15T19:30:00+08:00",
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "cancel_reimbursement",
            {
                "reimbursement_bill_ids": [501],
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool("get_budgets", {"book_id": -1, "account_id": "a"})
        await mcp_server.mcp.call_tool(
            "upload_bill_image",
            {
                "filename": "bill.jpg",
                "content_base64": "aW1hZ2U=",
                "mime_type": "image/jpeg",
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "create_tag",
            {"name": "差旅", "group_id": "g1", "color": "#167bff", "account_id": "a"},
        )
        await mcp_server.mcp.call_tool("update_tag", {"tag_id": "t1", "name": "出差", "account_id": "a"})
        await mcp_server.mcp.call_tool("delete_tag", {"tag_id": "t1", "account_id": "a"})
        await mcp_server.mcp.call_tool("archive_tag", {"tag_id": "t2", "account_id": "a"})
        await mcp_server.mcp.call_tool("create_tag_group", {"name": "项目", "account_id": "a"})
        await mcp_server.mcp.call_tool("update_tag_group", {"group_id": "g1", "name": "项目A", "account_id": "a"})
        await mcp_server.mcp.call_tool("delete_tag_group", {"group_id": "g1", "account_id": "a"})
        await mcp_server.mcp.call_tool("reorder_tags", {"tag_ids": ["t2", "t1"], "account_id": "a"})
        await mcp_server.mcp.call_tool("reorder_tag_groups", {"group_ids": ["g2", "g1"], "account_id": "a"})
        await mcp_server.mcp.call_tool(
            "create_loan_asset",
            {
                "name": "借给张三",
                "amount": 100.0,
                "startdate": "2026-01-01",
                "linked_asset_id": 201,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_loan_asset",
            {
                "asset_id": 701,
                "name": "借给张三 updated",
                "enddate": "2027-01-01",
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "add_loan_principal",
            {
                "loan_asset_id": 701,
                "amount": 60.0,
                "occurred_at": "2026-05-05T10:00:00+08:00",
                "from_asset_id": 201,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_loan_principal",
            {
                "bill_id": 811,
                "amount": 61.0,
                "occurred_at": "2026-05-06T10:00:00+08:00",
                "from_asset_id": 201,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "record_loan_collection",
            {
                "loan_asset_id": 701,
                "amount": 40.0,
                "occurred_at": "2026-05-01T10:00:00+08:00",
                "to_asset_id": 201,
                "interest_amount": 3.0,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_loan_collection",
            {
                "bill_id": 801,
                "amount": 41.0,
                "occurred_at": "2026-05-02T10:00:00+08:00",
                "to_asset_id": 201,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "create_debt_asset",
            {
                "name": "欠李四",
                "amount": 200.0,
                "startdate": "2026-01-01",
                "linked_asset_id": 201,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_debt_asset",
            {
                "asset_id": 901,
                "name": "欠李四 updated",
                "enddate": "2027-01-01",
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "add_debt_principal",
            {
                "debt_asset_id": 901,
                "amount": 70.0,
                "occurred_at": "2026-05-07T10:00:00+08:00",
                "to_asset_id": 202,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_debt_principal",
            {
                "bill_id": 1011,
                "amount": 71.0,
                "occurred_at": "2026-05-08T10:00:00+08:00",
                "to_asset_id": 202,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "record_debt_repayment",
            {
                "debt_asset_id": 901,
                "amount": 50.0,
                "occurred_at": "2026-05-03T10:00:00+08:00",
                "from_asset_id": 201,
                "interest_amount": 4.0,
                "account_id": "a",
            },
        )
        await mcp_server.mcp.call_tool(
            "update_debt_repayment",
            {
                "bill_id": 1001,
                "amount": 51.0,
                "occurred_at": "2026-05-04T10:00:00+08:00",
                "from_asset_id": 201,
                "account_id": "a",
            },
        )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert ("cancel_reimbursement", {"account_id": "a", "reimbursement_bill_ids": [501]}) in client.calls
    assert ("budget_list", {"account_id": "a", "book_id": -1}) in client.calls
    assert (
        "upload_bill_image",
        {"account_id": "a", "filename": "bill.jpg", "content": b"image", "mime_type": "image/jpeg"},
    ) in client.calls
    assert (
        "tag_submit",
        {"account_id": "a", "name": "差旅", "group_id": "g1", "color": "#167bff"},
    ) in client.calls
    assert ("tag_submit", {"account_id": "a", "name": "出差", "tag_id": "t1"}) in client.calls
    assert ("tag_delete", {"account_id": "a", "tag_id": "t1"}) in client.calls
    assert ("tag_archive", {"account_id": "a", "tag_id": "t2"}) in client.calls
    assert ("tag_submit_group", {"account_id": "a", "name": "项目"}) in client.calls
    assert ("tag_submit_group", {"account_id": "a", "group_id": "g1", "name": "项目A"}) in client.calls
    assert ("tag_delete_group", {"account_id": "a", "group_id": "g1"}) in client.calls
    assert ("tag_reorder_tag", {"account_id": "a", "tag_ids": ["t2", "t1"]}) in client.calls
    assert ("tag_reorder_group", {"account_id": "a", "group_ids": ["g2", "g1"]}) in client.calls

    assert [name for name, _ in client.calls] == [
        "create_refund",
        "update_refund",
        "create_reimbursement",
        "update_reimbursement",
        "cancel_reimbursement",
        "budget_list",
        "upload_bill_image",
        "tag_submit",
        "tag_submit",
        "tag_delete",
        "tag_archive",
        "tag_submit_group",
        "tag_submit_group",
        "tag_delete_group",
        "tag_reorder_tag",
        "tag_reorder_group",
        "create_loan_asset",
        "update_loan_asset",
        "add_loan_principal",
        "update_loan_principal",
        "record_loan_collection",
        "update_loan_collection",
        "create_debt_asset",
        "update_debt_asset",
        "add_debt_principal",
        "update_debt_principal",
        "record_debt_repayment",
        "update_debt_repayment",
    ]
    assert all(call["account_id"] == "a" for _, call in client.calls)
    assert client.calls[0][1]["occurred_at"] == 1_775_993_400
    assert next(call for name, call in client.calls if name == "record_loan_collection")["interest_amount"] == 3.0
    assert next(call for name, call in client.calls if name == "record_debt_repayment")["interest_amount"] == 4.0


def test_mcp_business_feature_tools_reject_invalid_image_keys_before_client_call():
    client = _BusinessFeatureMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("advanced")

    async def run():
        with pytest.raises(ToolError, match="Qianji image keys"):
            await mcp_server.mcp.call_tool(
                "create_refund",
                {
                    "source_bill_id": 101,
                    "amount": 12.5,
                    "occurred_at": "2026-04-12T19:30:00+08:00",
                    "account_id": "a",
                    "images": ["https://example.com/receipt.jpg"],
                },
            )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert client.calls == []


def test_infer_base_currency_picks_baseprice_one():
    currencies = [
        {"symbol": "CNY", "baseprice": "1.000000"},
        {"symbol": "USD", "baseprice": "6.770481"},
        {"symbol": "EUR", "baseprice": "7.5"},
    ]
    assert mcp_server._infer_base_currency(currencies) == "CNY"


def test_infer_base_currency_none_when_no_unit_rate():
    currencies = [{"symbol": "USD", "baseprice": "6.77"}, {"symbol": "EUR", "baseprice": "7.5"}]
    assert mcp_server._infer_base_currency(currencies) is None


def test_resolve_currency_sources():
    assert mcp_server._resolve_currency("USD", "CNY") == ("USD", "asset")
    assert mcp_server._resolve_currency(None, "CNY") == ("CNY", "account_default")
    assert mcp_server._resolve_currency("", "CNY") == ("CNY", "account_default")
    assert mcp_server._resolve_currency(None, "") == ("CNY", "fallback")


def test_resolve_currency_never_returns_none():
    currency, source = mcp_server._resolve_currency(None, "CNY")
    assert currency is not None
    assert source in {"asset", "account_default", "fallback"}


def test_normalize_book_marks_default():
    default_book = mcp_server._normalize_book({"bookid": -1, "name": "日常账本", "type": -1})
    assert default_book == {"id": -1, "name": "日常账本", "is_default": True, "type": -1}

    custom = mcp_server._normalize_book({"bookid": 123, "name": "旅行", "type": 1})
    assert custom["id"] == 123
    assert custom["is_default"] is False


def test_currency_direction_base_per_unit():
    # base currency (CNY) has base_per_unit == 1.0; quote currency reflects CNY-per-unit.
    currencies = [
        {"symbol": "CNY", "sign": "¥", "name": "人民币", "iscommon": 1, "baseprice": "1.000000", "pricetime": 1},
        {"symbol": "USD", "sign": "$", "name": "美元", "iscommon": 1, "baseprice": "6.770481", "pricetime": 1},
    ]
    base = mcp_server._infer_base_currency(currencies)
    assert base == "CNY"
    # simulate the per-item shaping the tool does
    usd = next(c for c in currencies if c["symbol"] == "USD")
    assert mcp_server._as_float(usd["baseprice"]) == 6.770481


def test_mcp_create_refund_forwards_currency(monkeypatch):
    client = _BusinessFeatureMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("advanced")

    async def run():
        await mcp_server.mcp.call_tool(
            "create_refund",
            {
                "source_bill_id": 301,
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "currency": {"code": "USD", "rate": 7.2},
            },
        )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    call = next(kwargs for name, kwargs in client.calls if name == "create_refund")
    assert call["currency"] == {"code": "USD", "rate": 7.2}


def test_mcp_create_refund_defaults_currency_to_none(monkeypatch):
    client = _BusinessFeatureMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("advanced")

    async def run():
        await mcp_server.mcp.call_tool(
            "create_refund",
            {
                "source_bill_id": 301,
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
            },
        )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    call = next(kwargs for name, kwargs in client.calls if name == "create_refund")
    assert call["currency"] is None


class _DeleteBillMcpClient:
    def __init__(self, del_ids: list):
        self.current_account: str | None = "a"
        self._del_ids = del_ids
        self.bill_delete_calls: list[int] = []
        self.cache_deleted: list[int] = []
        self._session_mgr = self

    def use_account(self, account_id: str | None):
        class _Ctx:
            def __enter__(ctx_self):
                self.current_account = account_id

            def __exit__(ctx_self, exc_type, exc, tb):
                self.current_account = "a"

        return _Ctx()

    async def bill_delete(self, bill_ids):
        self.bill_delete_calls.extend(bill_ids)
        return {"data": {"sync_result": {"bill": {"del_ids": self._del_ids}}}}

    async def cache_delete_bills(self, account_id: str, bill_ids: list[int]):
        self.cache_deleted.extend(bill_ids)


class _SwitchAccountMcpClient:
    def __init__(self):
        self.switched_to: str | None = None

    def set_account(self, account_id: str):
        self.switched_to = account_id


def test_mcp_delete_bill_clears_cache_after_upstream_confirms():
    large_bill_id = "1781668838987000002"
    client = _DeleteBillMcpClient(del_ids=[large_bill_id])
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("full")

    async def run():
        return await mcp_server.mcp.call_tool("delete_bill", {"bill_id": large_bill_id, "account_id": "a"})

    try:
        _, structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert structured == {"ok": True, "bill_id": large_bill_id, "deleted": True}
    assert client.bill_delete_calls == [int(large_bill_id)]
    assert client.cache_deleted == [int(large_bill_id)]


def test_mcp_delete_bill_keeps_cache_when_upstream_does_not_confirm():
    large_bill_id = "1781668838987000002"
    client = _DeleteBillMcpClient(del_ids=[])
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("full")

    async def run():
        return await mcp_server.mcp.call_tool("delete_bill", {"bill_id": large_bill_id, "account_id": "a"})

    try:
        _, structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert structured["ok"] is False
    assert structured["deleted"] is False
    assert structured["bill_id"] == large_bill_id
    assert structured["error"] == "upstream_not_confirmed"
    assert client.cache_deleted == []


def test_mcp_switch_account_sets_process_level_account():
    client = _SwitchAccountMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("read")

    async def run():
        return await mcp_server.mcp.call_tool("switch_account", {"account_id": "a"})

    try:
        _, structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")

    assert structured == {"ok": True, "switched_to": "a"}
    assert client.switched_to == "a"


def test_mcp_tags_disabled_error_advises_set_tags_enabled(monkeypatch):
    client = _NormalBillMcpClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def fake_create_expense(self, **kwargs):
        raise TagFeatureDisabledError()

    monkeypatch.setattr(write_service.WriteService, "create_expense", fake_create_expense)

    async def run():
        with pytest.raises(ToolError, match=r"set_tags_enabled\(true\)"):
            await mcp_server.mcp.call_tool(
                "create_expense",
                {
                    "amount": 1.0,
                    "occurred_at": "2026-04-12T19:30:00+08:00",
                    "asset_id": 201,
                    "category_id": 101,
                    "tags": ["咖啡"],
                    "account_id": "a",
                },
            )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("advanced")
