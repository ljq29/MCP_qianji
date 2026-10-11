from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from copy import deepcopy
from types import MethodType
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qianji import mcp_server
from qianji.client import QianjiClient
from qianji.endpoints import ENDPOINTS, ParamSpec
from qianji.server.routes import router
from qianji.session import SessionData, SessionManager
from qianji.sign import hash_password

AUTH_HEADERS = {"Authorization": f"Bearer qj1.md5.b~{hash_password('pwd')}"}


def _sample_value(spec: ParamSpec):
    if spec.name == "pwd":
        return "plain-password"
    if spec.annotation is int:
        return 123
    if spec.annotation is float:
        return 12.5
    if spec.annotation is bool:
        return True
    if spec.name == "v":
        return {"sample": True}
    return f"{spec.name}-value"


def _required_kwargs(endpoint):
    return {spec.name: _sample_value(spec) for spec in endpoint.params if spec.required and not spec.auto}


class _CaptureBillCreateClient:
    def __init__(self):
        self.current_account: str | None = None
        self.calls: list[dict] = []

    @contextmanager
    def use_account(self, account_id: str | None):
        old = self.current_account
        self.current_account = account_id
        try:
            yield
        finally:
            self.current_account = old

    async def bill_create(self, **kwargs):
        self.calls.append({"account_id": self.current_account, **kwargs})
        return {"data": {"sync_result": {"bill": {"new_ids": [9988]}}}}


class _BusinessBillCreateClient(_CaptureBillCreateClient):
    def __init__(self):
        super().__init__()
        self._session = SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
            user_configs={"tagon": 1},
        )
        self.submitted_tags: list[str] = []

    @property
    def session(self):
        return self._session

    async def asset_list(self, status: int = 0):
        return {"data": {"list": [{"id": 100, "name": "现金"}, {"id": 200, "name": "银行卡"}]}}

    async def category_list(self, book_id: int = -1, cat_type: int | None = None):
        return {
            "data": {
                "list": [
                    {"id": 3, "name": "餐饮", "type": cat_type, "sublist": []},
                    {"id": 30, "name": "工资父类", "type": cat_type, "sublist": [{"id": 31, "name": "工资"}]},
                ]
            }
        }

    async def tag_list(self):
        return {"data": {"list": [{"id": 10, "name": "工作"}, {"id": 11, "name": "差旅"}]}}

    async def tag_submit(self, name: str, groupid: str = "", tagid: str = "", color: str | None = None):
        self.submitted_tags.append(name)
        new_id = str(11 + len(self.submitted_tags))
        return {"data": {"tag": {"id": new_id, "name": name}}}

    async def get_tags_enabled(self):
        return self.session.user_configs.get("tagon") == 1

    async def set_tags_enabled(self, enabled: bool):
        self.session.user_configs["tagon"] = 1 if enabled else 0
        return {"ok": True, "enabled": bool(enabled)}


class _BusinessBillUpdateClient(_BusinessBillCreateClient):
    def __init__(self):
        super().__init__()
        self._session_mgr = SessionManager(":memory:")
        self.updated: list[dict] = []
        self.sync_calls: list[dict] = []

    async def currency_list(self):
        return {"data": {"list": [{"symbol": "CNY", "baseprice": "1.000000"}]}}

    async def sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        self.sync_calls.append(
            {
                "book_id": book_id,
                "last_time": last_time,
                "page_offset": page_offset,
                "last_times": last_times,
                "page_sign": page_sign,
            }
        )
        return {"data": {"changes": [], "deletes": [], "hasmore": 0}}

    async def bill_update(self, bill: dict):
        self.updated.append(deepcopy(bill))
        return {"data": {"sync_result": {"bill": {"update_ids": [bill["id"]]}}}}

    async def aclose(self):
        await self._session_mgr.close()


class _McpAccountStatusClient:
    def __init__(self):
        self.current_account: str | None = "a"
        self.refresh_calls = 0
        self._session = SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            user_name="Alice",
            devid="device-a",
            devname="Device A",
            user_configs={"tagon": 0},
        )

    @property
    def session(self):
        return self._session

    @property
    def is_logged_in(self):
        return True

    @contextmanager
    def use_account(self, account_id: str | None):
        yield

    async def login(self, email, password, account_id=None):
        self.current_account = account_id or email
        self._session.user_configs["tagon"] = 1
        return {"data": {"user": {"id": "user-a", "name": "Alice", "email": email}}}

    async def refresh_user_configs(self):
        self.refresh_calls += 1
        self._session.user_configs["tagon"] = 1
        return self._session.user_configs

    def list_accounts(self):
        return [
            {
                "account_id": "a",
                "user_name": "Alice",
                "email": "a@example.com",
                "devname": "Device A",
                "is_current": True,
                "tags_enabled": self._session.user_configs.get("tagon") == 1,
            }
        ]


def _raw_mcp_bill(
    bill_id: int,
    *,
    bill_type: int = 0,
    money: float = 1.0,
    asset_id: int = 100,
    target_asset_id: int = -1,
    category_id: int = 3,
    bill_time: int = 1_700_000_000,
    remark: str = "old",
    tag_ids: list[int] | None = None,
) -> dict:
    return {
        "id": bill_id,
        "userid": "user-a",
        "bookid": -1,
        "type": bill_type,
        "money": money,
        "cateid": category_id,
        "time": bill_time,
        "createtime": bill_time - 1,
        "updatetime": bill_time,
        "remark": remark,
        "status": 1,
        "assetid": asset_id,
        "fromid": asset_id if bill_type == 2 else -1,
        "targetid": target_asset_id if bill_type == 2 else -1,
        "extra": {"tags": tag_ids or []},
        "platform": 0,
    }


