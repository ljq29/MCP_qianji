from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from slowapi.errors import RateLimitExceeded
from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from qianji import mcp_server
from qianji.auth_token import format_qianji_auth_token, parse_qianji_auth_token
from qianji.client import QianjiClient
from qianji.errors import (
    TAG_FEATURE_DISABLED_HTTP_MESSAGE,
    AuthenticationError,
    NotLoggedInError,
    QianjiError,
    TagFeatureDisabledError,
    UpstreamError,
    UpstreamTimeoutError,
)
from qianji.file_service import initialize_file_retention_config
from qianji.server.pages import (
    inject_docs_nav,
    render_about_page,
    render_account_page,
    render_login_page,
    render_mcp_config_guide_page,
    render_mcp_config_page,
)
from qianji.server.security import AuthFailureLimiter, MCPAuthFailureLimiter
from qianji.server.security import mask_secret_identifier as _mask_secret_identifier
from qianji.version import __version__

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
DOCS_STATIC_URL = "/static/docs"
AUTH_SCHEME = "bearer "


class MCPASGIWrapper:
    def __init__(self) -> None:
        self.app: Any | None = None

    def set_app(self, app: Any | None) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if self.app is None:
            response = PlainTextResponse("MCP server is not ready", status_code=503)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


mcp_asgi = MCPASGIWrapper()


def _mcp_path() -> str:
    path = os.environ.get("MCP_PATH", "/mcp")
    if not path.startswith("/"):
        path = "/" + path
    return path


def _is_mcp_path(path: str) -> bool:
    mcp_path = _mcp_path().rstrip("/") or "/"
    return path == mcp_path or path.startswith(f"{mcp_path}/")


