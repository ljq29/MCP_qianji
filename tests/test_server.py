from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from qianji import icon_cache
from qianji.errors import AuthenticationError
from qianji.image_upload import MAX_BILL_IMAGE_UPLOAD_BYTES, BillImageValidationError
from qianji.server import app as server_app
from qianji.server.routes import GenericRequest, limiter, public_router, router
from qianji.sign import hash_password

PWD_HASH = hash_password("pwd")
WRONG_PWD_HASH = hash_password("wrong")
AUTH_PLACEHOLDER = "Bearer qj1.md5-b64.BASE64_ACCOUNT_TILDE_PWD_HASH"


def _auth_headers(account: str = "a", pwd_hash: str = PWD_HASH, *, b64: bool = False, urlsafe: bool = False):
    if b64:
        payload = f"{account}~{pwd_hash}".encode()
        encoder = base64.urlsafe_b64encode if urlsafe else base64.b64encode
        token = "qj1.md5-b64." + encoder(payload).decode("ascii").rstrip("=")
    else:
        token = f"qj1.md5.{account}~{pwd_hash}"
    return {"Authorization": "Bearer " + token}


AUTH_HEADERS = _auth_headers()
AUTH_HEADERS_BASE64 = _auth_headers(b64=True)
AUTH_HEADERS_URLSAFE_BASE64 = _auth_headers("用户@example.com", b64=True, urlsafe=True)


class FakeRouteClient:
    def __init__(self):
        self.default_account = "a"
        self._override: str | None = None
        self._tags_enabled = False
        self.session = SimpleNamespace(user_configs={})
        self.login_md5_calls: list[tuple[str, str, str | None]] = []

    @property
    def current_account(self) -> str:
        return self._override or self.default_account

    async def login(self, email: str, password: str, account_id: str | None = None):
        self.default_account = account_id or email
        return {"data": {"user": {"name": email, "email": email}}}

    async def login_md5(self, email: str, pwd_hash: str, account_id: str | None = None):
        self.login_md5_calls.append((email, pwd_hash, account_id))
        return await self.login(email, pwd_hash, account_id=account_id)

    async def account_login(self, v: str, pwd: str):
        return await self.login(v, pwd, account_id=v)

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

    @contextmanager
    def use_account(self, account_id: str | None):
        old = self._override
        if account_id:
            self._override = account_id
        try:
            yield
        finally:
            self._override = old

    async def asset_list(self, status: int = 0):
        return {"account_id": self.current_account, "status": status}

    async def get_tags_enabled(self):
        return self._tags_enabled

    async def set_tags_enabled(self, enabled: bool):
        self._tags_enabled = enabled
        return {"ok": True, "enabled": enabled}


def _make_rate_limited_app(fake: FakeRouteClient, *, use_middleware: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.client = fake
    app.state.limiter = limiter
    if use_middleware:
        app.add_middleware(server_app.RateLimitMiddleware)

    @app.exception_handler(RateLimitExceeded)
    def rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(status_code=429, content={"ec": 429, "em": "Rate limit exceeded"})

    app.include_router(router)
    return app


def _request(host: str = "example.test", proto: str = "https", authorization: str | None = None) -> Request:
    headers = [
        (b"host", b"internal:50003"),
        (b"x-forwarded-host", host.encode()),
        (b"x-forwarded-proto", proto.encode()),
    ]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
        }
    )


def _local_request(host: str = "127.0.0.1:50003", authorization: str | None = None) -> Request:
    headers = [
        (b"host", host.encode()),
    ]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
        }
    )


def test_route_account_context_does_not_mutate_default_account():
    app = FastAPI()
    fake = FakeRouteClient()
    app.state.client = fake
    app.include_router(router)

    with TestClient(app) as test_client:
        response = test_client.get("/assets", params={"account_id": "a", "status": 2}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"account_id": "a", "status": 2}
    assert fake.default_account == "a"
    assert fake.current_account == "a"


def test_route_rejects_cross_account_access():
    app = FastAPI()
    app.state.client = FakeRouteClient()
    app.include_router(router)

    with TestClient(app) as test_client:
        response = test_client.get("/assets", params={"account_id": "b"}, headers=AUTH_HEADERS)

    assert response.status_code == 403