# 112 个端点路径的基准快照，校验 ENDPOINTS 注册表与文档基准一致。
DOCUMENTED_ENDPOINT_PATHS = [
    "/account/login",
    "/account/logout",
    "/account/register",
    "/account/refreshtoken",
    "/account/getverifycode",
    "/account/setpwd",
    "/account/bindaccount",
    "/account/bindwx",
    "/account/unbind",
    "/account/loginwx",
    "/account/bind",
    "/account/cancelaccount",
    "/account/cancelthirdaccount",
    "/account/loginrecords2",
    "/account/removelogin",
    "/bill/syncall",
    "/bill/refund2",
    "/billbatch/deletev2",
    "/billbatch/modify",
    "/syncv2/pull",
    "/asset/list",
    "/asset/info",
    "/asset/submit",
    "/asset/delete",
    "/asset/deletecheck",
    "/asset/typelist",
    "/asset/banklist",
    "/asset/listloan",
    "/asset/reorder",
    "/asset/visible",
    "/asset/finish",
    "/asset_group/list",
    "/asset_group/submit",
    "/asset_group/delete",
    "/asset_group/addasset",
    "/asset_group/removeasset",
    "/asset_group/reorder",
    "/assetline/list",
    "/assetline/submit",
    "/assetline/init",
    "/assetline/reset",
    "/category/listv2",
    "/category/submitv2",
    "/category/submitmultiple",
    "/category/deletev2",
    "/category/deletecheckv2",
    "/category/iconlist",
    "/category/reorder3",
    "/category/changelevel",
    "/category/movesubcate",
    "/book/list",
    "/book/submit",
    "/book/delete",
    "/book/deletecheck",
    "/book/copy",
    "/book/members",
    "/book/invitecode",
    "/book/addbycode",
    "/book/infobycode",
    "/book/quit",
    "/book/reorder",
    "/book/config",
    "/book/visible",
    "/book/typelist",
    "/tag/list",
    "/tag/syncall",
    "/tag/submittag",
    "/tag/deletetag",
    "/tag/archivetag",
    "/tag/submitgroup",
    "/tag/deletegroup",
    "/tag/reordertag",
    "/tag/reordergroup",
    "/budget/list",
    "/budget/submitv2",
    "/usercard/listv2",
    "/usercard/submit",
    "/usercard/delete",
    "/usercard/reorder",
    "/currency/listv2",
    "/saving/list",
    "/saving/submit",
    "/saving/delete",
    "/saving/deposit",
    "/saving/canceldeposit",
    "/installment/list",
    "/installment/submit",
    "/installment/delete",
    "/installment/config",
    "/installment/bindbill",
    "/installment/prepay",
    "/repeat_task/list",
    "/repeat_task/submit",
    "/repeat_task/delete",
    "/repeat_task/toggle",
    "/repeat_task/bindbill",
    "/repeat_task/resetcount",
    "/repeat_task/holidays",
    "/baoxiao/baoxiao",
    "/baoxiao/cancelbaoxiao",
    "/baoxiao/upgradev2",
    "/import/platforms",
    "/import/submitfile",
    "/import/packs",
    "/import/packbills",
    "/import/confirmresult",
    "/import/deletepack",
    "/user/update",
    "/user/updateconfig",
    "/user/getaddress",
    "/user/submitaddress",
    "/client/checkfeedback2",
]


def test_endpoint_registry_matches_full_api_docs():
    assert len(ENDPOINTS) == 112
    assert [endpoint.path for endpoint in ENDPOINTS] == DOCUMENTED_ENDPOINT_PATHS


def test_qianji_client_exposes_all_endpoint_methods(tmp_path):
    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, params or {}))
        return {"ec": 200, "data": {"ctrl": ctrl, "act": act, "params": params or {}}}

    async def fake_public(self, ctrl, act, params=None):
        public_calls.append((ctrl, act, params or {}))
        return {"ec": 200, "data": {"ctrl": ctrl, "act": act, "params": params or {}}}

    calls = []
    public_calls = []
    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._request = MethodType(fake_request, client)
    client._request_public = MethodType(fake_public, client)

    async def run():
        for endpoint in ENDPOINTS:
            method = getattr(client, endpoint.method_name)
            result = await method(**_required_kwargs(endpoint))
            assert result["data"]["ctrl"] == endpoint.ctrl
            assert result["data"]["act"] == endpoint.act

    asyncio.run(run())

    seen = {(ctrl, act) for ctrl, act, _ in calls + public_calls}
    assert seen == {(endpoint.ctrl, endpoint.act) for endpoint in ENDPOINTS}
    assert ("asset", "info", {"did": 123}) in calls
    assert any(ctrl == "account" and act == "login" for ctrl, act, _ in public_calls)
    asyncio.run(client.aclose())


def test_public_account_methods_normalize_plaintext_password(tmp_path):
    async def fake_public(self, ctrl, act, params=None):
        return {"ec": 200, "data": {"ctrl": ctrl, "act": act, "params": params or {}}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._request_public = MethodType(fake_public, client)

    async def run():
        return await client.account_login(v="user@example.com", pwd="plain-password")

    result = asyncio.run(run())

    assert result["data"]["params"]["pwd"] != "plain-password"
    assert len(result["data"]["params"]["pwd"]) == 32
    asyncio.run(client.aclose())


def test_tag_api_uses_app_wire_shape(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, params or {}))
        return {"ec": 200, "data": {"ctrl": ctrl, "act": act, "params": params or {}}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._request = MethodType(fake_request, client)

    async def run():
        listed = await client.tag_list()
        created = await client.tag_submit(name="codex")
        updated = await client.tag_submit(name="codex2", groupid="group-1", tagid="tag-1", color="FF336699")
        return listed, created, updated

    listed, created, updated = asyncio.run(run())

    assert listed["data"]["params"] == {"status": -1}
    assert created["data"]["params"] == {"v": json.dumps({"groupid": "", "name": "codex"}, ensure_ascii=False)}
    assert updated["data"]["params"] == {
        "did": "tag-1",
        "v": json.dumps({"groupid": "group-1", "name": "codex2", "color": "FF336699"}, ensure_ascii=False),
    }
    assert calls == [
        ("tag", "list", {"status": -1}),
        ("tag", "submittag", {"v": json.dumps({"groupid": "", "name": "codex"}, ensure_ascii=False)}),
        (
            "tag",
            "submittag",
            {
                "did": "tag-1",
                "v": json.dumps({"groupid": "group-1", "name": "codex2", "color": "FF336699"}, ensure_ascii=False),
            },
        ),
    ]
    asyncio.run(client.aclose())


def test_asset_submit_accepts_balance_reconciliation_params_and_auto_uid(tmp_path):
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "path": request.url.path,
                "form": {key: values[0] for key, values in parse_qs(request.content.decode()).items()},
            }
        )
        return httpx.Response(200, json={"ec": 200, "data": {"ok": True}})

    client = QianjiClient(db_path=tmp_path / "sessions.db", base_url="https://qianji.test")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run():
        return await client.asset_submit(
            v={"id": 100, "money": 123.45},
            diffparams={"cateid": 3, "billflag": 1, "remark": "余额平账"},
            type={"bookid": -1},
        )

    result = asyncio.run(run())

    assert result == {"ec": 200, "data": {"ok": True}}
    assert requests == [
        {
            "path": "/asset/submit",
            "form": {
                "v": json.dumps({"id": 100, "money": 123.45}, ensure_ascii=False),
                "diffparams": json.dumps({"cateid": 3, "billflag": 1, "remark": "余额平账"}, ensure_ascii=False),
                "type": json.dumps({"bookid": -1}, ensure_ascii=False),
                "uid": "user-a",
                "fr": "user-a",
            },
        }
    ]
    asyncio.run(client.aclose())


