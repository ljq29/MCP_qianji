from __future__ import annotations

import asyncio
import json
import time
from io import BytesIO
from types import MethodType

import httpx
import pytest
from PIL import Image

from qianji.client import QianjiClient
from qianji.errors import AuthenticationError, QianjiError, UpstreamTimeoutError
from qianji.image_upload import BillImageValidationError
from qianji.session import SessionData, SessionManager
from qianji.sign import hash_password


def _session(account_id: str, token: str) -> SessionData:
    return SessionData(
        account_id=account_id,
        email=f"{account_id}@example.com",
        pwd_hash=f"pwd-{account_id}",
        utoken=token,
        user_id=f"user-{account_id}",
        user_name=account_id,
        devid=f"device-{account_id}",
        devname=f"Device {account_id}",
    )


def test_session_round_trips_user_configs(tmp_path):
    async def run():
        mgr = SessionManager(tmp_path / "sessions.db")
        await mgr.save(
            SessionData(
                account_id="a",
                email="a@example.com",
                pwd_hash="pwd",
                utoken="token",
                user_id="user-a",
                user_name="Alice",
                devid="device-a",
                devname="Device A",
                user_configs={"tagon": 1, "basecur": "CNY"},
            )
        )
        loaded = await mgr.load("a")
        await mgr.close()
        return loaded

    loaded = asyncio.run(run())

    assert loaded is not None
    assert loaded.user_configs == {"tagon": 1, "basecur": "CNY"}