def test_mirrored_account_login_rejects_body_account_different_from_authorization():
    app = FastAPI()
    app.state.client = FakeRouteClient()
    app.include_router(router)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/account/login",
            json={"v": "b", "pwd": "pwd"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 403


def test_route_requires_authorization_header():
    app = FastAPI()
    app.state.client = FakeRouteClient()
    app.include_router(router)

    with TestClient(app) as test_client:
        response = test_client.get("/assets")

    assert response.status_code == 401


def test_login_rate_limit_runs_before_auth_attempts():
    class RejectingLoginClient(FakeRouteClient):
        def __init__(self):
            super().__init__()
            self.login_calls = 0

        async def login(self, email: str, password: str, account_id: str | None = None):
            self.login_calls += 1
            raise AuthenticationError(1, "bad credentials")

    limiter.reset()
    fake = RejectingLoginClient()
    try:
        with TestClient(_make_rate_limited_app(fake)) as test_client:
            statuses = [
                test_client.post("/auth/login", headers=_auth_headers(pwd_hash=WRONG_PWD_HASH)).status_code
                for _ in range(12)
            ]

        assert statuses[:10] == [401] * 10
        assert statuses[10:] == [429, 429]
        assert fake.login_calls == 10
    finally:
        limiter.reset()


def test_global_rate_limit_middleware_applies_before_auth():
    class CountingLoginClient(FakeRouteClient):
        def __init__(self):
            super().__init__()
            self.login_calls = 0

        async def login(self, email: str, password: str, account_id: str | None = None):
            self.login_calls += 1
            return await super().login(email, password, account_id=account_id)

    limiter.reset()
    fake = CountingLoginClient()
    try:
        with TestClient(_make_rate_limited_app(fake, use_middleware=True)) as test_client:
            statuses = [test_client.get("/assets", headers=AUTH_HEADERS).status_code for _ in range(61)]

        assert statuses[:60] == [200] * 60
        assert statuses[60] == 429
        assert fake.login_calls == 60
    finally:
        limiter.reset()


def test_global_rate_limit_exempts_static_and_category_icons(monkeypatch):
    # A console page load pulls dozens of stylesheets, scripts and category
    # icons; those must not eat the same budget as the API calls the page needs.
    async def failing_fetch(name, *, transport=None):
        return None

    monkeypatch.setattr(icon_cache, "fetch_icon", failing_fetch)

    limiter.reset()
    try:
        app = FastAPI()
        app.state.client = FakeRouteClient()
        app.state.limiter = limiter
        app.add_middleware(server_app.RateLimitMiddleware)
        app.mount("/static", StaticFiles(directory=server_app.STATIC_DIR), name="static")
        app.include_router(public_router)
        app.include_router(router)

        with TestClient(app) as test_client:
            icon_statuses = [test_client.get("/category-icons/probe_missing.png").status_code for _ in range(70)]
            static_statuses = [test_client.get("/static/base.css").status_code for _ in range(70)]
            api_statuses = [test_client.get("/assets", headers=AUTH_HEADERS).status_code for _ in range(61)]
    finally:
        limiter.reset()

    assert icon_statuses == [404] * 70
    assert static_statuses == [200] * 70
    assert api_statuses[:60] == [200] * 60
    assert api_statuses[60] == 429


def test_route_accepts_base64_authorization_token():
    app = FastAPI()
    fake = FakeRouteClient()
    app.state.client = fake
    app.include_router(router)

    with TestClient(app) as test_client:
        response = test_client.get("/assets", headers=AUTH_HEADERS_BASE64)
        urlsafe_response = test_client.get("/assets", headers=AUTH_HEADERS_URLSAFE_BASE64)

    assert response.status_code == 200
    assert response.json() == {"account_id": "a", "status": 0}
    assert urlsafe_response.status_code == 200
    assert urlsafe_response.json() == {"account_id": "用户@example.com", "status": 0}
    assert fake.default_account == "用户@example.com"
    assert fake.login_md5_calls == [
        ("a", PWD_HASH, "a"),
        ("用户@example.com", PWD_HASH, "用户@example.com"),
    ]


def test_route_rejects_legacy_authorization_tokens():
    app = FastAPI()
    fake = FakeRouteClient()
    app.state.client = fake
    app.include_router(router)

    legacy_base64 = base64.b64encode(b"a~pwd").decode("ascii")

    with TestClient(app) as test_client:
        direct = test_client.get("/assets", headers={"Authorization": "Bearer a~pwd"})
        encoded = test_client.get("/assets", headers={"Authorization": "Bearer " + legacy_base64})

    assert direct.status_code == 401
    assert encoded.status_code == 401


def test_auth_accounts_returns_only_authorized_account():
    class MultiAccountClient(FakeRouteClient):
        def list_accounts(self):
            return [
                {
                    "account_id": "a",
                    "user_name": "A",
                    "email": "a@example.com",
                    "devname": "Device A",
                    "is_current": self.current_account == "a",
                },
                {
                    "account_id": "b",
                    "user_name": "B",
                    "email": "b@example.com",
                    "devname": "Device B",
                    "is_current": self.current_account == "b",
                },
            ]

    app = FastAPI()
    app.state.client = MultiAccountClient()
    app.include_router(router)

    with TestClient(app) as test_client:
        response = test_client.get("/auth/accounts", headers=AUTH_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["accounts"]) == 1
    assert payload["accounts"][0]["account_id"] == "a"


def test_generic_request_params_default_is_not_shared():
    first = GenericRequest()
    second = GenericRequest()

    first.params["x"] = 1

    assert second.params == {}


def test_compose_uses_single_api_service_for_mcp():
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")

    assert "qianji-api:" in compose
    assert "qianji-mcp-http:" not in compose
    assert "50004" not in compose
    assert "MCP_PORT" not in compose
    assert "QIANJI_MCP_MODE: ${QIANJI_MCP_MODE:-advanced}" in compose


def test_mcp_config_route_is_registered():
    assert any(getattr(route, "path", "") == "/mcp-config" for route in server_app.app.routes)


def test_about_page_escapes_account_fields(monkeypatch):
    class FakeHomepageClient:
        current_account = "<current>"

        def list_accounts(self):
            return [
                {
                    "account_id": "<script>",
                    "user_name": "Tom & Co",
                    "email": '"quoted"@example.com',
                    "devname": "<Device>",
                    "is_current": True,
                }
            ]

    monkeypatch.setattr(server_app.app.state, "client", FakeHomepageClient(), raising=False)

    html = asyncio.run(server_app.about_page(_request("money.example.com", "https")))

    assert "<script></td>" not in html
    assert "&lt;s***t&gt;" in html
    assert "Tom &amp; Co" not in html
    assert "To***Co" in html
    assert "&quot;quoted&quot;@example.com" not in html
    assert "&quot;q***@e***.com" in html
    assert "&lt;Device&gt;" in html
    assert "&lt;c***t&gt;" in html
    assert "Streamable HTTP MCP" in html
    assert "/mcp" in html
    assert "QIANJI_MCP_MODE" in html
    assert "advanced" in html
    assert "?mode=write" in html
    assert "create_expense" in html
    assert "create_income" in html
    assert "create_transfer" in html
    assert "delete_bill" in html
    assert "update_bill" in html
    assert "Add MCP to Agent" in html
    assert "mcpServers" in html
    assert "streamable-http" in html
    assert 'class="code-block"' in html
    assert "Agent config" in html
    assert "https://money.example.com/mcp" in html
    assert "Authorization" in html
    assert AUTH_PLACEHOLDER in html
    assert "Local agent config" not in html
    assert "操作台" in html
    assert "apiAccount" not in html
    assert "localStorage" not in html


def test_mcp_config_page_returns_bootstrap_page_without_authorization(monkeypatch):
    saved_hash = hash_password("saved-pass")
    fake = FakeRouteClient()
    fake.session = SimpleNamespace(email="saved@example.com", pwd_hash=saved_hash)
    monkeypatch.setattr(server_app.app.state, "client", fake, raising=False)

    html = asyncio.run(server_app.mcp_config_page(_request("money.example.com", "https")))

    assert "添加 MCP" in html
    assert "qianji.api.credentials" in html
    assert "localStorage" in html
    assert "Authorization" in html
    assert "Bearer qj1.md5-b64." not in html
    assert fake.login_md5_calls == []


def test_mcp_config_page_uses_request_authorization_not_saved_session(monkeypatch):
    saved_hash = hash_password("saved-pass")
    request_hash = hash_password("request-pass")
    saved_token = base64.b64encode(f"saved@example.com~{saved_hash}".encode()).decode("ascii")
    request_token = base64.b64encode(f"request@example.com~{request_hash}".encode()).decode("ascii")
    fake = FakeRouteClient()
    fake.session = SimpleNamespace(email="saved@example.com", pwd_hash=saved_hash)
    monkeypatch.setattr(server_app.app.state, "client", fake, raising=False)

    html = asyncio.run(
        server_app.mcp_config_page(
            _request(
                "money.example.com",
                "https",
                authorization=_auth_headers("request@example.com", request_hash)["Authorization"],
            )
        )
    )

    assert "添加 MCP" in html
    assert "https://money.example.com/mcp" in html
    assert f"Bearer qj1.md5-b64.{request_token}" in html
    assert f"Bearer qj1.md5-b64.{saved_token}" not in html
    assert "request@example.com" in html
    assert "saved@example.com" not in html
    assert "your-qianji-account~your-qianji-password" not in html
    assert 'data-mode="default"' in html
    assert 'data-mode="read"' in html
    assert 'data-mode="write"' in html
    assert 'data-mode="advanced"' in html
    assert 'data-mode="full"' in html
    assert '"mode": "default"' in html
    assert 'mode === "default"' in html
    assert "selectedMcpUrl()" in html
    assert "copyMcpJson" in html
    assert "copyAgentPrompt" in html
    assert "请把下面的钱迹 MCP 服务添加到你的 MCP 配置中" in html
    assert fake.login_md5_calls == [("request@example.com", request_hash, "request@example.com")]


def test_mcp_config_page_rejects_invalid_authorization(monkeypatch):
    saved_hash = hash_password("saved-pass")
    fake = FakeRouteClient()
    fake.session = SimpleNamespace(email="saved@example.com", pwd_hash=saved_hash)
    monkeypatch.setattr(server_app.app.state, "client", fake, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(server_app.mcp_config_page(_local_request(authorization="Bearer not-a-qianji-token")))

    assert exc_info.value.status_code == 401
    assert fake.login_md5_calls == []


CONSOLE_DIR = Path(server_app.STATIC_DIR) / "console"


def _console_js() -> str:
    return (CONSOLE_DIR / "console.js").read_text(encoding="utf-8")


def _console_css() -> str:
    return (CONSOLE_DIR / "console.css").read_text(encoding="utf-8")


def test_homepage_is_login_console():
    html = asyncio.run(server_app.homepage(_local_request()))

    assert "连接钱迹账号" in html
    assert "apiAccount" in html
    assert "apiPassword" in html
    assert "savedAccountSelect" in html
    assert "mcpConfigJson" in html
    assert "copyMcpConfig" in html
    assert "queryStartDate" in html
    assert "queryEndDate" in html
    assert "billList" in html
    assert "createBill" in html
    assert "saveBill" in html
    assert "pwd_hash" in html
    assert '<fieldset id="editBillPanel" class="edit-actions is-hidden" hidden>' in html
    assert "pwd_hash 与密码等价" in html
    assert "/static/base.css" in html
    assert "deleteBillButton" in html
    assert html.index('id="saveBillButton"') < html.index('id="cancelEditButton"') < html.index('id="deleteBillButton"')
    assert "<label>标签" in html
    assert '<input id="billTags" placeholder="标签名或ID，逗号分隔">' in html
    assert "categoryField" in html
    assert "assetFieldLabel" in html
    assert "targetAssetField" in html
    assert "/about" in html
    assert "/mcp-config" in html
    assert "/docs" in html
    # Console styles and behavior live in same-origin static files (CSP allows
    # 'self'); the page keeps only a bootstrap object carrying boot values.
    assert '<link rel="stylesheet" href="/static/console/console.css">' in html
    assert '<script src="/static/console/console.js" defer></script>' in html
    assert '<script>window.__QJ_BOOT__ = {"mcpPath": "/mcp"};</script>' in html
    assert "<style>" not in html
    assert "MCP Modes" not in html
    assert "Agent config" not in html


def test_console_script_preserves_auth_and_parsing_contract():
    js = _console_js()

    assert "base64Utf8" in js
    assert "Authorization 已使用 pwd_hash 和 base64 编码" in js
    assert "const mcpPath = window.__QJ_BOOT__.mcpPath;" in js
    assert "connected: false" in js
    assert "connecting: false" in js
    assert "refsLoaded: false" in js
    assert "connectionVersion: 0" in js
    assert "protectedActions" in js
    assert "function syncConnectionState" in js
    assert "请先连接钱迹账号" in js
    assert "function connectAndLoad" in js
    assert "async function autoConnect" in js
    assert "if (hasStoredCredentials) autoConnect()" in js
    assert "function ensureRefsLoaded" in js
    assert "连接状态已变更，请重新连接" in js
    assert "凭据已变更，请重新连接" in js
    assert "function quoteUnsafeJsonIntegers" in js
    assert 'else if (ch === "\\\\")' in js
    assert "function parseJsonPreservingLargeInts" in js
    assert "parseJsonPreservingLargeInts(text)" in js
    assert "try { return parseJsonPreservingLargeInts(value); }" in js
    assert "function rememberSavedCredential" in js
    assert '{...bill, id: String(bill.id)}' in js
    assert 'window.confirm("确认删除账单 ID " + id + "？此操作不可恢复。")' in js
    assert "请先从账单列表选择要删除的账单" in js
    assert "const id = String(state.editingBill.id);" in js
    assert "function setElementVisible" in js
    assert 'setElementVisible("editBillPanel", editing)' in js
    assert 'setElementVisible("targetAssetField", isTransfer)' in js
    assert "clearBillForm(Number(typeButton.dataset.billType))" in js
    assert 'callApi("/tags")' in js
    assert "parseTagInput" in js
    assert "selectedTagIds" in js
    assert "hasStoredCredentials" in js
    assert "function occurredAtFromDate" in js
    assert "bill_type" in js
    assert "from_asset_id" in js
    assert "category_id" in js
    assert "标签名称不唯一" in js
    assert "未找到标签" in js
    assert 'displayName: path.join(" / ")' in js
    assert "function assetNameById" in js
    assert "bill.fromid ?? bill.assetid" in js
    assert "bill.fromact || bill.assetname" in js
    assert "bill.targetact" in js
    assert '.join(" -> ")' in js
    assert '"[" + assetTypeLabel(asset) + "] "' in js
    assert 'return "借贷"' in js
    assert 'textContent = isTransfer ? "转出资产"' in js


def test_console_styles_are_desktop_adapted():
    css = _console_css()

    assert "flex-direction: column" in css
    assert "minmax(150px, 0.45fr)" in css
    assert "scrollbar-gutter: stable" in css
    assert "max-height: 640px" not in css


def test_console_drag_sort_replaces_arrow_reorder():
    js = _console_js()
    css = _console_css()
    html = asyncio.run(server_app.homepage(_local_request()))

    # one entry button per reorderable panel
    assert 'data-action="sortAssets"' in html
    assert 'data-action="sortCards"' in html
    assert 'data-action="sortCategories"' in html
    # generic pointer-event sort machinery with a bottom confirm bar
    assert "const SORT_CONFIG" in js
    assert "function enterSortMode" in js
    assert "function exitSortMode" in js
    assert "function onSortPointerDown" in js
    assert "function syncCategoryOrderFromDom" in js
    assert "setPointerCapture" in js
    # the three upstream reorder endpoints
    assert 'callApi("/asset/reorder"' in js
    assert 'callApi("/usercard/reorder"' in js
    assert "submitCategoryReorder" in js
    # the legacy arrow-button reorder is gone
    assert "moveCategoryUp" not in js
    assert "moveCategoryDown" not in js
    assert "↑ 上移" not in js
    # sort-mode styling hooks
    assert ".data-list.sort-mode" in css
    assert ".drag-handle" in css


def test_console_bill_batch_mode():
    js = _console_js()
    css = _console_css()

    # batch state + long-press entry
    assert "batch: {active: false, selected: new Set()}" in js
    assert "BATCH_LONG_PRESS_MS" in js
    assert "function enterBatchMode" in js
    assert "function exitBatchMode" in js
    assert "function toggleBatchSelectAll" in js
    # deletion goes through the batch endpoint and keeps the second confirm
    assert 'callApi("/billbatch/deletev2"' in js
    assert "确认删除选中的" in js
    # large-int ids are joined without Number()
    assert '"[" + ids.join(",") + "]"' in js
    # FAB hides and rows grow the check column in batch mode
    assert ".workspace.batch-mode .qj-fab" in css
    assert ".workspace.batch-mode #billList .bill-item" in css
    assert ".workspace .bill-item .qj-check" in css


def test_console_card_option_sheet_and_filter():
    js = _console_js()
    css = _console_css()
    html = asyncio.run(server_app.homepage(_local_request()))

    # filter bar markup: search input + type chips container
    assert 'id="cardSearch"' in html
    assert 'id="cardTypeChips"' in html
    assert 'id="cardFilterBar"' in html
    # filter state + helpers
    assert 'cardFilter: {q: "", type: ""}' in js
    assert "function filteredCards" in js
    assert "function renderCardTypeChips" in js
    # the app hides the type filter until more than two card types exist
    assert "types.length <= 2" in js
    # row tap opens the bottom-sheet menu instead of inline buttons
    assert "function openCardOptions" in js
    assert "复制卡号" in js
    assert "function deleteCardRecord" in js
    assert "cardTypeChip" in js
    # sort mode must ignore the filter so the commit sees the full order
    assert 'sortState.mode === "cards"' in js
    # row side buttons are gone; deletion still goes through usercard/delete
    assert '"copyCard"' not in js
    assert '"editCard"' not in js
    assert 'callApi("/usercard/delete"' in js
    assert ".workspace .card-filter" in css


def test_console_category_cards_and_icon_picker():
    js = _console_js()
    css = _console_css()
    html = asyncio.run(server_app.homepage(_local_request()))

    # the datalist is gone; the editor uses a picker button + hidden input
    assert 'id="cateIconList"' not in html
    assert 'list="cateIconList"' not in js
    assert "data-icon-picker" in js
    assert "function openIconPicker" in js
    assert "function syncCateIconField" in js
    # /categories/icons interleaves group headers with icon entries
    assert "categoryIconGroups: []," in js
    assert "entry.groupname || entry.group || entry.name" in js
    # card layout: root card + 5-column subcategory grid + trailing add cell
    assert "function buildCategoryCard" in js
    assert "cate-subgrid" in js
    assert "添加子分类" in js
    assert "function openCategoryOptions" in js
    assert "转为二级分类" in js
    assert "转为一级分类" in js
    assert "更换所属分类" in js
    # moves reuse the app's endpoints (endpoint chosen via variable in both
    # saveCategory and moveCategoryUnder, so assert the path literals)
    assert 'callApi("/category/changelevel"' in js
    assert '"/category/movesubcate"' in js
    assert 'callApi("/category/deletev2"' in js
    # sort mode must keep the flat .data-row.sub shape the drag logic needs
    assert 'sortState.mode === "categories"' in js
    assert "mode === \"categories\"" in js
    # an in-progress edit survives a background re-render: the tree captures the
    # draft before wiping the DOM, and the capture only applies when the on-screen
    # editor still belongs to the current editing target (identity guard)
    assert 'captureCategoryDraft(list.querySelector(".tree-editor"))' in js
    assert "function captureCategoryDraft" in js
    assert 'if (mode === "edit" && String(editing.id) !== String(editor.dataset.cateId || "")) return;' in js
    # the editor icon value lives in the hidden input, not the picker preview <img>
    assert 'editor.querySelector("input.cate-icon").value' in js
    # inline row buttons are gone
    assert "function miniButton" not in js
    assert ".workspace .cate-card" in css
    assert ".workspace .cate-icon-grid" in css


def test_billbatch_wire_params_match_app():
    # jadx network/api/bill/e.java: the app sends `billIds`/`values` as JSON
    # array strings — `ids`/`v` only survive as friendly aliases.
    from qianji.endpoints import ENDPOINTS, prepare_params

    delete = next(e for e in ENDPOINTS if e.ctrl == "billbatch" and e.act == "deletev2")
    params = prepare_params(delete, (), {"ids": "[1,2]"})
    assert params["billIds"] == "[1,2]"
    assert "ids" not in params

    modify = next(e for e in ENDPOINTS if e.ctrl == "billbatch" and e.act == "modify")
    params = prepare_params(modify, (), {"ids": [1, 2], "v": "{}"})
    assert params["billIds"] == "[1, 2]"
    assert params["values"] == "{}"


def test_console_assets_omit_removed_tag_ui():
    combined = asyncio.run(server_app.homepage(_local_request())) + _console_js() + _console_css()

    for absent in (
        "password:",
        "deleteBillId",
        "deleteBillPanel",
        "body.bill_time",
        "money: Number",
        "标签（逗号分隔）",
        "选择标签",
        "清空标签",
        "刷新标签",
        "selectedTags",
        "tagPanel",
        "tagGroups",
        "categoryChips",
    ):
        assert absent not in combined


def test_about_page_local_agent_config_uses_api_port_for_mcp(monkeypatch):
    class FakeHomepageClient:
        current_account = ""

        def list_accounts(self):
            return []

    monkeypatch.setattr(server_app.app.state, "client", FakeHomepageClient(), raising=False)

    html = asyncio.run(server_app.about_page(_local_request()))

    assert '"url": "http://127.0.0.1:50003/mcp"' in html
    assert '"url": "http://127.0.0.1:50004/mcp"' not in html
    assert f'"Authorization": "{AUTH_PLACEHOLDER}"' in html
    assert "Local agent config" not in html


def test_login_alias_and_docs_have_navigation(monkeypatch):
    class FakeHomepageClient:
        current_account = ""

        def list_accounts(self):
            return []

    monkeypatch.setattr(server_app.app.state, "client", FakeHomepageClient(), raising=False)

    with TestClient(server_app.app) as c:
        login = c.get("/login")
        about = c.get("/about")
        docs = c.get("/docs")
        redoc = c.get("/redoc")
        swagger_js = c.get("/static/docs/swagger-ui-bundle.js")
        swagger_css = c.get("/static/docs/swagger-ui.css")
        redoc_js = c.get("/static/docs/redoc.standalone.js")
        redoc_logo = c.get("/static/docs/redoc-logo-mini.svg")
        favicon = c.get("/favicon.ico", follow_redirects=False)

    assert login.status_code == 200
    assert "连接钱迹账号" in login.text
    for page in (login, about, docs, redoc):
        assert 'class="topbar"' in page.text
        assert 'class="brand-mark"' in page.text
        assert 'rel="icon"' in page.text or 'rel="shortcut icon"' in page.text
        assert "topnav" not in page.text
        assert "qianji-doc-nav" not in page.text
    assert docs.status_code == 200
    assert "/login" in docs.text
    assert "/about" in docs.text
    assert "/redoc" in docs.text
    assert "/static/docs/swagger-ui-bundle.js" in docs.text
    assert "/static/docs/swagger-ui.css" in docs.text
    assert "cdn.jsdelivr" not in docs.text
    assert "fastapi.tiangolo.com" not in docs.text
    assert redoc.status_code == 200
    assert "/login" in redoc.text
    assert "/about" in redoc.text
    assert "/docs" in redoc.text
    assert "/static/docs/redoc.standalone.js" in redoc.text
    assert "cdn.jsdelivr" not in redoc.text
    assert "fonts.googleapis.com" not in redoc.text
    assert "fastapi.tiangolo.com" not in redoc.text
    assert swagger_js.status_code == 200
    assert swagger_css.status_code == 200
    assert redoc_js.status_code == 200
    assert "cdn.redoc.ly" not in redoc_js.text
    assert redoc_logo.status_code == 200
    assert favicon.status_code in (301, 302, 307, 308)
    assert favicon.headers["location"] == "/static/docs/favicon.svg"


class _RecordingClient:
    def __init__(self):
        self.calls = []
        self.default_account = "a"
        self._override = None

    @property
    def current_account(self):
        return self._override or self.default_account

    async def login(self, email, password, account_id=None):
        self.default_account = account_id or email
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

    @contextmanager
    def use_account(self, account_id):
        old = self._override
        if account_id:
            self._override = account_id
        try:
            yield
        finally:
            self._override = old

    async def _request(self, ctrl, act, params=None):
        self.calls.append((ctrl, act, params or {}, self.current_account))
        return {
            "ec": 200,
            "data": {"ctrl": ctrl, "act": act, "params": params or {}, "account_id": self.current_account},
        }

    async def _request_public(self, ctrl, act, params=None):
        self.calls.append((ctrl, act, params or {}, self.current_account))
        return {
            "ec": 200,
            "data": {"ctrl": ctrl, "act": act, "params": params or {}, "account_id": self.current_account},
        }


def _app_with(client):
    app = FastAPI()
    app.state.client = client
    app.state.file_session_mgr = getattr(client, "_session_mgr", None)
    app.include_router(server_app.public_router)
    app.include_router(router)
    return app


class _BillRouteClient(FakeRouteClient):
    def __init__(self, pages, delete_result=None):
        super().__init__()
        from qianji.session import SessionManager

        self._session_mgr = SessionManager(":memory:")
        self.pages = pages
        self.delete_result = delete_result
        self.sync_calls = []
        self.created = []
        self.updated = []
        self.deleted = []

    async def sync_pull(self, book_id=-1, last_time=0, page_offset=0, *, last_times=None, page_sign=""):
        self.sync_calls.append((book_id, last_time, page_offset, self.current_account))
        return (
            self.pages[page_offset]
            if page_offset < len(self.pages)
            else {"data": {"changes": [], "deletes": [], "hasmore": 0}}
        )

    async def currency_list(self):
        return {"data": {"list": [{"symbol": "CNY", "baseprice": "1.000000"}]}}

    async def asset_list(self, status: int = 0):
        return {
            "data": {
                "list": [
                    {"id": 3},
                    {"id": 8},
                    {"id": 9},
                    {"id": 100},
                    {"id": 200},
                    {"id": 201},
                    {"id": 202},
                ]
            }
        }

    async def category_list(self, book_id=-1, cat_type=None):
        return {
            "data": {
                "list": [
                    {"id": 7, "type": cat_type},
                    {"id": 10, "type": cat_type},
                    {"id": 11, "type": cat_type},
                ]
            }
        }

    async def bill_create(self, **kwargs):
        self.created.append({"account": self.current_account, **kwargs})
        return {"data": {"sync_result": {"bill": {"new_ids": [123]}}}}

    async def bill_update(self, bill):
        self.updated.append(bill)
        return {"data": {"sync_result": {"bill": {"update_ids": [bill["id"]]}}}}

    async def bill_delete(self, bill_ids):
        self.deleted.append((bill_ids, self.current_account))
        return self.delete_result or {"data": {"sync_result": {"bill": {"del_ids": bill_ids, "has_failed": False}}}}


def test_create_bill_route_accepts_semantic_transfer_fields_and_returns_write_result():
    client = _TagResolvingBillRouteClient(
        [],
        tags_enabled=True,
        tag_list_result={"data": {"list": [{"tagid": "x-id", "name": "x"}, {"tagid": "y-id", "name": "y"}]}},
    )
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.post(
            "/bills",
            json={
                "bill_type": "transfer",
                "amount": 12.5,
                "from_asset_id": 100,
                "to_asset_id": 200,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "tags": ["x", "y"],
                "images": ["_receipt.jpg"],
                "remark": "semantic transfer",
                "book_id": -1,
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["command"] == "create_transfer"
    assert response.json()["resource"] == "bill"
    assert response.json()["ids"] == {"bill_id": "123", "new_ids": ["123"]}
    assert client.created == [
        {
            "account": "a",
            "money": 12.5,
            "bill_type": 2,
            "cateid": 0,
            "assetid": 100,
            "remark": "semantic transfer",
            "book_id": -1,
            "target_asset_id": 200,
            "tags": ["x-id", "y-id"],
            "images": ["_receipt.jpg"],
            "bill_time": 1_775_993_400,
        }
    ]


def test_create_bill_route_rejects_non_key_images():
    client = _TagResolvingBillRouteClient([])
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.post(
            "/bills",
            json={
                "bill_type": "expense",
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 201,
                "category_id": 7,
                "images": ["https://example.com/receipt.jpg"],
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 422
    assert "image key" in str(response.json()["detail"])
    assert client.created == []


def test_tags_enabled_routes_get_and_put_state():
    client = FakeRouteClient()
    app = _app_with(client)

    with TestClient(app) as c:
        get_before = c.get("/tags/enabled", headers=AUTH_HEADERS)
        put_enabled = c.put("/tags/enabled", json={"enabled": True}, headers=AUTH_HEADERS)
        get_after = c.get("/tags/enabled", headers=AUTH_HEADERS)

    assert get_before.status_code == 200
    assert get_before.json() == {"enabled": False}
    assert put_enabled.status_code == 200
    assert put_enabled.json()["ok"] is True
    assert put_enabled.json()["command"] == "set_tags_enabled"
    assert put_enabled.json()["data"] == {"enabled": True}
    assert get_after.status_code == 200
    assert get_after.json() == {"enabled": True}


def test_reimbursement_cancel_and_tag_admin_routes_forward_to_client():
    class AdminClient(FakeRouteClient):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def cancel_reimbursement(self, reimbursement_bill_ids):
            self.calls.append(("cancel_reimbursement", reimbursement_bill_ids, self.current_account))
            return {"ok": True, "ids": reimbursement_bill_ids}

        async def tag_submit(self, **kwargs):
            self.calls.append(("tag_submit", kwargs, self.current_account))
            return {"ok": True, **kwargs}

        async def tag_delete(self, **kwargs):
            self.calls.append(("tag_delete", kwargs, self.current_account))
            return {"ok": True, **kwargs}

        async def tag_archive(self, **kwargs):
            self.calls.append(("tag_archive", kwargs, self.current_account))
            return {"ok": True, **kwargs}

        async def tag_submit_group(self, **kwargs):
            self.calls.append(("tag_submit_group", kwargs, self.current_account))
            return {"ok": True, **kwargs}

        async def tag_delete_group(self, **kwargs):
            self.calls.append(("tag_delete_group", kwargs, self.current_account))
            return {"ok": True, **kwargs}

        async def tag_reorder_tag(self, **kwargs):
            self.calls.append(("tag_reorder_tag", kwargs, self.current_account))
            return {"ok": True, **kwargs}

        async def tag_reorder_group(self, **kwargs):
            self.calls.append(("tag_reorder_group", kwargs, self.current_account))
            return {"ok": True, **kwargs}

    client = AdminClient()
    app = _app_with(client)

    with TestClient(app) as c:
        cancel = c.post("/reimbursements/cancel", json={"reimbursement_bill_ids": [501]}, headers=AUTH_HEADERS)
        created = c.post(
            "/tags",
            json={"name": "差旅", "group_id": "g1", "color": "#167bff"},
            headers=AUTH_HEADERS,
        )
        assert cancel.status_code == 200
        assert created.status_code == 200
        assert c.patch("/tags/t1", json={"name": "出差"}, headers=AUTH_HEADERS).status_code == 200
        assert c.delete("/tags/t1", headers=AUTH_HEADERS).status_code == 200
        assert c.post("/tags/t2/archive", headers=AUTH_HEADERS).status_code == 200
        assert c.post("/tag-groups", json={"name": "项目"}, headers=AUTH_HEADERS).status_code == 200
        assert c.patch("/tag-groups/g1", json={"name": "项目A"}, headers=AUTH_HEADERS).status_code == 200
        assert c.delete("/tag-groups/g1", headers=AUTH_HEADERS).status_code == 200
        assert c.post("/tags/reorder", json={"tag_ids": ["t2", "t1"]}, headers=AUTH_HEADERS).status_code == 200
        assert c.post("/tag-groups/reorder", json={"group_ids": ["g2", "g1"]}, headers=AUTH_HEADERS).status_code == 200

    assert cancel.json()["ok"] is True
    assert cancel.json()["command"] == "cancel_reimbursement"
    assert cancel.json()["ids"] == {"reimbursement_bill_ids": ["501"]}
    assert created.json()["ok"] is True
    assert created.json()["command"] == "create_tag"
    assert ("cancel_reimbursement", [501], "a") in client.calls
    assert ("tag_submit", {"name": "差旅", "group_id": "g1", "color": "#167bff"}, "a") in client.calls
    assert ("tag_submit", {"tag_id": "t1", "name": "出差"}, "a") in client.calls
    assert ("tag_delete", {"tag_id": "t1"}, "a") in client.calls
    assert ("tag_archive", {"tag_id": "t2"}, "a") in client.calls
    assert ("tag_submit_group", {"name": "项目"}, "a") in client.calls
    assert ("tag_submit_group", {"group_id": "g1", "name": "项目A"}, "a") in client.calls
    assert ("tag_delete_group", {"group_id": "g1"}, "a") in client.calls
    assert ("tag_reorder_tag", {"tag_ids": ["t2", "t1"]}, "a") in client.calls
    assert ("tag_reorder_group", {"group_ids": ["g2", "g1"]}, "a") in client.calls


def test_upload_bill_image_route_reads_multipart_and_returns_image_key():
    class UploadClient(FakeRouteClient):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def upload_bill_image(self, *, filename, content, mime_type):
            self.calls.append(
                {"filename": filename, "content": content, "mime_type": mime_type, "account": self.current_account}
            )
            return {"ok": True, "image_key": "_key.jpg"}

    client = UploadClient()
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.post(
            "/uploads/bill-image",
            files={"file": ("bill.jpg", b"image-bytes", "image/jpeg")},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["command"] == "upload_bill_image"
    assert response.json()["data"]["image_key"] == "_key.jpg"
    assert client.calls == [
        {"filename": "bill.jpg", "content": b"image-bytes", "mime_type": "image/jpeg", "account": "a"}
    ]


def test_upload_bill_image_route_maps_validation_error_to_422():
    class UploadClient(FakeRouteClient):
        async def upload_bill_image(self, *, filename, content, mime_type):
            raise BillImageValidationError("invalid_image", "Invalid image")

    app = _app_with(UploadClient())

    with TestClient(app) as c:
        response = c.post(
            "/uploads/bill-image",
            files={"file": ("bill.jpg", b"not-image", "image/jpeg")},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_image"


def test_upload_bill_image_route_rejects_oversized_request_before_client_call():
    class UploadClient(FakeRouteClient):
        def __init__(self):
            super().__init__()
            self.upload_calls = 0

        async def upload_bill_image(self, *, filename, content, mime_type):
            self.upload_calls += 1
            return {"image_key": "_should-not-upload"}

    client = UploadClient()
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.post(
            "/uploads/bill-image",
            files={"file": ("huge.jpg", b"x" * (MAX_BILL_IMAGE_UPLOAD_BYTES + 1), "image/jpeg")},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "Uploaded file is too large"
    assert client.upload_calls == 0


def test_search_export_and_report_routes_use_cache_and_return_download_urls():
    bills = [
        {
            "id": 1,
            "userid": "u1",
            "bookid": -1,
            "type": 0,
            "money": 12.5,
            "cateid": 10,
            "time": 100,
            "createtime": 100,
            "updatetime": 100,
            "remark": "coffee",
            "status": 1,
            "assetid": 3,
            "fromid": -1,
            "targetid": -1,
            "extra": {"tags": ["food"]},
        },
        {
            "id": 2,
            "userid": "u1",
            "bookid": -1,
            "type": 0,
            "money": 80,
            "cateid": 11,
            "time": 200,
            "createtime": 200,
            "updatetime": 200,
            "remark": "taxi airport",
            "status": 1,
            "assetid": 3,
            "fromid": -1,
            "targetid": -1,
            "extra": {"tags": ["travel"]},
        },
    ]
    client = _BillRouteClient([{"data": {"changes": bills, "deletes": [], "hasmore": 0}}])
    app = _app_with(client)

    with TestClient(app, base_url="https://api.example") as c:
        search = c.get("/bills/search", params={"keyword": "taxi"}, headers=AUTH_HEADERS)
        exported = c.post("/bills/export", json={"format": "csv", "keyword": "taxi"}, headers=AUTH_HEADERS)
        report = c.post("/reports/image", json={"report_type": "monthly_summary"}, headers=AUTH_HEADERS)
        downloaded = c.get(exported.json()["download_url"].replace("https://api.example", ""))

    assert search.status_code == 200
    assert [item["id"] for item in search.json()["items"]] == ["2"]
    assert exported.status_code == 200
    assert exported.json()["download_url"].startswith("https://api.example/files/")
    assert report.status_code == 200
    assert report.json()["content_type"] == "image/svg+xml"
    assert downloaded.status_code == 200
    assert "taxi airport" in downloaded.text


class _TagResolvingBillRouteClient(_BillRouteClient):
    def __init__(self, pages, delete_result=None, *, tags_enabled=True, tag_list_result=None):
        super().__init__(pages, delete_result=delete_result)
        self._tags_enabled = tags_enabled
        self.session = SimpleNamespace(user_configs={"tagon": 1 if tags_enabled else 0})
        self.tag_list_result = tag_list_result or {"data": {"list": []}}
        self.tag_list_calls = 0
        self.tag_submit_calls = []

    async def tag_list(self):
        self.tag_list_calls += 1
        return self.tag_list_result

    async def tag_submit(self, name):
        self.tag_submit_calls.append(name)
        return {"data": {"tagid": f"created:{name}"}}


def test_post_bills_resolves_tag_names_and_creates_missing():
    client = _TagResolvingBillRouteClient(
        [],
        tags_enabled=True,
        tag_list_result={"data": {"list": [{"tagid": "existing-id", "name": "existing"}]}},
    )
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.post(
            "/bills",
            json={
                "bill_type": "expense",
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 100,
                "category_id": 7,
                "remark": "resolved tags",
                "book_id": -1,
                "tags": ["existing", "missing"],
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["command"] == "create_expense"
    assert response.json()["data"]["tag_ids"] == ["existing-id", "created:missing"]
    assert client.tag_submit_calls == ["missing"]
    assert client.created[-1]["tags"] == ["existing-id", "created:missing"]


def test_post_bills_returns_409_when_tag_feature_disabled():
    client = _TagResolvingBillRouteClient(
        [],
        tags_enabled=False,
        tag_list_result={"data": {"list": [{"tagid": "blocked-id", "name": "blocked"}]}},
    )
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.post(
            "/bills",
            json={
                "bill_type": "expense",
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "asset_id": 100,
                "category_id": 7,
                "remark": "disabled tags",
                "book_id": -1,
                "tags": ["blocked"],
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 409
    assert "PUT /tags/enabled" in str(response.json()["detail"])
    assert client.created == []


def test_patch_bills_resolves_tag_names_and_allows_clearing():
    original = {
        "id": 100,
        "userid": "u1",
        "bookid": -1,
        "type": 0,
        "money": 1.0,
        "cateid": 10,
        "time": 20,
        "createtime": 11,
        "updatetime": 12,
        "remark": "old",
        "status": 1,
        "assetid": 3,
        "fromid": -1,
        "targetid": -1,
        "extra": {"baoxiaoed": 0, "tags": ["old"]},
        "platform": 0,
    }
    client = _TagResolvingBillRouteClient(
        [{"data": {"changes": [original], "deletes": [], "hasmore": 0}}],
        tags_enabled=True,
        tag_list_result={"data": {"list": [{"tagid": "existing-id", "name": "existing"}]}},
    )
    app = _app_with(client)

    with TestClient(app) as c:
        resolved = c.patch(
            "/bills/100",
            json={"bill_type": "expense", "tags": ["existing", "missing"]},
            headers=AUTH_HEADERS,
        )
        cleared = c.patch("/bills/100", json={"bill_type": "expense", "tags": []}, headers=AUTH_HEADERS)

    assert resolved.status_code == 200
    assert resolved.json()["command"] == "update_expense"
    assert resolved.json()["ids"]["bill_id"] == "100"
    assert client.updated[-2]["extra"]["tags"] == ["existing-id", "created:missing"]
    assert cleared.status_code == 200
    assert client.updated[-1]["extra"]["tags"] == []


class _BusinessFeatureRouteClient(FakeRouteClient):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    async def create_refund(self, **kwargs):
        self.calls.append(("create_refund", kwargs))
        return {"ok": True, "refund_bill_id": 301, **kwargs}

    async def update_refund(self, **kwargs):
        self.calls.append(("update_refund", kwargs))
        return {"ok": True, **kwargs}

    async def create_reimbursement(self, **kwargs):
        self.calls.append(("create_reimbursement", kwargs))
        return {"ok": True, "reimbursement_bill_id": 501, **kwargs}

    async def update_reimbursement(self, **kwargs):
        self.calls.append(("update_reimbursement", kwargs))
        return {"ok": True, **kwargs}

    async def create_loan_asset(self, **kwargs):
        self.calls.append(("create_loan_asset", kwargs))
        return {"ok": True, "asset_id": 701, **kwargs}

    async def update_loan_asset(self, **kwargs):
        self.calls.append(("update_loan_asset", kwargs))
        return {"ok": True, **kwargs}

    async def record_loan_collection(self, **kwargs):
        self.calls.append(("record_loan_collection", kwargs))
        return {"ok": True, "bill_id": 801, "interest_bill_id": 802, **kwargs}

    async def update_loan_collection(self, **kwargs):
        self.calls.append(("update_loan_collection", kwargs))
        return {"ok": True, **kwargs}

    async def add_loan_principal(self, **kwargs):
        self.calls.append(("add_loan_principal", kwargs))
        return {"ok": True, "bill_id": 811, **kwargs}

    async def update_loan_principal(self, **kwargs):
        self.calls.append(("update_loan_principal", kwargs))
        return {"ok": True, **kwargs}

    async def create_debt_asset(self, **kwargs):
        self.calls.append(("create_debt_asset", kwargs))
        return {"ok": True, "asset_id": 901, **kwargs}

    async def update_debt_asset(self, **kwargs):
        self.calls.append(("update_debt_asset", kwargs))
        return {"ok": True, **kwargs}

    async def record_debt_repayment(self, **kwargs):
        self.calls.append(("record_debt_repayment", kwargs))
        return {"ok": True, "bill_id": 1001, "interest_bill_id": 1002, **kwargs}

    async def update_debt_repayment(self, **kwargs):
        self.calls.append(("update_debt_repayment", kwargs))
        return {"ok": True, **kwargs}

    async def add_debt_principal(self, **kwargs):
        self.calls.append(("add_debt_principal", kwargs))
        return {"ok": True, "bill_id": 1011, **kwargs}

    async def update_debt_principal(self, **kwargs):
        self.calls.append(("update_debt_principal", kwargs))
        return {"ok": True, **kwargs}

    async def asset_list_loan(self, loan_type, status=0):
        self.calls.append(("asset_list_loan", {"loan_type": loan_type, "status": status}))
        return {"data": {"list": [{"id": loan_type, "status": status}]}}


def test_business_feature_routes_forward_refund_reimbursement_and_loan_payloads():
    client = _BusinessFeatureRouteClient()
    app = _app_with(client)

    with TestClient(app) as c:
        refund = c.post(
            "/bills/101/refunds",
            json={"amount": 12.5, "occurred_at": "2026-04-12T19:30:00+08:00", "asset_id": 201},
            headers=AUTH_HEADERS,
        )
        refund_update = c.patch(
            "/refunds/301",
            json={"source_bill_id": 101, "amount": 13.5, "occurred_at": "2026-04-13T19:30:00+08:00"},
            headers=AUTH_HEADERS,
        )
        reimbursement = c.post(
            "/reimbursements",
            json={
                "source_bill_ids": [401, 402],
                "amount": 88.0,
                "occurred_at": "2026-04-14T19:30:00+08:00",
                "asset_id": 202,
            },
            headers=AUTH_HEADERS,
        )
        reimbursement_update = c.patch(
            "/reimbursements/501",
            json={"source_bill_ids": [401], "amount": 66.0, "occurred_at": "2026-04-15T19:30:00+08:00"},
            headers=AUTH_HEADERS,
        )
        loan_asset = c.post(
            "/assets/loans",
            json={"name": "借给张三", "amount": 100.0, "startdate": "2026-01-01", "linked_asset_id": 201},
            headers=AUTH_HEADERS,
        )
        loan_asset_update = c.patch(
            "/assets/loans/701",
            json={"name": "借给张三 updated", "enddate": "2027-01-01"},
            headers=AUTH_HEADERS,
        )
        collection = c.post(
            "/loans/701/collections",
            json={
                "amount": 40.0,
                "occurred_at": "2026-05-01T10:00:00+08:00",
                "to_asset_id": 201,
                "interest_amount": 3.0,
            },
            headers=AUTH_HEADERS,
        )
        collection_update = c.patch(
            "/loan-collections/801",
            json={"amount": 41.0, "occurred_at": "2026-05-02T10:00:00+08:00", "to_asset_id": 201},
            headers=AUTH_HEADERS,
        )

    assert refund.status_code == 200
    assert refund_update.status_code == 200
    assert reimbursement.status_code == 200
    assert reimbursement_update.status_code == 200
    assert loan_asset.status_code == 200
    assert loan_asset_update.status_code == 200
    assert collection.status_code == 200
    assert collection_update.status_code == 200
    assert refund.json()["command"] == "create_refund"
    assert refund.json()["ids"]["source_bill_id"] == "101"
    assert refund.json()["ids"]["refund_bill_id"] == "301"
    assert reimbursement.json()["command"] == "create_reimbursement"
    assert reimbursement.json()["ids"]["source_bill_ids"] == ["401", "402"]
    assert loan_asset.json()["command"] == "create_loan_asset"
    assert collection.json()["ids"]["bill_id"] == "801"
    assert collection.json()["ids"]["interest_bill_id"] == "802"
    assert [name for name, _ in client.calls] == [
        "create_refund",
        "update_refund",
        "create_reimbursement",
        "update_reimbursement",
        "create_loan_asset",
        "update_loan_asset",
        "record_loan_collection",
        "update_loan_collection",
    ]
    assert client.calls[0][1]["source_bill_id"] == 101
    assert client.calls[0][1]["occurred_at"] == 1_775_993_400
    assert client.calls[5][1] == {"asset_id": 701, "name": "借给张三 updated", "enddate": "2027-01-01"}
    assert client.calls[6][1]["loan_asset_id"] == 701
    assert client.calls[6][1]["interest_amount"] == 3.0


def test_business_feature_routes_reject_invalid_image_keys_before_client_call():
    client = _BusinessFeatureRouteClient()
    app = _app_with(client)

    with TestClient(app) as c:
        refund = c.post(
            "/bills/101/refunds",
            json={
                "amount": 12.5,
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "images": ["https://example.com/receipt.jpg"],
            },
            headers=AUTH_HEADERS,
        )
        loan_asset = c.patch(
            "/assets/loans/701",
            json={"images": [f"_{i}.jpg" for i in range(7)]},
            headers=AUTH_HEADERS,
        )
        repayment = c.post(
            "/debts/901/repayments",
            json={
                "amount": 50.0,
                "occurred_at": "2026-05-03T10:00:00+08:00",
                "images": ["dir/receipt.jpg"],
            },
            headers=AUTH_HEADERS,
        )

    assert refund.status_code == 422
    assert loan_asset.status_code == 422
    assert repayment.status_code == 422
    assert client.calls == []


def test_business_feature_routes_forward_debt_payloads():
    client = _BusinessFeatureRouteClient()
    app = _app_with(client)

    with TestClient(app) as c:
        debt_asset = c.post(
            "/assets/debts",
            json={"name": "欠李四", "amount": 200.0, "startdate": "2026-01-01", "linked_asset_id": 201},
            headers=AUTH_HEADERS,
        )
        debt_asset_update = c.patch(
            "/assets/debts/901",
            json={"name": "欠李四 updated"},
            headers=AUTH_HEADERS,
        )
        repayment = c.post(
            "/debts/901/repayments",
            json={
                "amount": 50.0,
                "occurred_at": "2026-05-03T10:00:00+08:00",
                "from_asset_id": 201,
                "interest_amount": 4.0,
            },
            headers=AUTH_HEADERS,
        )
        repayment_update = c.patch(
            "/debt-repayments/1001",
            json={"amount": 51.0, "occurred_at": "2026-05-04T10:00:00+08:00", "from_asset_id": 201},
            headers=AUTH_HEADERS,
        )

    assert debt_asset.status_code == 200
    assert debt_asset_update.status_code == 200
    assert repayment.status_code == 200
    assert repayment_update.status_code == 200
    assert debt_asset.json()["command"] == "create_debt_asset"
    assert repayment.json()["ids"]["bill_id"] == "1001"
    assert repayment.json()["ids"]["interest_bill_id"] == "1002"
    assert [name for name, _ in client.calls] == [
        "create_debt_asset",
        "update_debt_asset",
        "record_debt_repayment",
        "update_debt_repayment",
    ]
    assert client.calls[2][1]["debt_asset_id"] == 901
    assert client.calls[2][1]["interest_amount"] == 4.0


def test_business_feature_routes_forward_principal_and_list_payloads():
    client = _BusinessFeatureRouteClient()
    app = _app_with(client)

    with TestClient(app) as c:
        loans = c.get("/assets/loans", params={"status": 1}, headers=AUTH_HEADERS)
        debts = c.get("/assets/debts", params={"status": 2}, headers=AUTH_HEADERS)
        loan_principal = c.post(
            "/loans/701/principal-records",
            json={"amount": 60.0, "occurred_at": "2026-05-05T10:00:00+08:00", "from_asset_id": 201},
            headers=AUTH_HEADERS,
        )
        loan_principal_update = c.patch(
            "/loan-principal-records/811",
            json={"amount": 61.0, "occurred_at": "2026-05-06T10:00:00+08:00", "from_asset_id": 201},
            headers=AUTH_HEADERS,
        )
        debt_principal = c.post(
            "/debts/901/principal-records",
            json={"amount": 70.0, "occurred_at": "2026-05-07T10:00:00+08:00", "to_asset_id": 202},
            headers=AUTH_HEADERS,
        )
        debt_principal_update = c.patch(
            "/debt-principal-records/1011",
            json={"amount": 71.0, "occurred_at": "2026-05-08T10:00:00+08:00", "to_asset_id": 202},
            headers=AUTH_HEADERS,
        )

    assert loans.status_code == 200
    assert debts.status_code == 200
    assert loan_principal.status_code == 200
    assert loan_principal_update.status_code == 200
    assert debt_principal.status_code == 200
    assert debt_principal_update.status_code == 200
    assert loan_principal.json()["command"] == "add_loan_principal"
    assert loan_principal.json()["ids"]["bill_id"] == "811"
    assert debt_principal.json()["command"] == "add_debt_principal"
    assert debt_principal.json()["ids"]["bill_id"] == "1011"
    assert [name for name, _ in client.calls] == [
        "asset_list_loan",
        "asset_list_loan",
        "add_loan_principal",
        "update_loan_principal",
        "add_debt_principal",
        "update_debt_principal",
    ]
    assert client.calls[0][1] == {"loan_type": 52, "status": 1}
    assert client.calls[1][1] == {"loan_type": 51, "status": 2}
    assert client.calls[2][1]["loan_asset_id"] == 701
    assert client.calls[4][1]["debt_asset_id"] == 901


def test_bills_range_filters_paged_results():
    # Cache-backed: sync pulls these bills into cache (one page), then the range
    # query reads from cache and filters by occurred_at, returning the normalized shape.
    client = _BillRouteClient(
        [
            {
                "data": {
                    "changes": [
                        {"id": 1, "time": 10, "bookid": -1, "type": 0, "money": 1},
                        {"id": 2, "time": 20, "bookid": -1, "type": 0, "money": 2},
                        {"id": 3, "time": 30, "bookid": -1, "type": 0, "money": 3},
                        {"id": 4, "time": 40, "bookid": -1, "type": 0, "money": 4},
                    ],
                    "deletes": [],
                    "hasmore": 0,
                    "bookid": -1,
                    "pageoffset": 1,
                    "pagesign": "end",
                    "lasttimes": {"-1": 40},
                }
            },
        ]
    )
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.get(
            "/bills/range",
            params={"start_time": 15, "end_time": 35, "book_id": -1, "limit": 50},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    data = response.json()
    assert [bill["id"] for bill in data["items"]] == ["3", "2"]  # newest-first, within [15,35]
    assert data["has_more"] is False
    assert data["next_cursor"] is None


def test_get_bills_returns_paginated_cache_shape():
    client = _BillRouteClient(
        [
            {
                "data": {
                    "changes": [{"id": i, "time": 100 + i, "bookid": -1, "type": 0, "money": i} for i in range(1, 4)],
                    "deletes": [],
                    "hasmore": 0,
                    "bookid": -1,
                    "pageoffset": 1,
                    "pagesign": "end",
                    "lasttimes": {"-1": 103},
                }
            },
        ]
    )
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.get("/bills", params={"book_id": -1, "limit": 50}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert {bill["id"] for bill in data["items"]} == {"1", "2", "3"}
    assert "has_more" in data and "next_cursor" in data


def test_post_bills_sync_returns_stats():
    client = _BillRouteClient(
        [
            {
                "data": {
                    "changes": [{"id": 1, "time": 10, "bookid": -1, "type": 0, "money": 1}],
                    "deletes": [],
                    "hasmore": 0,
                    "bookid": -1,
                    "pageoffset": 1,
                    "pagesign": "end",
                    "lasttimes": {"-1": 10},
                }
            },
        ]
    )
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.post("/bills/sync", params={"book_id": -1, "full": True}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["synced"] == 1
    assert data["truncated"] is False
    assert -1 in data["books"]


def test_update_bill_patches_allowed_fields_on_original_bill():
    original = {
        "id": 100,
        "userid": "u1",
        "bookid": -1,
        "type": 0,
        "money": 1.0,
        "cateid": 10,
        "time": 20,
        "createtime": 11,
        "updatetime": 12,
        "remark": "old",
        "status": 1,
        "assetid": 3,
        "fromid": -1,
        "targetid": -1,
        "extra": {"baoxiaoed": 0, "tags": ["old"]},
        "platform": 0,
    }
    client = _TagResolvingBillRouteClient(
        [{"data": {"changes": [original], "deletes": [], "hasmore": 0}}],
        tags_enabled=True,
        tag_list_result={"data": {"list": [{"tagid": "work-id", "name": "work"}]}},
    )
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.patch(
            "/bills/100",
            json={
                "bill_type": "expense",
                "amount": 2.5,
                "category_id": 11,
                "asset_id": 8,
                "remark": "new",
                "occurred_at": "2026-04-12T19:30:00+08:00",
                "tags": ["work"],
                "images": ["_new.jpg"],
            },
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    updated = client.updated[-1]
    assert updated["id"] == 100
    assert updated["userid"] == "u1"
    assert updated["createtime"] == 11
    assert updated["platform"] == 0
    assert updated["money"] == 2.5
    assert updated["type"] == 0
    assert updated["cateid"] == 11
    assert updated["assetid"] == 8
    assert updated["remark"] == "new"
    assert updated["time"] == 1_775_993_400
    assert updated["images"] == ["_new.jpg"]
    assert updated["extra"]["baoxiaoed"] == 0
    assert updated["extra"]["tags"] == ["work-id"]


def test_update_bill_returns_404_when_original_bill_is_missing():
    client = _BillRouteClient(
        [
            {"data": {"changes": [], "deletes": [], "hasmore": 0}},
        ]
    )
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.patch("/bills/999", json={"bill_type": "expense", "remark": "new"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "bill_not_found"
    assert client.updated == []


def test_delete_bill_accepts_large_id_when_qianji_confirms_delete():
    bill_id = 1781362099609157301
    client = _BillRouteClient([], {"data": {"sync_result": {"bill": {"del_ids": [bill_id], "has_failed": False}}}})
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.delete(f"/bills/{bill_id}", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["ids"]["bill_id"] == str(bill_id)
    assert client.deleted == [([bill_id], "a")]


def test_delete_bill_returns_error_when_qianji_does_not_confirm_delete():
    bill_id = 1781362099609157301
    client = _BillRouteClient([], {"data": {"sync_result": {"bill": {"del_ids": [], "has_failed": False}}}})
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.delete(f"/bills/{bill_id}", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["message"] == f"Qianji did not confirm deletion for bill: {bill_id}"
    assert client.deleted == [([bill_id], "a")]


def test_mirrored_route_missing_required_field_returns_422():
    app = _app_with(_RecordingClient())
    with TestClient(app) as c:
        # /asset/info requires `did`; sending an empty body must not 500
        response = c.post("/asset/info", json={}, headers=AUTH_HEADERS)
    assert response.status_code == 422


def test_generic_passthrough_unknown_endpoint_returns_404():
    app = _app_with(_RecordingClient())
    with TestClient(app) as c:
        response = c.post("/api/nope/missing", json={"params": {}}, headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_generic_passthrough_routes_through_endpoint_registry():
    client = _RecordingClient()
    app = _app_with(client)
    with TestClient(app) as c:
        # bool must be normalized to "1" via the registry, not "True"
        response = c.post("/api/category/listv2", json={"params": {"showhide": True}}, headers=AUTH_HEADERS)
    assert response.status_code == 200
    ctrl, act, params, account_id = client.calls[-1]
    assert (ctrl, act) == ("category", "listv2")
    assert params.get("showhide") == "1"
    assert account_id == "a"


def test_upstream_error_maps_to_502():
    from qianji.errors import UpstreamError, UpstreamTimeoutError

    class _FailingClient(_RecordingClient):
        async def _request(self, ctrl, act, params=None):
            raise UpstreamError(0, "boom")

    app = FastAPI()
    app.state.client = _FailingClient()
    app.include_router(router)
    server_app.app.state.client = _FailingClient()

    # exercise the registered handlers on the real app
    @server_app.app.get("/_raise_upstream")
    async def _raise_upstream():
        raise UpstreamError(502, "upstream down")

    @server_app.app.get("/_raise_timeout")
    async def _raise_timeout():
        raise UpstreamTimeoutError(0, "slow")

    with TestClient(server_app.app) as c:
        assert c.get("/_raise_upstream").status_code == 502
        assert c.get("/_raise_timeout").status_code == 504


# --- MCP auth failure rate limiting (task: no unlimited upstream login brute-force) ---


def test_mcp_auth_failures_are_rate_limited_per_ip():
    async def unauthorized_app(scope, receive, send):
        response = JSONResponse(status_code=401, content={"error": "unauthorized"})
        await response(scope, receive, send)

    app = server_app.RateLimitMiddleware(unauthorized_app)

    c = TestClient(app)
    statuses = [c.post("/mcp", json={}).status_code for _ in range(12)]

    assert statuses[:10] == [401] * 10
    assert statuses[10:] == [429, 429]


def test_mcp_successful_requests_do_not_consume_auth_failure_budget():
    async def ok_app(scope, receive, send):
        response = JSONResponse(status_code=200, content={"ok": True})
        await response(scope, receive, send)

    app = server_app.RateLimitMiddleware(ok_app)

    c = TestClient(app)
    statuses = [c.post("/mcp", json={}).status_code for _ in range(15)]

    assert statuses == [200] * 15


def test_mcp_auth_failure_limiter_window_expires():
    limiter_instance = server_app.MCPAuthFailureLimiter(limit=2, window=60.0)

    assert limiter_instance.allowed("1.2.3.4") is True
    limiter_instance.record("1.2.3.4")
    limiter_instance.record("1.2.3.4")
    assert limiter_instance.allowed("1.2.3.4") is False
    # A different client IP is not affected.
    assert limiter_instance.allowed("5.6.7.8") is True
    # Entries older than the window are pruned.
    limiter_instance._failures["1.2.3.4"] = [0.0]
    assert limiter_instance.allowed("1.2.3.4") is True


# --- TagFeatureDisabledError -> HTTP 409 ---


def test_tag_feature_disabled_handler_returns_409_with_http_hint():
    from qianji.errors import TagFeatureDisabledError

    app = FastAPI()
    app.add_exception_handler(TagFeatureDisabledError, server_app.tag_feature_disabled_handler)

    @app.get("/trigger")
    async def trigger():
        raise TagFeatureDisabledError()

    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.get("/trigger")

    assert response.status_code == 409
    body = response.json()
    assert body["ec"] == 409
    assert "PUT /tags/enabled" in body["em"]


def test_write_http_result_maps_tag_feature_disabled_to_409():
    from qianji.errors import TagFeatureDisabledError
    from qianji.server import routes as routes_module

    async def boom():
        raise TagFeatureDisabledError()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(routes_module._write_http_result(boom()))

    assert exc_info.value.status_code == 409
    assert "PUT /tags/enabled" in exc_info.value.detail


# --- Invalid pagination cursors -> HTTP 422 ---


def test_get_bills_rejects_invalid_cursor_without_sync():
    client = _BillRouteClient([])
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.get("/bills", params={"cursor": "!!not-a-cursor!!"}, headers=AUTH_HEADERS)

    assert response.status_code == 422
    assert "cursor" in response.json()["detail"].lower()
    assert client.sync_calls == []


def test_get_bills_range_rejects_invalid_cursor_without_sync():
    client = _BillRouteClient([])
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.get(
            "/bills/range",
            params={"start_time": 0, "end_time": 100, "cursor": "!!not-a-cursor!!"},
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 422
    assert "cursor" in response.json()["detail"].lower()
    assert client.sync_calls == []


# --- REST auth failure rate limiting, error mapping, and hardening ---


def test_rest_auth_failures_are_rate_limited_per_ip(monkeypatch):
    from qianji.server import routes as routes_module
    from qianji.server.security import RestAuthFailureLimiter

    class RejectingClient(FakeRouteClient):
        async def login(self, email, password, account_id=None):
            raise AuthenticationError(1, "bad credentials")

    monkeypatch.setattr(routes_module, "auth_failure_limiter", RestAuthFailureLimiter(limit=3, window=60.0))
    app = FastAPI()
    app.state.client = RejectingClient()
    app.include_router(router)

    with TestClient(app) as c:
        statuses = [c.get("/assets", headers=_auth_headers(pwd_hash=WRONG_PWD_HASH)).status_code for _ in range(5)]

    assert statuses == [401, 401, 401, 429, 429]


def test_auth_failure_limiter_prunes_empty_ip_buckets():
    from qianji.server.security import AuthFailureLimiter

    instance = AuthFailureLimiter(limit=1, window=60.0)
    instance.record("1.2.3.4")
    assert "1.2.3.4" in instance._failures
    instance._failures["1.2.3.4"] = [0.0]
    assert instance.allowed("1.2.3.4") is True
    assert "1.2.3.4" not in instance._failures


def test_require_api_auth_maps_upstream_errors():
    from qianji.errors import UpstreamError, UpstreamTimeoutError

    class TimeoutClient(FakeRouteClient):
        async def login(self, email, password, account_id=None):
            raise UpstreamTimeoutError(0, "slow")

    class UpstreamFailClient(FakeRouteClient):
        async def login(self, email, password, account_id=None):
            raise UpstreamError(502, "upstream down")

    class RejectingClient(FakeRouteClient):
        async def login(self, email, password, account_id=None):
            raise AuthenticationError(1, "bad credentials")

    results = {}
    for name, client in (
        ("timeout", TimeoutClient()),
        ("upstream", UpstreamFailClient()),
        ("auth", RejectingClient()),
    ):
        app = FastAPI()
        app.state.client = client
        app.include_router(router)
        with TestClient(app) as c:
            results[name] = c.get("/assets", headers=AUTH_HEADERS).status_code

    assert results == {"timeout": 504, "upstream": 502, "auth": 401}


def test_patch_bill_rejects_unknown_bill_type_with_422():
    client = _BillRouteClient([])
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.patch("/bills/100", json={"bill_type": "weird", "remark": "x"}, headers=AUTH_HEADERS)

    assert response.status_code == 422
    assert "bill_type" in response.json()["detail"]


def test_auth_login_rechecks_password_against_bearer_token():
    limiter.reset()
    app = FastAPI()
    app.state.client = FakeRouteClient()
    app.include_router(router)
    try:
        with TestClient(app) as c:
            ok = c.post("/auth/login", json={"email": "a", "password": "pwd"}, headers=AUTH_HEADERS)
            bad = c.post("/auth/login", json={"email": "a", "password": "wrong"}, headers=AUTH_HEADERS)
            skipped = c.post("/auth/login", json={"email": "a"}, headers=AUTH_HEADERS)
    finally:
        limiter.reset()

    assert ok.status_code == 200
    assert bad.status_code == 401
    assert skipped.status_code == 200


def test_switch_account_sets_process_current_account():
    class SwitchClient(FakeRouteClient):
        def __init__(self):
            super().__init__()
            self.set_account_calls = []

        def set_account(self, account_id):
            self.set_account_calls.append(account_id)
            self.default_account = account_id

    client = SwitchClient()
    app = _app_with(client)

    with TestClient(app) as c:
        response = c.post("/auth/switch", params={"account_id": "a"}, headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["switched_to"] == "a"
    assert "note" in response.json()
    assert client.set_account_calls == ["a"]


def test_export_bills_prefers_public_base_url(monkeypatch):
    monkeypatch.setenv("QIANJI_PUBLIC_BASE_URL", "https://bills.example.com/")
    bills = [
        {
            "id": 1,
            "userid": "u1",
            "bookid": -1,
            "type": 0,
            "money": 12.5,
            "cateid": 10,
            "time": 100,
            "createtime": 100,
            "updatetime": 100,
            "remark": "coffee",
            "status": 1,
            "assetid": 3,
            "fromid": -1,
            "targetid": -1,
            "extra": {"tags": ["food"]},
        },
    ]
    client = _BillRouteClient([{"data": {"changes": bills, "deletes": [], "hasmore": 0}}])
    app = _app_with(client)

    with TestClient(app, base_url="https://api.example") as c:
        exported = c.post("/bills/export", json={"format": "csv"}, headers=AUTH_HEADERS)

    assert exported.status_code == 200
    assert exported.json()["download_url"].startswith("https://bills.example.com/files/")


def test_bill_search_request_normalizes_string_bill_id():
    from pydantic import ValidationError

    from qianji.server.routes import BillSearchRequest

    assert BillSearchRequest(bill_id="1781362099609157301").bill_id == 1781362099609157301
    assert BillSearchRequest(bill_id=42).bill_id == 42
    assert BillSearchRequest().bill_id is None
    with pytest.raises(ValidationError):
        BillSearchRequest(bill_id="not-a-number")


def test_security_headers_applied_to_pages_and_api():
    with TestClient(server_app.app) as c:
        login = c.get("/login")
        guide = c.get("/mcp-config")
        health = c.get("/healthz")
        base_css = c.get("/static/base.css")
        console_css = c.get("/static/console/console.css")
        console_js = c.get("/static/console/console.js")

    assert login.status_code == 200
    assert login.headers["x-content-type-options"] == "nosniff"
    assert login.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in login.headers["content-security-policy"]
    assert guide.status_code == 200
    assert "qianji.api.credentials" in guide.text
    assert guide.headers["cache-control"] == "no-store"
    assert health.status_code == 200
    assert health.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in health.headers
    assert "cache-control" not in health.headers
    assert base_css.status_code == 200
    # The console moved its styles and behavior into same-origin static files;
    # CSP is only injected into text/html responses, so these rely on the page's
    # 'self' allowance for script-src/style-src.
    assert console_css.status_code == 200
    assert console_css.headers["content-type"].startswith("text/css")
    assert console_js.status_code == 200
    # mimetypes 对 .js 的映射随 Python/平台不同：application/javascript 或 text/javascript。
    assert console_js.headers["content-type"].startswith(("application/javascript", "text/javascript"))