def test_update_asset_balance_without_adjustment_preserves_asset_payload(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, deepcopy(params or {})))
        if (ctrl, act) == ("asset", "info"):
            return {
                "ec": 200,
                "data": {
                    "asset": {
                        "id": 100,
                        "name": "现金",
                        "type": 1,
                        "stype": 11,
                        "money": 10.0,
                        "extra": {"kept": True},
                    }
                },
            }
        return {"ec": 200, "data": {"bill": {"id": 501}}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._request = MethodType(fake_request, client)

    result = asyncio.run(client.update_asset_balance(100, 20.5))

    assert result == {"ec": 200, "data": {"bill": {"id": 501}}}
    assert calls[0] == ("asset", "info", {"did": 100})
    assert calls[1][:2] == ("asset", "submit")
    assert set(calls[1][2]) == {"v"}
    submitted = json.loads(calls[1][2]["v"])
    assert submitted == {
        "id": 100,
        "name": "现金",
        "type": 1,
        "stype": 11,
        "money": 20.5,
        "extra": {"kept": True},
    }
    asyncio.run(client.aclose())


def test_update_asset_balance_with_adjustment_sends_diffparams(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, deepcopy(params or {})))
        if (ctrl, act) == ("asset", "info"):
            return {"ec": 200, "data": {"asset": {"id": 100, "name": "现金", "money": 10.0}}}
        return {"ec": 200, "data": {"bill": {"id": 502}}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._request = MethodType(fake_request, client)

    result = asyncio.run(
        client.update_asset_balance(
            100,
            15.0,
            generate_adjustment=True,
            category_id=3,
            bill_flag=1,
            remark="余额平账",
        )
    )

    assert result == {"ec": 200, "data": {"bill": {"id": 502}}}
    assert json.loads(calls[1][2]["v"]) == {"id": 100, "name": "现金", "money": 15.0}
    assert json.loads(calls[1][2]["diffparams"]) == {"cateid": 3, "billflag": 1, "remark": "余额平账"}
    asyncio.run(client.aclose())


def test_update_asset_balance_requires_category_for_adjustment(tmp_path):
    async def fake_request(self, ctrl, act, params=None):
        return {"ec": 200, "data": {"asset": {"id": 100, "money": 10.0}}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._request = MethodType(fake_request, client)

    with pytest.raises(ValueError, match="category_id"):
        asyncio.run(client.update_asset_balance(100, 15.0, generate_adjustment=True))

    asyncio.run(client.aclose())


def test_update_asset_balance_equal_balance_omits_diffparams(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, deepcopy(params or {})))
        if (ctrl, act) == ("asset", "info"):
            return {"ec": 200, "data": {"asset": {"id": 100, "money": 10.0}}}
        return {"ec": 200, "data": {"ok": True}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._request = MethodType(fake_request, client)

    asyncio.run(client.update_asset_balance(100, 10.0, generate_adjustment=True, category_id=3))

    assert set(calls[1][2]) == {"v"}
    assert json.loads(calls[1][2]["v"]) == {"id": 100, "money": 10.0}
    asyncio.run(client.aclose())


def test_business_apis_use_jadx_wire_shapes(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, deepcopy(params or {})))
        if (ctrl, act) == ("asset", "info"):
            return {
                "ec": 200,
                "data": {
                    "asset": {
                        "id": 701,
                        "name": "old",
                        "type": 5,
                        "stype": 52,
                        "money": 100.0,
                        "incount": 1,
                        "loan": {"money": 100.0, "startdate": "2026-01-01", "enddate": None, "totalpay": 0.0},
                    }
                },
            }
        return {"ec": 200, "data": {"ctrl": ctrl, "act": act, "params": params or {}}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._request = MethodType(fake_request, client)

    async def run():
        await client.create_refund(
            source_bill_id=101,
            amount=12.5,
            occurred_at=1_788_888_888,
            asset_id=201,
            remark="退款",
            tags=["tag-a"],
        )
        await client.update_refund(
            source_bill_id=101,
            refund_bill_id=301,
            amount=13.5,
            occurred_at=1_788_888_999,
        )
        await client.create_reimbursement(
            source_bill_ids=[401, 402],
            amount=88.0,
            occurred_at=1_788_889_000,
            asset_id=202,
            remark="报销",
            tags=["tag-b"],
        )
        await client.update_reimbursement(
            reimbursement_bill_id=501,
            source_bill_ids=[401],
            amount=66.0,
            occurred_at=1_788_889_111,
        )
        await client.create_loan_asset(
            name="借给张三",
            amount=100.0,
            startdate="2026-01-01",
            enddate="2026-12-31",
            linked_asset_id=201,
            remark="本金",
        )
        await client.update_loan_asset(asset_id=701, name="借给张三 updated", enddate="2027-01-01")

    asyncio.run(run())

    refund_create = calls[0]
    assert refund_create[:2] == ("bill", "refund2")
    assert refund_create[2]["did"] == 101
    assert json.loads(refund_create[2]["v"]) == {
        "money": 12.5,
        "time": 1_788_888_888,
        "assetid": 201,
        "remark": "退款",
        "tags": ["tag-a"],
    }

    refund_update = calls[1]
    assert json.loads(refund_update[2]["v"])["billid"] == 301

    reimbursement_create = calls[2]
    assert reimbursement_create[:2] == ("baoxiao", "baoxiao")
    assert reimbursement_create[2]["bxtime"] == 1_788_889_000
    assert reimbursement_create[2]["remark"] == "报销"
    assert reimbursement_create[2]["tags"] == json.dumps(["tag-b"], ensure_ascii=False)
    assert json.loads(reimbursement_create[2]["v"]) == {
        "billids": [401, 402],
        "money": 88.0,
        "assetid": 202,
    }

    reimbursement_update = calls[3]
    assert reimbursement_update[:2] == ("baoxiao", "baoxiao")
    assert reimbursement_update[2]["did"] == 501
    assert json.loads(reimbursement_update[2]["v"])["billids"] == [401]

    loan_create = calls[4]
    assert loan_create[:2] == ("asset", "submit")
    loan_asset = json.loads(loan_create[2]["v"])
    loan_extra = json.loads(loan_create[2]["type"])
    assert loan_asset["type"] == 5
    assert loan_asset["stype"] == 52
    assert loan_asset["money"] == 100.0
    assert loan_asset["loan"]["money"] == 100.0
    assert loan_extra == {"bookid": -1, "remark": "本金", "accountId": 201}

    loan_update_info = calls[5]
    loan_update = calls[6]
    assert loan_update_info[:2] == ("asset", "info")
    assert loan_update[:2] == ("asset", "submit")
    updated_asset = json.loads(loan_update[2]["v"])
    assert updated_asset["id"] == 701
    assert updated_asset["name"] == "借给张三 updated"
    assert updated_asset["loan"]["enddate"] == "2027-01-01"
    asyncio.run(client.aclose())


def test_category_and_asset_update_apis_use_jadx_wire_shapes(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, deepcopy(params or {})))
        if (ctrl, act) == ("asset", "info"):
            did = (params or {}).get("did")
            if did == 701:
                return {
                    "ec": 200,
                    "data": {
                        "asset": {
                            "id": 701,
                            "name": "借给张三",
                            "type": 5,
                            "stype": 52,
                            "money": 100.0,
                            "incount": 1,
                            "loan": {"money": 100.0, "startdate": "2026-01-01", "enddate": None, "totalpay": 0.0},
                        }
                    },
                }
            return {"ec": 200, "data": {"asset": {"id": 42, "name": "招行", "type": 1, "money": 100.0}}}
        return {"ec": 200, "data": {"ctrl": ctrl, "act": act}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
        )
    }
    client._current_account = "a"
    client._request = MethodType(fake_request, client)

    async def run():
        await client.category_submit(name="餐饮", icon="cate_food", cat_type=0, book_id=-1, parent_id=-1, cateid=-1)
        await client.category_submit(name="外卖", icon="", cat_type=0, book_id=-1, parent_id=101, cateid=102)
        await client.category_delete(cateid=102)
        await client.category_delete(cateid=102, force=True)
        await client.category_deletev2(did=102, deletebills=True, newcateid=103)
        await client.category_changelevel(cateid=102, parent_id=101)
        await client.category_changelevel(did=102)
        await client.category_movesubcate(cateid=102, parent_id=103)
        await client.update_asset(42, name="新招行", remark="工资卡")
        await client.update_asset(701, name="借给李四")

    asyncio.run(run())

    create = calls[0]
    assert create[:2] == ("category", "submitv2")
    assert create[2] == {"bookid": "-1", "v": "餐饮", "t": "0", "icon": "cate_food"}

    update = calls[1]
    assert update[:2] == ("category", "submitv2")
    assert update[2] == {"bookid": "-1", "v": "外卖", "t": "0", "did": "102", "parentid": "101"}

    delete_keep = calls[2]
    assert delete_keep[:2] == ("category", "deletev2")
    assert delete_keep[2] == {"did": "102", "deletebills": "0"}

    delete_force = calls[3]
    assert delete_force[2] == {"did": "102", "deletebills": "1"}

    delete_spec = calls[4]
    assert delete_spec[:2] == ("category", "deletev2")
    assert delete_spec[2] == {"did": 102, "deletebills": "1", "newcateid": 103}

    demote = calls[5]
    assert demote[:2] == ("category", "changelevel")
    assert demote[2] == {"did": 102, "parentid": 101}

    promote = calls[6]
    assert promote[:2] == ("category", "changelevel")
    assert promote[2] == {"did": 102}

    move = calls[7]
    assert move[:2] == ("category", "movesubcate")
    assert move[2] == {"did": 102, "parentid": 103}

    asset_info_call, asset_submit = calls[8], calls[9]
    assert asset_info_call[:2] == ("asset", "info")
    assert asset_submit[:2] == ("asset", "submit")
    assert "type" not in asset_submit[2]
    assert json.loads(asset_submit[2]["v"]) == {
        "id": 42,
        "name": "新招行",
        "type": 1,
        "money": 100.0,
        "remark": "工资卡",
    }

    loan_info_call, loan_submit = calls[10], calls[11]
    assert loan_info_call[:2] == ("asset", "info")
    assert loan_submit[:2] == ("asset", "submit")
    loan_v = json.loads(loan_submit[2]["v"])
    assert loan_v["id"] == 701
    assert loan_v["name"] == "借给李四"
    assert loan_v["loan"]["startdate"] == "2026-01-01"
    asyncio.run(client.aclose())