class RateLimitMiddleware:
    # slowapi 的 ASGI 中间件依赖在 app.routes 上逐个 matches() 解析 endpoint，
    # fastapi 0.140 的 lazy include（_IncludedRouter）会让该解析静默失效，
    # 因此全局限流改为自建滑动窗口计数，不再依赖路由解析。
    GLOBAL_RATE_LIMIT = 60
    GLOBAL_RATE_WINDOW = 60.0
    # A single console page load requests dozens of stylesheets, scripts and
    # category icons. Counting those against the same budget as the API calls
    # the page actually needs would make the console throttle itself.
    EXEMPT_PREFIXES = ("/static/", "/category-icons/")

    def __init__(self, app):
        self.app = app
        self._global_limiter = AuthFailureLimiter(self.GLOBAL_RATE_LIMIT, self.GLOBAL_RATE_WINDOW)
        self._mcp_auth_limiter = MCPAuthFailureLimiter()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if _is_mcp_path(path):
            await self._handle_mcp(scope, receive, send)
            return
        if path.startswith(self.EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        key = client[0] if client else "unknown"
        if not self._global_limiter.allowed(key):
            logger.warning("Rate limit exceeded: %s %s from %s", scope.get("method"), scope.get("path"), key)
            response = JSONResponse(status_code=429, content={"ec": 429, "em": "Rate limit exceeded"})
            await response(scope, receive, send)
            return
        self._global_limiter.record(key)
        await self.app(scope, receive, send)

    async def _handle_mcp(self, scope, receive, send):
        # The MCP sub-app verifies Bearer tokens against the upstream login
        # endpoint, so failed attempts must be throttled per client IP to avoid
        # becoming an open relay for upstream credential brute-forcing.
        client = scope.get("client")
        key = client[0] if client else "unknown"
        if not self._mcp_auth_limiter.allowed(key):
            logger.warning("MCP auth failure rate limit exceeded for %s", key)
            response = JSONResponse(
                status_code=429,
                content={"ec": 429, "em": "Too many failed authentication attempts"},
            )
            await response(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and message["status"] in (401, 403):
                self._mcp_auth_limiter.record(key)
            await send(message)

        await self.app(scope, receive, send_wrapper)


CSP_HEADER_VALUE = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'"
)


class SecurityHeadersMiddleware:
    """Baseline security headers for every HTTP response.

    HTML pages carry a permissive CSP (the server-rendered pages rely on
    inline scripts/styles); auth-related responses are marked no-store.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        no_store = path == "/mcp-config" or path.startswith("/auth/") or _is_mcp_path(path)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                if "x-content-type-options" not in headers:
                    headers["x-content-type-options"] = "nosniff"
                if "x-frame-options" not in headers:
                    headers["x-frame-options"] = "DENY"
                if no_store and "cache-control" not in headers:
                    headers["cache-control"] = "no-store"
                content_type = headers.get("content-type", "")
                if "content-security-policy" not in headers and content_type.startswith("text/html"):
                    headers["content-security-policy"] = CSP_HEADER_VALUE
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _build_mcp_app():
    mcp_server.configure_mcp_tools()
    mcp_server.mcp.settings.streamable_http_path = _mcp_path()
    mcp_server.mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    return mcp_server.mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    project_root = Path(__file__).resolve().parent.parent.parent
    db_path = Path(os.environ.get("QIANJI_DB_PATH", str(project_root / ".qianji.db")))
    client = QianjiClient(db_path=db_path)
    old_mcp_client = mcp_server._client
    old_json_dir = project_root / ".qianji_sessions"
    if old_json_dir.exists():
        migrated = await client._session_mgr.migrate_from_json(old_json_dir)
        if migrated:
            await client._load_all_sessions()
    await initialize_file_retention_config(client._session_mgr)
    await client._session_mgr.delete_expired_generated_files()
    mcp_server.set_client(client)
    mcp_app = _build_mcp_app()
    mcp_asgi.set_app(mcp_app)
    try:
        async with client:
            async with mcp_server.mcp.session_manager.run():
                app.state.client = client
                app.state.file_session_mgr = client._session_mgr
                # Effective bind address, written back by the CLI entry point;
                # used to build local URLs (e.g. the MCP URL on /about).
                app.state.server_host = os.environ.get("QIANJI_HOST", "127.0.0.1")
                app.state.server_port = int(os.environ.get("QIANJI_PORT", "50003"))
                try:
                    yield
                finally:
                    mcp_asgi.set_app(None)
    finally:
        mcp_server.set_client(old_mcp_client)
        mcp_server.reset_streamable_http_session_manager()


app = FastAPI(
    title="Qianji API",
    description="钱迹记账 API 代理服务 — 逆向工程实现",
    version=__version__,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(NotLoggedInError)
async def not_logged_in_handler(request, exc):
    logger.warning("Not logged in: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=403, content={"ec": 0, "em": "Not logged in. POST /auth/login first."})


@app.exception_handler(AuthenticationError)
async def auth_error_handler(request, exc):
    logger.warning("Authentication error [%s]: %s %s — %s", exc.ec, request.method, request.url.path, exc.em)
    return JSONResponse(status_code=401, content={"ec": exc.ec, "em": exc.em})


@app.exception_handler(UpstreamTimeoutError)
async def upstream_timeout_handler(request, exc):
    logger.error("Upstream timeout: %s %s — %s", request.method, request.url.path, exc.em)
    return JSONResponse(status_code=504, content={"ec": exc.ec, "em": exc.em})


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request, exc):
    logger.error("Upstream error [%s]: %s %s — %s", exc.ec, request.method, request.url.path, exc.em)
    return JSONResponse(status_code=502, content={"ec": exc.ec, "em": exc.em})


@app.exception_handler(TagFeatureDisabledError)
async def tag_feature_disabled_handler(request, exc):
    logger.warning("Tag feature disabled: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=409, content={"ec": exc.ec, "em": TAG_FEATURE_DISABLED_HTTP_MESSAGE})


@app.exception_handler(QianjiError)
async def qianji_error_handler(request, exc):
    logger.error("Qianji error [%s]: %s %s — %s", exc.ec, request.method, request.url.path, exc.em)
    return JSONResponse(status_code=502, content={"ec": exc.ec, "em": exc.em, "data": exc.data})


def _local_mcp_url(request: Request) -> str:
    host = request.url.hostname or "localhost"
    if host in {"0.0.0.0", "::", "::1"}:
        host = "localhost"
    app_state = getattr(request.scope.get("app"), "state", None)
    port = getattr(app_state, "server_port", None) or os.environ.get("QIANJI_PORT", "50003")
    return f"http://{host}:{port}{_mcp_path()}"


def _agent_mcp_url(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host")
    host = (forwarded_host or request.headers.get("host", request.url.netloc)).split(",", 1)[0].strip()
    hostname = host.rsplit(":", 1)[0].strip("[]").lower()
    if forwarded_host or hostname not in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}:
        return _external_url(request, os.environ.get("MCP_PATH", "/mcp"))
    return _local_mcp_url(request)


async def _mcp_auth_header_from_request(request: Request, client: QianjiClient) -> tuple[str, str, bool]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(AUTH_SCHEME):
        raise HTTPException(status_code=401, detail="Authentication required")

    credentials = parse_qianji_auth_token(header[len(AUTH_SCHEME) :])
    if credentials is None:
        logger.warning("Invalid MCP config auth token from %s", request.client.host if request.client else "unknown")
        raise HTTPException(status_code=401, detail="Invalid Authorization token")

    try:
        await client.login_md5(credentials.account, credentials.pwd_hash, account_id=credentials.account)
    except QianjiError as exc:
        logger.warning("MCP config auth credentials rejected for %s: %s", _mask_secret_identifier(credentials.account), exc)
        raise HTTPException(status_code=401, detail="Invalid Qianji credentials") from exc

    token = format_qianji_auth_token(credentials.account, credentials.pwd_hash, b64=True)
    return f"Bearer {token}", credentials.account, True


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def homepage(request: Request):
    return render_login_page(mcp_path=_mcp_path())


@app.get("/account", response_class=HTMLResponse, include_in_schema=False)
async def account_page(request: Request):
    return render_account_page(mcp_path=_mcp_path())


@app.get("/mcp-config", response_class=HTMLResponse, include_in_schema=False)
async def mcp_config_page(request: Request):
    if not request.headers.get("authorization", "").lower().startswith(AUTH_SCHEME):
        # Browser visits (e.g. the console nav link) carry no Authorization
        # header; serve a bootstrap page that re-fetches with the credentials
        # the console stored in local/session storage instead of a bare 401.
        return render_mcp_config_guide_page()
    client: QianjiClient = app.state.client
    auth_header, account_label, has_credentials = await _mcp_auth_header_from_request(request, client)
    return render_mcp_config_page(
        agent_mcp_url=_agent_mcp_url(request),
        auth_header=auth_header,
        account_label=account_label,
        has_credentials=has_credentials,
    )


@app.get("/about", response_class=HTMLResponse, include_in_schema=False)
async def about_page(request: Request):
    client: QianjiClient = app.state.client
    accounts = client.list_accounts()
    if accounts:
        rows = "".join(
            f"<tr><td>{'<b>' + escape(_mask_secret_identifier(a['account_id'])) + '</b>' if a['is_current'] else escape(_mask_secret_identifier(a['account_id']))}</td>"
            f"<td>{escape(_mask_secret_identifier(a['user_name']))}</td><td>{escape(_mask_secret_identifier(a['email']))}</td><td>{escape(str(a['devname']))}</td></tr>"
            for a in accounts
        )
        status_class = "ok"
        status_msg = (
            f"{len(accounts)} account(s) logged in. Current: <b>{escape(_mask_secret_identifier(client.current_account))}</b>"
            f"<br><table><tr><th>Account ID</th><th>Name</th><th>Email</th><th>Device</th></tr>{rows}</table>"
        )
    else:
        status_class = "warn"
        status_msg = 'No accounts logged in. Use <a href="/account">the account page</a> or authenticated API calls to connect.'
    raw_mcp_mode = os.environ.get(mcp_server.MCP_MODE_ENV)
    try:
        mcp_mode = mcp_server.get_mcp_mode(raw_mcp_mode)
    except ValueError:
        mcp_mode = f"{str(raw_mcp_mode or '').strip().lower()} (invalid)"
    return render_about_page(
        status_class=status_class,
        status_msg=status_msg,
        mcp_mode=mcp_mode,
        agent_mcp_url=_agent_mcp_url(request),
    )


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def swagger_docs():
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title="Qianji API Docs",
        swagger_js_url=f"{DOCS_STATIC_URL}/swagger-ui-bundle.js",
        swagger_css_url=f"{DOCS_STATIC_URL}/swagger-ui.css",
        swagger_favicon_url=f"{DOCS_STATIC_URL}/favicon.svg",
        swagger_ui_parameters={"persistAuthorization": True},
    )
    return inject_docs_nav(response.body.decode("utf-8"), active="docs")


@app.get("/redoc", response_class=HTMLResponse, include_in_schema=False)
async def redoc_docs():
    response = get_redoc_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title="Qianji API ReDoc",
        redoc_js_url=f"{DOCS_STATIC_URL}/redoc.standalone.js",
        redoc_favicon_url=f"{DOCS_STATIC_URL}/favicon.svg",
        with_google_fonts=False,
    )
    return inject_docs_nav(response.body.decode("utf-8"), active="redoc")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse(url=f"{DOCS_STATIC_URL}/favicon.svg")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}


from qianji.server.routes import _external_url, limiter, public_router, router  # noqa: E402

app.state.limiter = limiter
app.include_router(public_router)
app.include_router(router)
app.router.routes.append(Route(_mcp_path(), mcp_asgi, methods=["GET", "POST", "DELETE"], name="mcp"))


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    client_host = request.client.host if request.client else "unknown"
    logger.warning("Rate limit exceeded: %s %s from %s", request.method, request.url.path, client_host)
    return JSONResponse(status_code=429, content={"ec": 429, "em": "Rate limit exceeded"})