def test_login_persists_userconfigs(tmp_path):
    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        assert (ctrl, act) == ("account", "login")
        return {
            "ec": 200,
            "data": {
                "token": "token-a",
                "user": {"id": "user-a", "name": "Alice", "email": "a@example.com"},
                "books": [],
                "userconfigs": {"tagon": 1},
            },
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        c._request_raw = MethodType(fake_raw, c)
        await c.login("a@example.com", "password", account_id="a")
        loaded = await c._session_mgr.load("a")
        await c.aclose()
        return loaded

    loaded = asyncio.run(run())

    assert loaded is not None
    assert loaded.user_configs == {"tagon": 1}


def test_login_md5_reuses_existing_session_when_password_is_stored_hash(tmp_path):
    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        raise AssertionError("stored pwd_hash should reuse the existing session")

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        stored = SessionData(
            account_id="a@example.com",
            email="a@example.com",
            pwd_hash="0123456789abcdef0123456789abcdef",
            utoken="token-a",
            user_id="user-a",
            user_name="Alice",
            devid="device-a",
            devname="Device A",
        )
        await c._session_mgr.save(stored)
        c._request_raw = MethodType(fake_raw, c)
        try:
            return await c.login_md5("a@example.com", stored.pwd_hash, account_id="a@example.com")
        finally:
            await c.aclose()

    result = asyncio.run(run())

    assert result["em"] == "Session reused (credentials match)"
    assert result["data"]["token"] == "token-a"


def test_login_md5_sends_provided_hash_without_rehashing_new_session(tmp_path):
    pwd_hash = "0123456789abcdef0123456789abcdef"
    seen_params: list[dict] = []

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        assert (ctrl, act) == ("account", "login")
        seen_params.append(params or {})
        return {
            "ec": 200,
            "data": {
                "token": "token-a",
                "user": {"id": "user-a", "name": "Alice", "email": "a@example.com"},
            },
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        c._request_raw = MethodType(fake_raw, c)
        try:
            await c.login_md5("a@example.com", pwd_hash, account_id="a")
            loaded = await c._session_mgr.load("a")
            return loaded
        finally:
            await c.aclose()

    loaded = asyncio.run(run())

    assert seen_params == [{"v": "a@example.com", "pwd": pwd_hash}]
    assert loaded is not None
    assert loaded.pwd_hash == pwd_hash
    assert seen_params[0]["pwd"] != hash_password(pwd_hash)


def test_login_treats_32_hex_password_as_plaintext(tmp_path):
    password = "0123456789abcdef0123456789abcdef"
    seen_params: list[dict] = []

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        assert (ctrl, act) == ("account", "login")
        seen_params.append(params or {})
        return {
            "ec": 200,
            "data": {
                "token": "token-a",
                "user": {"id": "user-a", "name": "Alice", "email": "a@example.com"},
            },
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        c._request_raw = MethodType(fake_raw, c)
        try:
            await c.login("a@example.com", password, account_id="a")
            loaded = await c._session_mgr.load("a")
            return loaded
        finally:
            await c.aclose()

    loaded = asyncio.run(run())

    assert seen_params == [{"v": "a@example.com", "pwd": hash_password(password)}]
    assert loaded is not None
    assert loaded.pwd_hash == hash_password(password)


def test_login_reuses_existing_email_device_when_password_changes(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        calls.append((devid, devname, params))
        assert (ctrl, act) == ("account", "login")
        return {
            "ec": 200,
            "data": {
                "token": "token-new",
                "user": {"id": "user-a", "name": "Alice", "email": "a@example.com"},
                "books": [],
            },
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        await c._session_mgr.save(
            SessionData(
                account_id="a@example.com",
                email="a@example.com",
                pwd_hash=hash_password("old-password"),
                utoken="",
                user_id="user-a",
                user_name="Alice",
                devid="device-existing",
                devname="Device Existing",
            )
        )
        c._request_raw = MethodType(fake_raw, c)
        try:
            result = await c.login("a@example.com", "new-password")
            loaded = await c._session_mgr.load("a@example.com")
            duplicate = await c._session_mgr.load("a@example.com#2")
            return result, loaded, duplicate
        finally:
            await c.aclose()

    result, loaded, duplicate = asyncio.run(run())

    assert result["data"]["token"] == "token-new"
    assert loaded is not None
    assert loaded.account_id == "a@example.com"
    assert loaded.devid == "device-existing"
    assert loaded.devname == "Device Existing"
    assert loaded.pwd_hash == hash_password("new-password")
    assert duplicate is None
    assert calls == [("device-existing", "Device Existing", {"v": "a@example.com", "pwd": loaded.pwd_hash})]


def test_login_reuses_email_device_without_overriding_explicit_account_id(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        calls.append((devid, devname, params))
        assert (ctrl, act) == ("account", "login")
        return {
            "ec": 200,
            "data": {
                "token": "token-new",
                "user": {"id": "user-a", "name": "Alice", "email": "a@example.com"},
                "books": [],
            },
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        await c._session_mgr.save(
            SessionData(
                account_id="old-alias",
                email="a@example.com",
                pwd_hash=hash_password("old-password"),
                utoken="",
                user_id="user-a",
                user_name="Alice",
                devid="device-existing",
                devname="Device Existing",
            )
        )
        c._request_raw = MethodType(fake_raw, c)
        try:
            await c.login("a@example.com", "new-password", account_id="new-alias")
            loaded = await c._session_mgr.load("new-alias")
            old = await c._session_mgr.load("old-alias")
            return c.current_account, loaded, old
        finally:
            await c.aclose()

    current_account, loaded, old = asyncio.run(run())

    assert current_account == "new-alias"
    assert loaded is not None
    assert loaded.account_id == "new-alias"
    assert loaded.devid == "device-existing"
    assert loaded.devname == "Device Existing"
    assert loaded.pwd_hash == hash_password("new-password")
    assert old is not None
    assert old.pwd_hash == hash_password("old-password")
    assert calls == [("device-existing", "Device Existing", {"v": "a@example.com", "pwd": loaded.pwd_hash})]


def test_concurrent_first_login_uses_one_upstream_login_and_one_device(tmp_path):
    calls: list[tuple[str, str]] = []

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        assert (ctrl, act) == ("account", "login")
        calls.append((devid, devname))
        await asyncio.sleep(0.05)
        return {
            "ec": 200,
            "data": {
                "token": "token-a",
                "user": {"id": "user-a", "name": "Alice", "email": "a@example.com"},
                "books": [],
            },
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        c._request_raw = MethodType(fake_raw, c)
        try:
            results = await asyncio.gather(
                *(c.login("a@example.com", "password", account_id="a@example.com") for _ in range(5))
            )
            loaded = await c._session_mgr.load("a@example.com")
            return results, loaded
        finally:
            await c.aclose()

    results, loaded = asyncio.run(run())

    assert len(calls) == 1
    assert loaded is not None
    assert loaded.devid == calls[0][0]
    assert {result["data"]["token"] for result in results} == {"token-a"}


def test_login_md5_rejects_invalid_hash(tmp_path):
    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        try:
            with pytest.raises(ValueError, match="32-character MD5"):
                await c.login_md5("a@example.com", "not-md5", account_id="a")
        finally:
            await c.aclose()

    asyncio.run(run())


def test_refresh_user_configs_updates_session_from_client_init_response(tmp_path):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        calls.append((ctrl, act, params))
        assert (ctrl, act) == ("client", "init")
        assert utoken == "token-a-old"
        assert devid == "device-a"
        assert devname == "Device A"
        assert user_id == "user-a-old"
        return {
            "ec": 200,
            "data": {
                "userinfo": {"id": "user-a-new", "name": "Alice New", "email": "a@example.com"},
                "books": [{"bookid": -1, "name": "日常"}],
                "userconfigs": {"tagon": 1, "basecur": "CNY"},
            },
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        c._sessions = {
            "a": SessionData(
                account_id="a",
                email="a@example.com",
                pwd_hash="pwd-hash",
                utoken="token-a-old",
                user_id="user-a-old",
                user_name="Alice Old",
                devid="device-a",
                devname="Device A",
                user_configs={"tagon": 0},
            )
        }
        c._current_account = "a"
        c._request_raw = MethodType(fake_raw, c)
        refreshed = await c.refresh_user_configs()
        loaded = await c._session_mgr.load("a")
        await c.aclose()
        return refreshed, loaded

    refreshed, loaded = asyncio.run(run())

    assert calls == [("client", "init", {"v": 0, "vvmark": 0, "newinstall": 0, "upgradealert": 0})]
    assert refreshed == {"tagon": 1, "basecur": "CNY"}
    assert loaded is not None
    assert loaded.utoken == "token-a-old"
    assert loaded.user_id == "user-a-new"
    assert loaded.user_name == "Alice New"
    assert loaded.books == [{"bookid": -1, "name": "日常"}]
    assert loaded.user_configs == {"tagon": 1, "basecur": "CNY"}


def test_refresh_user_configs_does_not_clear_cache_when_client_init_omits_userconfigs(tmp_path):
    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        assert (ctrl, act) == ("client", "init")
        return {"ec": 200, "data": {"configs": {"version": 1}}}

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        c._sessions = {
            "a": SessionData(
                account_id="a",
                email="a@example.com",
                pwd_hash="pwd-hash",
                utoken="token-a",
                user_id="user-a",
                user_name="Alice",
                devid="device-a",
                devname="Device A",
                user_configs={"tagon": 1},
            )
        }
        c._current_account = "a"
        c._request_raw = MethodType(fake_raw, c)
        refreshed = await c.refresh_user_configs()
        loaded = await c._session_mgr.load("a")
        await c.aclose()
        return refreshed, loaded

    refreshed, loaded = asyncio.run(run())

    assert refreshed == {"tagon": 1}
    assert loaded is not None
    assert loaded.user_configs == {"tagon": 1}


@pytest.fixture
def client(tmp_path):
    c = QianjiClient(db_path=tmp_path / "sessions.db")
    sessions = {
        "a": _session("a", "token-a"),
        "b": _session("b", "token-b"),
    }
    c._sessions = sessions
    c._current_account = "a"

    async def _save_all():
        for session in sessions.values():
            await c._session_mgr.save(session)

    asyncio.run(_save_all())
    yield c
    asyncio.run(c.aclose())


def test_list_accounts_includes_cached_tags_enabled(client):
    client._sessions["a"].user_configs["tagon"] = 1
    client._sessions["b"].user_configs["tagon"] = 0

    accounts = client.list_accounts()

    by_id = {account["account_id"]: account for account in accounts}
    assert by_id["a"]["tags_enabled"] is True
    assert by_id["b"]["tags_enabled"] is False


def test_aclose_closes_http_and_sqlite(tmp_path):
    async def run():
        c = QianjiClient(db_path=tmp_path / "close.db")
        async with c:
            assert c._http is not None
            assert c._session_mgr._conn is not None
        assert c._http is None
        assert c._session_mgr._conn is None
        await c.aclose()

    asyncio.run(run())


def test_tag_management_facade_uses_semantic_methods_and_app_field_shape(client):
    calls = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, params or {}))
        return {"ec": 200, "data": {"ctrl": ctrl, "act": act, "params": params or {}}}

    client._request = MethodType(fake_request, client)

    async def run():
        await client.tag_submit(name="差旅", group_id="g1", color="#167bff")
        await client.tag_submit(name="出差", tag_id="t1")
        await client.tag_delete(tag_id="t1")
        await client.tag_archive(tag_id="t2")
        await client.tag_submit_group(name="项目")
        await client.tag_submit_group(name="项目A", group_id="g1")
        await client.tag_delete_group(group_id="g1")
        await client.tag_reorder_tag(tag_ids=["t2", "t1"])
        await client.tag_reorder_group(group_ids=["g2", "g1"])

    asyncio.run(run())

    assert calls[0] == (
        "tag",
        "submittag",
        {"v": json.dumps({"groupid": "g1", "name": "差旅", "color": "#167bff"}, ensure_ascii=False)},
    )
    assert calls[1] == (
        "tag",
        "submittag",
        {"v": json.dumps({"groupid": "", "name": "出差"}, ensure_ascii=False), "did": "t1"},
    )
    assert calls[2] == ("tag", "deletetag", {"did": "t1"})
    assert calls[3] == ("tag", "archivetag", {"did": "t2"})
    assert calls[4] == ("tag", "submitgroup", {"v": json.dumps({"name": "项目"}, ensure_ascii=False)})
    assert calls[5] == ("tag", "submitgroup", {"v": json.dumps({"name": "项目A"}, ensure_ascii=False), "did": "g1"})
    assert calls[6] == ("tag", "deletegroup", {"did": "g1"})
    assert calls[7] == ("tag", "reordertag", {"v": json.dumps(["t2", "t1"], ensure_ascii=False, separators=(",", ":"))})
    assert calls[8] == (
        "tag",
        "reordergroup",
        {"v": json.dumps(["g2", "g1"], ensure_ascii=False, separators=(",", ":"))},
    )


def test_bill_image_upload_fetches_dt_1_token_and_posts_to_qiniu(client, monkeypatch):
    calls = []
    source = BytesIO()
    Image.new("RGB", (8, 6), (20, 80, 160)).save(source, format="PNG")
    source_bytes = source.getvalue()

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, params or {}))
        return {"ec": 200, "data": {"v": "upload-token", "expireAt": 1_800_000_000}}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"key": "remote-key"}

    class FakeHTTP:
        async def post(self, url, data=None, files=None):
            calls.append(("http", "post", {"url": url, "data": data, "files": files}))
            return FakeResponse()

        async def aclose(self):
            return None

    client._request = MethodType(fake_request, client)
    client._http = FakeHTTP()
    monkeypatch.setenv("QIANJI_QINIU_UPLOAD_URL", "https://upload.example")

    async def run():
        return await client.upload_bill_image(filename="bill.png", content=source_bytes, mime_type="image/png")

    result = asyncio.run(run())

    assert calls[0] == ("common", "uploadtoken", {"dt": 1})
    upload = calls[1][2]
    assert upload["url"] == "https://upload.example"
    assert upload["data"]["token"] == "upload-token"
    assert upload["data"]["key"].endswith(".jpg")
    filename, uploaded_bytes, content_type = upload["files"]["file"]
    assert filename == "bill.jpg"
    assert content_type == "image/jpeg"
    with Image.open(BytesIO(uploaded_bytes)) as uploaded:
        assert uploaded.format == "JPEG"
        assert uploaded.size == (8, 6)
    assert result["ok"] is True
    assert result["image_key"] == upload["data"]["key"]
    assert result["remote_key"] == "remote-key"
    assert result["token_expires_at"] == 1_800_000_000
    assert result["original"]["format"] == "PNG"
    assert result["uploaded"]["format"] == "JPEG"


def test_bill_image_upload_rejects_invalid_image_before_fetching_token(client):
    calls = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, params or {}))
        return {"ec": 200, "data": {"v": "upload-token", "expireAt": 1_800_000_000}}

    client._request = MethodType(fake_request, client)

    async def run():
        with pytest.raises(BillImageValidationError) as exc:
            await client.upload_bill_image(filename="bill.jpg", content=b"not-image", mime_type="image/jpeg")
        return exc.value

    err = asyncio.run(run())

    assert err.code == "invalid_image"
    assert calls == []


def test_bill_image_upload_maps_timeout_to_upstream_timeout(client, monkeypatch):
    source = BytesIO()
    Image.new("RGB", (8, 6), (20, 80, 160)).save(source, format="PNG")

    async def fake_request(self, ctrl, act, params=None):
        return {"ec": 200, "data": {"v": "upload-token", "expireAt": 1_800_000_000}}

    class TimeoutHTTP:
        async def post(self, url, data=None, files=None):
            raise httpx.TimeoutException("slow upstream")

        async def aclose(self):
            return None

    client._request = MethodType(fake_request, client)
    client._http = TimeoutHTTP()
    monkeypatch.setenv("QIANJI_QINIU_UPLOAD_URL", "https://upload.example")

    async def run():
        with pytest.raises(UpstreamTimeoutError):
            await client.upload_bill_image(filename="bill.png", content=source.getvalue(), mime_type="image/png")

    asyncio.run(run())


def test_special_bill_image_keys_are_validated_before_upstream_requests(client):
    calls = []

    async def fake_request(self, ctrl, act, params=None):
        calls.append((ctrl, act, params or {}))
        raise AssertionError("invalid image keys must be rejected before upstream requests")

    client._request = MethodType(fake_request, client)

    async def assert_rejected(coro):
        with pytest.raises(ValueError):
            await coro
        assert calls == []

    async def run():
        await assert_rejected(
            client.create_refund(
                source_bill_id=101,
                amount=12.5,
                occurred_at=1_788_888_888,
                images=["https://example.com/receipt.jpg"],
            )
        )
        await assert_rejected(
            client.update_reimbursement(
                reimbursement_bill_id=501,
                source_bill_ids=[401],
                amount=66.0,
                occurred_at=1_788_889_111,
                images=["dir/receipt.jpg"],
            )
        )
        await assert_rejected(
            client.create_loan_asset(
                name="借给张三",
                amount=100.0,
                startdate="2026-01-01",
                images=["data:image/png;base64,aaa"],
            )
        )
        await assert_rejected(
            client.update_loan_asset(asset_id=701, images=[f"_{i}.jpg" for i in range(7)])
        )
        await assert_rejected(
            client.record_loan_collection(
                loan_asset_id=701,
                amount=40.0,
                occurred_at=1_788_889_222,
                images=[r"dir\receipt.jpg"],
            )
        )
        await assert_rejected(
            client.update_debt_repayment(
                bill_id=1001,
                amount=51.0,
                occurred_at=1_788_889_333,
                images=["aW1hZ2U="],
            )
        )

    asyncio.run(run())


def test_use_account_is_context_local_for_concurrent_requests(client):
    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        await asyncio.sleep(0.01 if utoken == "token-a" else 0)
        return {
            "ec": 200,
            "data": {
                "current_account": self.current_account,
                "utoken": utoken,
                "user_id": user_id,
            },
        }

    client._request_raw = MethodType(fake_raw, client)

    async def call(account_id: str):
        with client.use_account(account_id):
            before = client.current_account
            result = await client.call("asset", "list")
            after = client.current_account
        return before, result["data"], after

    async def run():
        return await asyncio.gather(call("a"), call("b"))

    result_a, result_b = asyncio.run(run())

    assert result_a == ("a", {"current_account": "a", "utoken": "token-a", "user_id": "user-a"}, "a")
    assert result_b == ("b", {"current_account": "b", "utoken": "token-b", "user_id": "user-b"}, "b")
    assert client.current_account == "a"


def test_use_account_rejects_unknown_account(client):
    with pytest.raises(QianjiError):
        with client.use_account("missing"):
            pass


def test_auth_recovery_updates_the_context_account(client):
    asset_calls = 0

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        nonlocal asset_calls
        if ctrl == "asset" and act == "list":
            asset_calls += 1
            if asset_calls == 1:
                raise AuthenticationError(8888, "expired")
            return {"ec": 200, "data": {"utoken": utoken, "user_id": user_id}}
        if ctrl == "account" and act == "refreshtoken":
            assert user_id == "user-b"
            return {"ec": 200, "data": {"token": "token-b-new"}}
        raise AssertionError(f"unexpected call: {ctrl}/{act}")

    client._request_raw = MethodType(fake_raw, client)

    async def run():
        with client.use_account("b"):
            return await client.call("asset", "list")

    result = asyncio.run(run())

    assert result["data"] == {"utoken": "token-b-new", "user_id": "user-b"}
    assert client._sessions["a"].utoken == "token-a"
    assert client._sessions["b"].utoken == "token-b-new"
    assert client.current_account == "a"


def test_bill_create_generates_unique_local_ids(client):
    bill_ids: list[int] = []

    async def fake_request(self, ctrl, act, params=None):
        payload = json.loads(params["v"])
        bills = json.loads(payload["bills"]["changelist"])
        bill_ids.append(bills[0]["id"])
        return {"ec": 200, "data": {}}

    client._request = MethodType(fake_request, client)

    async def run():
        for _ in range(100):
            await client.bill_create(money=1.0, cateid=1)

    asyncio.run(run())

    assert len(bill_ids) == 100
    assert len(set(bill_ids)) == 100


def test_bill_create_builds_historical_transfer_bill(client):
    created_bills: list[dict] = []

    async def fake_request(self, ctrl, act, params=None):
        assert (ctrl, act) == ("bill", "syncall")
        payload = json.loads(params["v"])
        created_bills.extend(json.loads(payload["bills"]["changelist"]))
        return {"ec": 200, "data": {}}

    client._request = MethodType(fake_request, client)

    async def run():
        await client.bill_create(
            money=12.5,
            bill_type=2,
            cateid=7,
            assetid=100,
            target_asset_id=200,
            bill_time=0,
            tags=["x", "y"],
            images=["_receipt.jpg"],
            remark="historical transfer",
            book_id=-1,
        )

    asyncio.run(run())

    bill = created_bills[0]
    assert bill["type"] == 2
    assert bill["money"] == 12.5
    assert bill["cateid"] == 7
    assert bill["assetid"] == 100
    assert bill["fromid"] == 100
    assert bill["targetid"] == 200
    assert bill["time"] == 0
    assert bill["remark"] == "historical transfer"
    assert bill["extra"]["tags"] == ["x", "y"]
    assert bill["images"] == ["_receipt.jpg"]


def test_bill_ids_are_unique_across_bill_and_debtloan_modules(client):
    """Regular bills and debt/loan bills share one ID counter: no collisions
    even when both are created within the same millisecond."""
    bill_ids: list[int] = []

    async def fake_request(self, ctrl, act, params=None):
        payload = json.loads(params["v"])
        bill_ids.extend(b["id"] for b in json.loads(payload["bills"]["changelist"]))
        return {"ec": 200, "data": {}}

    client._request = MethodType(fake_request, client)

    async def run():
        for _ in range(50):
            await client.bill_create(money=1.0, cateid=1)
            await client.add_loan_principal(loan_asset_id=701, amount=2.0, occurred_at=1_788_889_000)

    asyncio.run(run())

    assert len(bill_ids) == 100
    assert len(set(bill_ids)) == 100
    assert all(isinstance(bill_id, int) for bill_id in bill_ids)


def test_login_lock_is_keyed_by_email_not_account_id(client):
    assert client._login_lock_for("a@example.com", "acc-1") is client._login_lock_for("a@example.com", "acc-2")
    assert client._login_lock_for("a@example.com", None) is client._login_lock_for("a@example.com", "acc-1")
    assert client._login_lock_for("b@example.com", "acc-1") is not client._login_lock_for("a@example.com", "acc-1")


def test_login_lock_entry_removed_after_login(tmp_path):
    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        return {
            "ec": 200,
            "data": {"token": "token-a", "user": {"id": "user-a", "name": "Alice", "email": "a@example.com"}},
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        c._request_raw = MethodType(fake_raw, c)
        try:
            await c.login("a@example.com", "password", account_id="a")
            return dict(c._login_locks)
        finally:
            await c.aclose()

    assert asyncio.run(run()) == {}


def test_login_reuses_session_within_max_age(tmp_path, monkeypatch):
    monkeypatch.setenv("QIANJI_SESSION_MAX_AGE_DAYS", "30")

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        raise AssertionError("fresh session should be reused without an upstream login")

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        await c._session_mgr.save(
            SessionData(
                account_id="a",
                email="a@example.com",
                pwd_hash="0123456789abcdef0123456789abcdef",
                utoken="token-a",
                user_id="user-a",
                user_name="Alice",
                devid="device-a",
                devname="Device A",
                login_time=time.time() - 10 * 86400,
            )
        )
        c._request_raw = MethodType(fake_raw, c)
        try:
            return await c.login_md5("a@example.com", "0123456789abcdef0123456789abcdef")
        finally:
            await c.aclose()

    result = asyncio.run(run())
    assert result["em"] == "Session reused (credentials match)"
    assert result["data"]["token"] == "token-a"


def test_login_forces_upstream_login_when_session_older_than_max_age(tmp_path, monkeypatch):
    monkeypatch.setenv("QIANJI_SESSION_MAX_AGE_DAYS", "30")
    login_calls = 0

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        nonlocal login_calls
        login_calls += 1
        assert (ctrl, act) == ("account", "login")
        return {
            "ec": 200,
            "data": {"token": "token-new", "user": {"id": "user-a", "name": "Alice", "email": "a@example.com"}},
        }

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        await c._session_mgr.save(
            SessionData(
                account_id="a",
                email="a@example.com",
                pwd_hash="0123456789abcdef0123456789abcdef",
                utoken="token-old",
                user_id="user-a",
                user_name="Alice",
                devid="device-a",
                devname="Device A",
                login_time=time.time() - 40 * 86400,
            )
        )
        c._request_raw = MethodType(fake_raw, c)
        try:
            result = await c.login_md5("a@example.com", "0123456789abcdef0123456789abcdef")
            loaded = await c._session_mgr.load("a")
            return result, loaded
        finally:
            await c.aclose()

    result, loaded = asyncio.run(run())
    assert login_calls == 1
    assert result["data"]["token"] == "token-new"
    assert loaded is not None
    assert loaded.devid == "device-a"


def test_login_session_max_age_zero_disables_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("QIANJI_SESSION_MAX_AGE_DAYS", "0")

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        raise AssertionError("max age 0 disables expiry; session must be reused")

    async def run():
        c = QianjiClient(db_path=tmp_path / "sessions.db")
        await c._session_mgr.save(
            SessionData(
                account_id="a",
                email="a@example.com",
                pwd_hash="0123456789abcdef0123456789abcdef",
                utoken="token-old",
                user_id="user-a",
                user_name="Alice",
                devid="device-a",
                devname="Device A",
                login_time=time.time() - 400 * 86400,
            )
        )
        c._request_raw = MethodType(fake_raw, c)
        try:
            return await c.login_md5("a@example.com", "0123456789abcdef0123456789abcdef")
        finally:
            await c.aclose()

    result = asyncio.run(run())
    assert result["em"] == "Session reused (credentials match)"
    assert result["data"]["token"] == "token-old"


def test_auth_recovery_does_not_resurrect_logged_out_account(client):
    refresh_calls = 0

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        nonlocal refresh_calls
        if ctrl == "asset" and act == "list":
            # A concurrent logout lands while this request is in flight.
            client._sessions.pop("a", None)
            raise AuthenticationError(8888, "expired")
        if (ctrl, act) == ("account", "refreshtoken"):
            refresh_calls += 1
            return {"ec": 200, "data": {"token": "token-a-new"}}
        raise AssertionError(f"unexpected call: {ctrl}/{act}")

    client._request_raw = MethodType(fake_raw, client)

    async def run():
        with pytest.raises(AuthenticationError):
            await client.call("asset", "list")

    asyncio.run(run())

    assert refresh_calls == 0
    assert "a" not in client._sessions


def test_concurrent_auth_recovery_is_serialized(client):
    refresh_calls = 0
    relogin_calls = 0

    async def fake_raw(self, ctrl, act, params=None, *, utoken, devid, devname, user_id):
        nonlocal refresh_calls, relogin_calls
        if ctrl == "asset" and act == "list":
            if utoken != "token-a-new":
                raise AuthenticationError(8888, "expired")
            return {"ec": 200, "data": {"utoken": utoken}}
        if (ctrl, act) == ("account", "refreshtoken"):
            refresh_calls += 1
            await asyncio.sleep(0.02)
            return {"ec": 200, "data": {"token": "token-a-new"}}
        if (ctrl, act) == ("account", "login"):
            relogin_calls += 1
            return {"ec": 200, "data": {"token": "token-a-relogin", "user": {"id": user_id or "user-a"}}}
        raise AssertionError(f"unexpected call: {ctrl}/{act}")

    client._request_raw = MethodType(fake_raw, client)

    async def run():
        return await asyncio.gather(*(client.call("asset", "list") for _ in range(5)))

    results = asyncio.run(run())

    assert all(r["data"]["utoken"] == "token-a-new" for r in results)
    assert refresh_calls == 1
    assert relogin_calls == 0
    assert client._sessions["a"].utoken == "token-a-new"


def _debtloan_sync_page(bill: dict | None) -> dict:
    return {
        "ec": 200,
        "data": {
            "changes": [bill] if bill else [],
            "deletes": [],
            "hasmore": 0,
            "bookid": -1,
            "pageoffset": 1,
            "pagesign": "end",
            "lasttimes": {"-1": 100},
        },
    }


def _raw_loan_principal_bill(bill_id: int) -> dict:
    return {
        "id": bill_id,
        "userid": "user-a",
        "bookid": -1,
        "type": 7,
        "money": 100.0,
        "cateid": 0,
        "time": 1_788_889_000,
        "createtime": 1_788_889_000,
        "updatetime": 1_788_889_000,
        "remark": "principal",
        "status": 1,
        "assetid": 701,
        "fromid": 301,
        "targetid": -1,
        "extra": {},
    }


def test_debtloan_update_syncs_cache_then_updates_full_bill(client):
    updated_bills: list[dict] = []

    async def fake_request(self, ctrl, act, params=None):
        if (ctrl, act) == ("syncv2", "pull"):
            return _debtloan_sync_page(_raw_loan_principal_bill(9001))
        if (ctrl, act) == ("bill", "syncall"):
            payload = json.loads(params["v"])
            updated_bills.extend(json.loads(payload["bills"]["changelist"]))
            return {"ec": 200, "data": {}}
        raise AssertionError(f"unexpected call: {ctrl}/{act}")

    client._request = MethodType(fake_request, client)

    async def run():
        return await client.update_loan_principal(9001, amount=150.0, remark="adjusted")

    result = asyncio.run(run())

    assert result["ok"] is True
    assert len(updated_bills) == 1
    sent = updated_bills[0]
    # The upstream update carries the full cached bill, not a fabricated stub.
    assert sent["id"] == 9001
    assert sent["type"] == 7
    assert sent["money"] == 150.0
    assert sent["remark"] == "adjusted"
    assert sent["assetid"] == 701
    assert sent["fromid"] == 301
    assert sent["time"] == 1_788_889_000


def test_debtloan_update_refuses_to_fabricate_bill_when_cache_stays_empty(client):
    syncall_calls = 0

    async def fake_request(self, ctrl, act, params=None):
        nonlocal syncall_calls
        if (ctrl, act) == ("syncv2", "pull"):
            return _debtloan_sync_page(None)
        syncall_calls += 1
        return {"ec": 200, "data": {}}

    client._request = MethodType(fake_request, client)

    async def run():
        with pytest.raises(ValueError, match="not found in the local bill cache"):
            await client.update_loan_principal(9999, amount=150.0)

    asyncio.run(run())

    assert syncall_calls == 0