def test_mirrored_routes_are_registered_and_call_endpoint_methods():
    class FakeClient:
        def __init__(self):
            self.account_id = "a"
            self.default_account = "a"

        @property
        def current_account(self):
            return self.account_id

        async def login(self, email, password, account_id=None):
            self.default_account = account_id or email
            self.account_id = self.default_account
            return {"data": {"user": {"name": email, "email": email}}}

        async def login_md5(self, email, pwd_hash, account_id=None):
            return await self.login(email, pwd_hash, account_id=account_id)

        def list_accounts(self):
            return [
                {
                    "account_id": self.default_account,
                    "user_name": self.default_account,
                    "email": self.default_account,
                    "devname": "Device",
                    "is_current": True,
                }
            ]

        def use_account(self, account_id):
            class AccountContext:
                def __enter__(inner_self):
                    self.account_id = account_id or "a"

                def __exit__(inner_self, *args):
                    self.account_id = "a"

            return AccountContext()

        async def asset_info(self, **kwargs):
            return {"account_id": self.account_id, "kwargs": kwargs}

    app = FastAPI()
    fake = FakeClient()
    app.state.client = fake
    app.include_router(router)

    paths = {route.path for route in router.routes if "POST" in getattr(route, "methods", set())}
    assert {endpoint.path for endpoint in ENDPOINTS}.issubset(paths)

    with TestClient(app) as test_client:
        response = test_client.post("/asset/info", params={"account_id": "b"}, json={"did": 987}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"account_id": "b", "kwargs": {"did": 987}}
    assert fake.account_id == "a"


def test_mcp_registers_dedicated_tools_and_calls_endpoint_tool(tmp_path):
    async def fake_request(self, ctrl, act, params=None):
        return {"ec": 200, "data": {"ctrl": ctrl, "act": act, "params": params or {}, "account": self.current_account}}

    client = QianjiClient(db_path=tmp_path / "sessions.db")
    client._sessions = {
        "b": SessionData(
            account_id="b",
            email="b@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-b",
            devid="device-b",
            devname="Device B",
        )
    }
    client._current_account = "b"
    client._request = MethodType(fake_request, client)

    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("full")

    async def run():
        tools = await mcp_server.mcp.list_tools()
        names = {tool.name for tool in tools}
        assert {endpoint.tool_name for endpoint in ENDPOINTS}.issubset(names)

        asset_info = next(tool for tool in tools if tool.name == "asset_info")
        assert "did" in asset_info.inputSchema["properties"]
        assert "account_id" in asset_info.inputSchema["properties"]

        content, structured = await mcp_server.mcp.call_tool("asset_info", {"did": 321, "account_id": "b"})
        assert content
        payload = json.loads(structured["result"])
        assert payload["data"] == {
            "ctrl": "asset",
            "act": "info",
            "params": {"did": 321},
            "account": "b",
        }

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")
        asyncio.run(client.aclose())


def test_mcp_create_bill_schema_includes_historical_transfer_fields_in_full_mode_only():
    mcp_server.configure_mcp_tools("full")

    async def run():
        tools = await mcp_server.mcp.list_tools()
        create_bill = next(tool for tool in tools if tool.name == "create_bill")
        props = create_bill.inputSchema["properties"]

        assert "bill_time" in props
        assert "target_asset_id" in props
        assert "tags" in props
        assert "Unix seconds" in create_bill.description
        assert "type=2" in create_bill.description
        assert "Comma-separated tag names" in create_bill.description
        assert "low-level" in create_bill.description
        assert "full-mode" in create_bill.description

    try:
        asyncio.run(run())
    finally:
        mcp_server.configure_mcp_tools("write")


def test_mcp_full_mode_asset_submit_schema_includes_reconciliation_params():
    mcp_server.configure_mcp_tools("full")

    async def run():
        tools = await mcp_server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}

        assert len(tools) == len(mcp_server.get_mcp_tool_names_for_mode("full"))
        props = by_name["asset_submit"].inputSchema["properties"]
        assert {"v", "diffparams", "type", "account_id"}.issubset(props)

    try:
        asyncio.run(run())
    finally:
        mcp_server.configure_mcp_tools("write")


def test_mcp_bill_id_schemas_use_strings_without_changing_other_ids():
    def assert_string_schema(schema: dict):
        if "anyOf" in schema:
            assert {"type": "string"} in schema["anyOf"]
            return
        assert schema["type"] == "string"

    def assert_string_array_schema(schema: dict):
        assert schema["type"] == "array"
        assert schema["items"]["type"] == "string"

    mcp_server.configure_mcp_tools("full")

    async def run():
        tools = await mcp_server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}

        expected_string_fields = {
            "get_bill_by_id": ("bill_id",),
            "search_bills": ("bill_id",),
            "export_bills": ("bill_id",),
            "generate_report_image": ("bill_id",),
            "update_expense": ("bill_id",),
            "update_income": ("bill_id",),
            "update_transfer": ("bill_id",),
            "create_refund": ("source_bill_id",),
            "update_refund": ("source_bill_id", "refund_bill_id"),
            "update_reimbursement": ("reimbursement_bill_id",),
            "update_loan_principal": ("bill_id",),
            "update_debt_principal": ("bill_id",),
            "update_loan_collection": ("bill_id",),
            "update_debt_repayment": ("bill_id",),
            "delete_bill": ("bill_id",),
            "update_bill": ("bill_id",),
            "bill_refund2": ("did",),
            "saving_deposit": ("billid",),
            "installment_bindbill": ("billid",),
            "repeat_task_bindbill": ("billid",),
            "baoxiao_baoxiao": ("did",),
        }
        expected_string_array_fields = {
            "create_reimbursement": ("source_bill_ids",),
            "update_reimbursement": ("source_bill_ids",),
            "cancel_reimbursement": ("reimbursement_bill_ids",),
        }

        for tool_name, field_names in expected_string_fields.items():
            props = by_name[tool_name].inputSchema["properties"]
            for field_name in field_names:
                assert_string_schema(props[field_name])

        for tool_name, field_names in expected_string_array_fields.items():
            props = by_name[tool_name].inputSchema["properties"]
            for field_name in field_names:
                assert_string_array_schema(props[field_name])

        assert by_name["asset_info"].inputSchema["properties"]["did"]["type"] == "integer"
        assert by_name["book_members"].inputSchema["properties"]["bookid"]["type"] == "integer"

    try:
        asyncio.run(run())
    finally:
        mcp_server.configure_mcp_tools("write")


def test_mcp_write_schemas_expose_business_tools_without_low_level_type():
    mcp_server.configure_mcp_tools("write")

    async def run():
        tools = await mcp_server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}

        assert {
            "create_expense",
            "create_income",
            "create_transfer",
            "update_expense",
            "update_income",
            "update_transfer",
        }.issubset(by_name)
        assert {"update_bill", "delete_bill", "call_api"}.isdisjoint(by_name)
        for name in ("create_expense", "create_income"):
            props = by_name[name].inputSchema["properties"]
            expected_props = {
                "amount",
                "occurred_at",
                "asset_id",
                "category_id",
                "remark",
                "book_id",
                "account_id",
                "tags",
            }
            assert expected_props.issubset(props)
            assert "tag_ids" not in props
            assert "type" not in props
            assert "bill_time" not in props
            assert "target_asset_id" not in props

        transfer_props = by_name["create_transfer"].inputSchema["properties"]
        expected_transfer_props = {
            "amount",
            "occurred_at",
            "from_asset_id",
            "to_asset_id",
            "remark",
            "book_id",
            "account_id",
            "tags",
        }
        assert expected_transfer_props.issubset(transfer_props)
        assert "tag_ids" not in transfer_props
        assert "type" not in transfer_props
        assert "category_id" not in transfer_props
        assert "bill_time" not in transfer_props
        assert "target_asset_id" not in transfer_props

        for name in ("update_expense", "update_income"):
            props = by_name[name].inputSchema["properties"]
            expected_props = {
                "bill_id",
                "amount",
                "occurred_at",
                "asset_id",
                "category_id",
                "remark",
                "book_id",
                "account_id",
                "tags",
            }
            assert expected_props.issubset(props)
            assert "tag_ids" not in props
            assert "type" not in props
            assert "bill_time" not in props
            assert "target_asset_id" not in props

        update_transfer_props = by_name["update_transfer"].inputSchema["properties"]
        expected_update_transfer_props = {
            "bill_id",
            "amount",
            "occurred_at",
            "from_asset_id",
            "to_asset_id",
            "remark",
            "book_id",
            "account_id",
            "tags",
        }
        assert expected_update_transfer_props.issubset(update_transfer_props)
        assert "tag_ids" not in update_transfer_props
        assert "type" not in update_transfer_props
        assert "category_id" not in update_transfer_props
        assert "bill_time" not in update_transfer_props
        assert "target_asset_id" not in update_transfer_props

    try:
        asyncio.run(run())
    finally:
        mcp_server.configure_mcp_tools("write")


def test_mcp_modes_expose_tag_switch_tools():
    mcp_server.configure_mcp_tools("read")

    async def run_read():
        tools = await mcp_server.mcp.list_tools()
        return {tool.name for tool in tools}

    read_names = asyncio.run(run_read())
    assert "get_tags_enabled" in read_names
    assert "set_tags_enabled" not in read_names

    mcp_server.configure_mcp_tools("write")

    async def run_write():
        tools = await mcp_server.mcp.list_tools()
        return {tool.name for tool in tools}

    write_names = asyncio.run(run_write())
    assert "get_tags_enabled" in write_names
    assert "set_tags_enabled" in write_names

    mcp_server.configure_mcp_tools("write")


def test_mcp_modes_use_unified_bill_search_and_keep_legacy_queries_in_full():
    mcp_server.configure_mcp_tools("read")

    async def run_read():
        tools = await mcp_server.mcp.list_tools()
        return {tool.name for tool in tools}

    read_names = asyncio.run(run_read())
    assert {"search_bills", "export_bills", "generate_report_image", "get_budgets"}.issubset(read_names)
    assert {"list_recent_bills", "get_bills_range", "get_bill_by_id"}.isdisjoint(read_names)

    mcp_server.configure_mcp_tools("full")

    async def run_full():
        tools = await mcp_server.mcp.list_tools()
        return {tool.name for tool in tools}

    full_names = asyncio.run(run_full())
    assert {"search_bills", "list_recent_bills", "get_bills_range", "get_bill_by_id"}.issubset(full_names)
    mcp_server.configure_mcp_tools("advanced")


def test_mcp_write_and_advanced_modes_expose_phase_one_write_tools():
    mcp_server.configure_mcp_tools("write")

    async def run_write():
        tools = await mcp_server.mcp.list_tools()
        return {tool.name for tool in tools}

    write_names = asyncio.run(run_write())
    assert {
        "upload_bill_image",
        "create_tag",
        "update_tag",
        "delete_tag",
        "archive_tag",
        "create_tag_group",
        "update_tag_group",
        "delete_tag_group",
        "reorder_tags",
        "reorder_tag_groups",
    }.issubset(write_names)
    assert "cancel_reimbursement" not in write_names

    mcp_server.configure_mcp_tools("advanced")

    async def run_advanced():
        tools = await mcp_server.mcp.list_tools()
        return {tool.name for tool in tools}

    advanced_names = asyncio.run(run_advanced())
    assert "cancel_reimbursement" in advanced_names


def test_mcp_account_tools_report_cached_tags_enabled_without_refreshing_status():
    client = _McpAccountStatusClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        _, login_result = await mcp_server.mcp.call_tool(
            "login",
            {"email": "a@example.com", "password": "pwd", "account_id": "a"},
        )
        client.session.user_configs["tagon"] = 0
        _, status_result = await mcp_server.mcp.call_tool("get_login_status", {})
        _, list_result = await mcp_server.mcp.call_tool("list_accounts", {})
        return login_result, status_result, list_result

    try:
        login_result, status_result, list_result = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert login_result["tags_enabled"] is True
    assert status_result["logged_in"] is True
    assert status_result["tags_enabled"] is False
    assert list_result["accounts"][0]["tags_enabled"] is False
    assert client.refresh_calls == 0


def test_mcp_create_bill_forwards_historical_transfer_fields():
    client = _CaptureBillCreateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("full")

    async def run():
        content, structured = await mcp_server.mcp.call_tool(
            "create_bill",
            {
                "money": 12.5,
                "type": 2,
                "category_id": 7,
                "asset_id": 100,
                "target_asset_id": 200,
                "bill_time": 1712345678,
                "tags": "x, y",
                "remark": "historical transfer",
                "book_id": -1,
                "account_id": "a",
            },
        )
        assert content
        assert structured["bill_id"] == "9988"
        assert structured["ok"] is True

        await mcp_server.mcp.call_tool(
            "create_bill",
            {
                "money": 1.25,
                "type": 0,
                "category_id": 3,
                "asset_id": 4,
                "remark": "old behavior",
                "book_id": -1,
                "account_id": "a",
            },
        )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert client.calls[0] == {
        "account_id": "a",
        "money": 12.5,
        "bill_type": 2,
        "cateid": 7,
        "assetid": 100,
        "remark": "historical transfer",
        "book_id": -1,
        "bill_time": 1712345678,
        "target_asset_id": 200,
        "tags": ["x", "y"],
    }
    assert client.calls[1] == {
        "account_id": "a",
        "money": 1.25,
        "bill_type": 0,
        "cateid": 3,
        "assetid": 4,
        "remark": "old behavior",
        "book_id": -1,
        "bill_time": None,
        "target_asset_id": -1,
        "tags": None,
    }


def test_mcp_create_expense_validates_and_forwards_business_fields():
    client = _BusinessBillCreateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        content, structured = await mcp_server.mcp.call_tool(
            "create_expense",
            {
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 100,
                "category_id": 3,
                "tags": ["工作", "差旅"],
                "remark": "晚餐",
                "book_id": -1,
                "account_id": "a",
            },
        )
        assert content
        assert structured["ok"] is True
        assert structured["command"] == "create_expense"
        assert structured["resource"] == "bill"
        assert structured["ids"] == {"bill_id": "9988", "new_ids": ["9988"]}
        assert structured["data"] == {
            "bill_type": "expense",
            "amount": 12.5,
            "occurred_at": "2026-04-12T19:30:00+08:00",
            "book_id": -1,
            "asset_id": 100,
            "category_id": 3,
            "remark": "晚餐",
            "tags": ["工作", "差旅"],
            "tag_ids": ["10", "11"],
        }
        assert structured["raw_result"] == {"data": {"sync_result": {"bill": {"new_ids": [9988]}}}}

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert client.calls == [
        {
            "account_id": "a",
            "money": 12.5,
            "bill_type": 0,
            "cateid": 3,
            "assetid": 100,
            "remark": "晚餐",
            "book_id": -1,
            "bill_time": 1775993400,
            "target_asset_id": -1,
            "tags": ["10", "11"],
        }
    ]
    assert client.submitted_tags == []


def test_mcp_create_expense_creates_missing_tag_names():
    client = _BusinessBillCreateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        content, structured = await mcp_server.mcp.call_tool(
            "create_expense",
            {
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 100,
                "category_id": 3,
                "tags": ["工作", "新标签"],
                "account_id": "a",
            },
        )
        assert content
        return structured

    try:
        structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert structured["data"]["tags"] == ["工作", "新标签"]
    assert structured["data"]["tag_ids"] == ["10", "12"]
    assert client.submitted_tags == ["新标签"]
    assert client.calls[0]["tags"] == ["10", "12"]


def test_mcp_create_expense_rejects_tags_when_app_ui_switch_disabled():
    from mcp.server.fastmcp.exceptions import ToolError

    client = _BusinessBillCreateClient()
    client.session.user_configs["tagon"] = 0
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        return await mcp_server.mcp.call_tool(
            "create_expense",
            {
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 100,
                "category_id": 3,
                "tags": ["工作"],
                "account_id": "a",
            },
        )

    try:
        with pytest.raises(ToolError, match="set_tags_enabled"):
            asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert not client.calls


def test_mcp_get_tags_flattens_groups_and_skips_group_records():
    client = _BusinessBillCreateClient()

    async def tag_list(self):
        return {
            "data": {
                "list": [
                    {"id": "", "name": None, "tags": [{"id": "2606166a30efaea0beb4525", "name": "codex"}]},
                    {"id": "group-1", "name": "默认组", "tags": [{"tagid": "work-id", "name": "工作"}]},
                ]
            }
        }

    client.tag_list = MethodType(tag_list, client)
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        return await mcp_server.mcp.call_tool("get_tags", {"account_id": "a"})

    try:
        content, structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert content
    assert structured == {
        "items": [
            {"id": "2606166a30efaea0beb4525", "name": "codex"},
            {"id": "work-id", "name": "工作"},
        ]
    }


def test_mcp_create_income_resolves_nested_category_id():
    client = _BusinessBillCreateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        await mcp_server.mcp.call_tool(
            "create_income",
            {
                "amount": 1000.0,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 100,
                "category_id": 31,
                "account_id": "a",
            },
        )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert client.calls[0]["bill_type"] == 1
    assert client.calls[0]["cateid"] == 31


def test_mcp_create_expense_rejects_negative_amount():
    client = _BusinessBillCreateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        with pytest.raises(Exception, match="amount"):
            await mcp_server.mcp.call_tool(
                "create_expense",
                {
                    "amount": -1.0,
                    "occurred_at": "2026-04-12T19:30:00+08:00",
                    "asset_id": 100,
                    "category_id": 3,
                    "account_id": "a",
                },
            )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert client.calls == []


def test_mcp_create_expense_rejects_naive_occurred_at():
    client = _BusinessBillCreateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        with pytest.raises(Exception, match="timezone"):
            await mcp_server.mcp.call_tool(
                "create_expense",
                {
                    "amount": 1.0,
                    "occurred_at": "2026-04-12T19:30:00",
                    "asset_id": 100,
                    "category_id": 3,
                    "account_id": "a",
                },
            )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert client.calls == []


def test_mcp_create_transfer_rejects_same_asset():
    client = _BusinessBillCreateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        with pytest.raises(Exception, match="from_asset_id and to_asset_id"):
            await mcp_server.mcp.call_tool(
                "create_transfer",
                {
                    "amount": 1.0,
                    "occurred_at": "2026-04-12T19:30:00+08:00",
                    "from_asset_id": 100,
                    "to_asset_id": 100,
                    "account_id": "a",
                },
            )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert client.calls == []


def test_mcp_create_transfer_forwards_from_and_to_assets_without_category():
    client = _BusinessBillCreateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        content, structured = await mcp_server.mcp.call_tool(
            "create_transfer",
            {
                "amount": 88.0,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "from_asset_id": 100,
                "to_asset_id": 200,
                "remark": "转账",
                "account_id": "a",
            },
        )
        assert content
        assert structured["data"]["bill_type"] == "transfer"
        assert structured["data"]["from_asset_id"] == 100
        assert structured["data"]["to_asset_id"] == 200
        assert "category_id" not in structured["data"]

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")

    assert client.calls == [
        {
            "account_id": "a",
            "money": 88.0,
            "bill_type": 2,
            "cateid": 0,
            "assetid": 100,
            "remark": "转账",
            "book_id": -1,
            "bill_time": 1775993400,
            "target_asset_id": 200,
            "tags": None,
        }
    ]


@pytest.mark.parametrize(
    ("tool_name", "original_type"),
    [
        ("update_expense", 1),
        ("update_expense", 2),
        ("update_income", 0),
        ("update_income", 2),
        ("update_transfer", 0),
        ("update_transfer", 1),
    ],
)
def test_mcp_business_update_rejects_wrong_original_bill_type(tool_name, original_type):
    client = _BusinessBillUpdateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        await client._session_mgr.cache_upsert_bills("a", [_raw_mcp_bill(900, bill_type=original_type)])
        with pytest.raises(Exception, match="bill type"):
            await mcp_server.mcp.call_tool(
                tool_name,
                {
                    "bill_id": 900,
                    "amount": 2.0,
                    "account_id": "a",
                },
            )

    try:
        asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")
        asyncio.run(client.aclose())

    assert client.updated == []


def test_mcp_update_expense_validates_and_forwards_tags_by_name():
    client = _BusinessBillUpdateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        await client._session_mgr.cache_upsert_bills(
            "a",
            [
                _raw_mcp_bill(
                    901,
                    bill_type=0,
                    money=1.0,
                    asset_id=100,
                    category_id=3,
                    bill_time=1_700_000_000,
                    remark="old",
                    tag_ids=[11],
                )
            ],
        )
        content, structured = await mcp_server.mcp.call_tool(
            "update_expense",
            {
                "bill_id": 901,
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 200,
                "category_id": 3,
                "tags": ["工作", "新标签"],
                "remark": "晚餐",
                "book_id": -1,
                "account_id": "a",
            },
        )
        cached = await client._session_mgr.cache_get_bill("a", 901)
        return content, structured, cached

    try:
        content, structured, cached = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")
        asyncio.run(client.aclose())

    assert content
    assert structured["ok"] is True
    assert structured["command"] == "update_expense"
    assert structured["resource"] == "bill"
    assert structured["ids"] == {"bill_id": "901", "update_ids": ["901"]}
    assert structured["data"] == {
        "bill_type": "expense",
        "amount": 12.5,
        "occurred_at": "2026-04-12T19:30:00+08:00",
        "asset_id": 200,
        "category_id": 3,
        "remark": "晚餐",
        "tags": ["工作", "新标签"],
        "tag_ids": ["10", "12"],
    }
    assert client.submitted_tags == ["新标签"]
    assert client.updated[-1]["id"] == 901
    assert client.updated[-1]["userid"] == "user-a"
    assert client.updated[-1]["money"] == 12.5
    assert client.updated[-1]["time"] == 1775993400
    assert client.updated[-1]["assetid"] == 200
    assert client.updated[-1]["cateid"] == 3
    assert client.updated[-1]["remark"] == "晚餐"
    assert client.updated[-1]["extra"]["tags"] == ["10", "12"]
    assert cached["money"] == 12.5
    assert cached["extra"]["tags"] == ["10", "12"]


def test_mcp_update_transfer_forwards_from_and_to_assets_without_category():
    client = _BusinessBillUpdateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        await client._session_mgr.cache_upsert_bills(
            "a",
            [_raw_mcp_bill(902, bill_type=2, asset_id=100, target_asset_id=200, category_id=0)],
        )
        content, structured = await mcp_server.mcp.call_tool(
            "update_transfer",
            {
                "bill_id": 902,
                "from_asset_id": 200,
                "to_asset_id": 100,
                "account_id": "a",
            },
        )
        return content, structured

    try:
        content, structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")
        asyncio.run(client.aclose())

    assert content
    assert structured["data"]["bill_type"] == "transfer"
    assert structured["data"]["from_asset_id"] == 200
    assert structured["data"]["to_asset_id"] == 100
    assert "category_id" not in structured["data"]
    assert client.updated[-1]["assetid"] == 200
    assert client.updated[-1]["fromid"] == 200
    assert client.updated[-1]["targetid"] == 100


def test_mcp_update_expense_rejects_tags_when_app_ui_switch_disabled():
    from mcp.server.fastmcp.exceptions import ToolError

    client = _BusinessBillUpdateClient()
    client.session.user_configs["tagon"] = 0
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        await client._session_mgr.cache_upsert_bills("a", [_raw_mcp_bill(903, bill_type=0)])
        return await mcp_server.mcp.call_tool(
            "update_expense",
            {
                "bill_id": 903,
                "tags": ["工作"],
                "account_id": "a",
            },
        )

    try:
        with pytest.raises(ToolError, match="set_tags_enabled"):
            asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")
        asyncio.run(client.aclose())

    assert not client.updated


def test_mcp_business_update_without_fields_returns_no_update_fields():
    client = _BusinessBillUpdateClient()
    old_client = mcp_server._client
    mcp_server._client = client
    mcp_server.configure_mcp_tools("write")

    async def run():
        await client._session_mgr.cache_upsert_bills("a", [_raw_mcp_bill(904, bill_type=0)])
        content, structured = await mcp_server.mcp.call_tool(
            "update_expense",
            {
                "bill_id": 904,
                "account_id": "a",
            },
        )
        return content, structured

    try:
        content, structured = asyncio.run(run())
    finally:
        mcp_server._client = old_client
        mcp_server.configure_mcp_tools("write")
        asyncio.run(client.aclose())

    assert content
    assert structured == {
        "ok": False,
        "command": "update_expense",
        "resource": "bill",
        "error_code": "no_update_fields",
        "message": "At least one update field is required",
        "ids": {"bill_id": "904"},
        "raw_result": {},
    }
    assert client.updated == []
