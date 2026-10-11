from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import httpx

from qianji.api.account import AccountAPI
from qianji.api.asset import AssetAPI
from qianji.api.bill import BillAPI
from qianji.api.budget import BudgetAPI
from qianji.api.category import CategoryAPI
from qianji.api.debtloan import DebtLoanAPI
from qianji.api.refund import RefundAPI
from qianji.api.reimbursement import ReimbursementAPI
from qianji.api.sync import SyncAPI
from qianji.api.tag import TagAPI
from qianji.api.upload import UploadAPI
from qianji.api.user import UserAPI
from qianji.api.usercard import UserCardAPI
from qianji.constants import (
    BASE_URL,
    DEFAULT_CLANG,
    DEFAULT_CREGION,
    DEFAULT_DEVBRAND,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_KEEPALIVE,
    DEFAULT_MK,
    DEFAULT_OS,
    DEFAULT_PKG,
    DEFAULT_TIMEOUT,
    DEFAULT_TIMEZONE_OFFSET,
    DEFAULT_VS,
    DEFAULT_VSN,
    EC_AUTH_EXPIRED,
    EC_SIGNATURE_MISMATCH,
)
from qianji.endpoints import install_endpoint_methods
from qianji.errors import (
    AuthenticationError,
    NotLoggedInError,
    QianjiError,
    SignatureError,
    UpstreamError,
    UpstreamTimeoutError,
)
from qianji.session import SessionData, SessionManager
from qianji.sign import generate_sign, hash_password, is_md5_hash
from qianji.tags import TAG_ENABLED_CONFIG_KEY, tags_flag_enabled

logger = logging.getLogger(__name__)

SESSION_MAX_AGE_DAYS_ENV = "QIANJI_SESSION_MAX_AGE_DAYS"
DEFAULT_SESSION_MAX_AGE_DAYS = 30.0


def _session_max_age_seconds() -> float:
    raw = os.environ.get(SESSION_MAX_AGE_DAYS_ENV, "")
    try:
        days = float(raw) if raw.strip() else DEFAULT_SESSION_MAX_AGE_DAYS
    except ValueError:
        days = DEFAULT_SESSION_MAX_AGE_DAYS
    return max(0.0, days) * 86400


def _session_exceeds_max_age(session: SessionData) -> bool:
    max_age = _session_max_age_seconds()
    if max_age <= 0:
        return False
    login_time = float(session.login_time or 0)
    if login_time <= 0:
        return False
    return (time.time() - login_time) > max_age


class QianjiClient(
    AccountAPI,
    AssetAPI,
    BillAPI,
    RefundAPI,
    ReimbursementAPI,
    DebtLoanAPI,
    CategoryAPI,
    BudgetAPI,
    SyncAPI,
    TagAPI,
    UploadAPI,
    UserCardAPI,
    UserAPI,
):
    def __init__(
        self,
        db_path: Path | str = ".qianji.db",
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_keepalive: int = DEFAULT_MAX_KEEPALIVE,
    ):
        self._base_url = base_url.rstrip("/")
        self._session_mgr = SessionManager(db_path)
        self._sessions: dict[str, SessionData] = {}
        self._current_account: str | None = None
        self._login_locks: dict[str, asyncio.Lock] = {}
        self._bill_sync_locks: dict[str, asyncio.Lock] = {}
        self._account_override: ContextVar[str | None] = ContextVar(
            f"qianji_account_override_{id(self)}",
            default=None,
        )
        self._timeout = timeout
        self._limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        )
        self._http: httpx.AsyncClient | None = None

    async def _load_all_sessions(self) -> None:
        self._sessions = await self._session_mgr.load_all()
        if self._sessions and not self._current_account:
            self._current_account = next(iter(self._sessions))

    async def __aenter__(self) -> QianjiClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout, limits=self._limits)
        await self._load_all_sessions()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
        await self._session_mgr.close()

    # ---- Account management ----

    def _effective_account_id(self) -> str | None:
        return self._account_override.get() or self._current_account

    def _account_not_found_error(self, account_id: str) -> QianjiError:
        return QianjiError(0, f"Account '{account_id}' not found. Available: {list(self._sessions.keys())}")

    def _login_lock_for(self, email: str, account_id: str | None = None) -> asyncio.Lock:
        # The lock key must be a stable identity: account_id can be rewritten
        # during login (existing sessions are matched by credentials/email), so
        # email is the only reliable key. Two logins for the same email always
        # serialize on the same lock regardless of the account_id passed in.
        key = email or account_id or "__default__"
        lock = self._login_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._login_locks[key] = lock
        return lock

    def _release_login_lock(self, email: str, account_id: str | None = None) -> None:
        key = email or account_id or "__default__"
        lock = self._login_locks.get(key)
        if lock is not None and not lock.locked():
            self._login_locks.pop(key, None)

    def _bill_sync_lock_for(self, account_id: str) -> asyncio.Lock:
        lock = self._bill_sync_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._bill_sync_locks[account_id] = lock
        return lock

    @contextmanager
    def use_account(self, account_id: str | None) -> Iterator[None]:
        if not account_id:
            yield
            return
        if account_id not in self._sessions:
            raise self._account_not_found_error(account_id)
        token = self._account_override.set(account_id)
        try:
            yield
        finally:
            self._account_override.reset(token)

    @property
    def current_account(self) -> str | None:
        return self._effective_account_id()

    def set_account(self, account_id: str) -> None:
        if account_id not in self._sessions:
            raise self._account_not_found_error(account_id)
        self._current_account = account_id

    def list_accounts(self) -> list[dict]:
        current_account = self._effective_account_id()
        result = []
        for aid, s in self._sessions.items():
            tagon = s.user_configs.get(TAG_ENABLED_CONFIG_KEY)
            result.append(
                {
                    "account_id": aid,
                    "user_name": s.user_name,
                    "email": s.email,
                    "devname": s.devname,
                    "is_current": aid == current_account,
                    "tags_enabled": tags_flag_enabled(tagon),
                }
            )
        return result

    @property
    def is_logged_in(self) -> bool:
        account_id = self._effective_account_id()
        if not account_id:
            return False
        s = self._sessions.get(account_id)
        return s is not None and bool(s.utoken)

    @property
    def _user_id(self) -> str:
        return self._current_session.user_id

    @property
    def session(self) -> SessionData | None:
        return self._sessions.get(self._effective_account_id() or "")

    @property
    def _current_session(self) -> SessionData:
        _, session = self._effective_session()
        return session

    def _effective_session(self) -> tuple[str, SessionData]:
        account_id = self._effective_account_id()
        if not account_id or account_id not in self._sessions:
            raise NotLoggedInError()
        return account_id, self._sessions[account_id]

    # ---- Login / Logout ----

    async def login(self, email: str, password: str, account_id: str | None = None) -> dict:
        try:
            async with self._login_lock_for(email, account_id):
                return await self._login_with_pwd_hash(email, hash_password(password), account_id=account_id)
        finally:
            self._release_login_lock(email, account_id)

    async def login_md5(self, email: str, pwd_hash: str, account_id: str | None = None) -> dict:
        if not is_md5_hash(pwd_hash):
            raise ValueError("Qianji login_md5 requires a 32-character MD5 password hash")
        try:
            async with self._login_lock_for(email, account_id):
                return await self._login_with_pwd_hash(email, pwd_hash.lower(), account_id=account_id)
        finally:
            self._release_login_lock(email, account_id)

    async def _login_with_pwd_hash(self, email: str, pwd_hash: str, account_id: str | None = None) -> dict:
        existing = await self._session_mgr.find_by_credentials(email, pwd_hash)
        if existing and existing.utoken and not _session_exceeds_max_age(existing):
            logger.debug("Reusing existing session for %s (account_id=%s)", email, existing.account_id)
            self._sessions[existing.account_id] = existing
            self._current_account = existing.account_id
            return {
                "ec": 200,
                "em": "Session reused (credentials match)",
                "data": {
                    "user": {"id": existing.user_id, "name": existing.user_name, "email": existing.email},
                    "token": existing.utoken,
                },
            }

        if existing:
            account_id = existing.account_id
            devid, devname = existing.devid, existing.devname
        else:
            email_session = await self._session_mgr.find_by_email(email)
            migrated = None if email_session else await self._session_mgr.find_migrated_entry(email)
            if email_session:
                if account_id is None:
                    account_id = email_session.account_id
                devid, devname = email_session.devid, email_session.devname
            elif migrated:
                account_id = migrated.account_id
                devid, devname = migrated.devid, migrated.devname
            elif account_id is None:
                account_id = await self._session_mgr.generate_account_id(email)
                devid, devname = await self._session_mgr.get_or_create_device_identity(account_id)
            else:
                devid, devname = await self._session_mgr.get_or_create_device_identity(account_id)

        result = await self._request_raw(
            "account",
            "login",
            {"v": email, "pwd": pwd_hash},
            utoken="",
            devid=devid,
            devname=devname,
            user_id="",
        )
        data = result["data"]
        session = SessionData(
            account_id=account_id,
            email=email,
            pwd_hash=pwd_hash,
            utoken=data["token"],
            user_id=data["user"]["id"],
            user_name=data["user"].get("name", ""),
            devid=devid,
            devname=devname,
            books=data.get("books", []),
            user_configs=data.get("userconfigs", {}) if isinstance(data.get("userconfigs"), dict) else {},
            login_time=time.time(),
            vip_type=data["user"].get("viptype", 0),
            vip_end=data["user"].get("vipend", 0),
        )
        self._sessions[account_id] = session
        await self._session_mgr.save(session)
        self._current_account = account_id
        return result

    async def refresh_user_configs(self) -> dict[str, object]:
        account_id, session = self._effective_session()

        result = await self._request(
            "client",
            "init",
            {
                "v": 0,
                "vvmark": 1 if session.vip_type else 0,
                "newinstall": 0,
                "upgradealert": 0,
            },
        )
        data = result.get("data", {})
        if not isinstance(data, dict):
            raise QianjiError(502, "Qianji client init response did not include config data")

        user = data.get("userinfo") or data.get("user")
        if isinstance(user, dict):
            user_id = user.get("id")
            if user_id:
                session.user_id = str(user_id)
            session.user_name = str(user.get("name") or session.user_name)

        books = data.get("books")
        if isinstance(books, list):
            session.books = books

        user_configs = data.get("userconfigs")
        if isinstance(user_configs, dict):
            session.user_configs.update(user_configs)

        session.login_time = time.time()
        self._sessions[account_id] = session
        await self._session_mgr.save(session)
        return session.user_configs

    async def logout(self, account_id: str | None = None) -> dict:
        aid = account_id or self._effective_account_id()
        if not aid or aid not in self._sessions:
            raise NotLoggedInError()
        s = self._sessions[aid]
        try:
            result = await self._request_raw(
                "account",
                "logout",
                utoken=s.utoken,
                devid=s.devid,
                devname=s.devname,
                user_id=s.user_id,
            )
        except QianjiError as exc:
            logger.warning("Remote logout failed for account %s, proceeding with local cleanup: %s", aid, exc)
            result = {"ec": 200, "em": "Logged out locally"}
        self._sessions.pop(aid, None)
        await self._session_mgr.delete(aid)
        if self._current_account == aid:
            self._current_account = next(iter(self._sessions), None)
        return result

    # ---- Core request ----

    async def _request_public(self, ctrl: str, act: str, params: dict[str, Any] | None = None) -> dict:
        params = params or {}
        identity = str(params.get("v") or params.get("account") or self._current_account or "__public__")
        devid, devname = await self._session_mgr.get_or_create_device_identity(identity)
        return await self._request_raw(
            ctrl,
            act,
            params,
            utoken="",
            devid=devid,
            devname=devname,
            user_id="",
        )

    async def _request(self, ctrl: str, act: str, params: dict[str, Any] | None = None) -> dict:
        account_id, s = self._effective_session()
        try:
            return await self._request_raw(
                ctrl,
                act,
                params or {},
                utoken=s.utoken,
                devid=s.devid,
                devname=s.devname,
                user_id=s.user_id,
            )
        except AuthenticationError:
            logger.debug("Auth recovery triggered for account %s on %s/%s", account_id, ctrl, act)
            recovered = await self._recover_auth(account_id, s)
            if not recovered:
                raise
            s = self._sessions[account_id]
            return await self._request_raw(
                ctrl,
                act,
                params or {},
                utoken=s.utoken,
                devid=s.devid,
                devname=s.devname,
                user_id=s.user_id,
            )

    async def _recover_auth(self, account_id: str, session: SessionData) -> bool:
        # Serialize recovery per account identity (email): concurrent requests
        # that all hit 401/8888 must not trigger parallel refresh/relogin calls.
        failed_token = session.utoken
        async with self._login_lock_for(session.email, account_id):
            current = self._sessions.get(account_id)
            if current is None:
                # The account was logged out while we waited on the lock; do not
                # resurrect it with a stale-session relogin.
                return False
            if current.utoken != failed_token:
                # Another coroutine already recovered while we waited on the lock.
                return True
            if await self._try_refresh_token(account_id, session):
                return True
            return await self._try_relogin(account_id, session)

    async def _try_refresh_token(self, account_id: str, session: SessionData) -> bool:
        try:
            result = await self._request_raw(
                "account",
                "refreshtoken",
                {"uid": session.user_id},
                utoken=session.utoken,
                devid=session.devid,
                devname=session.devname,
                user_id=session.user_id,
            )
            new_token = result.get("data", {}).get("token")
            if new_token:
                session.utoken = new_token
                await self._session_mgr.update_token(account_id, new_token)
                logger.debug("Token refreshed for account %s", account_id)
                return True
        except QianjiError as exc:
            logger.warning("Token refresh failed for account %s: %s", account_id, exc)
        return False

    async def _try_relogin(self, account_id: str, session: SessionData) -> bool:
        if not session.pwd_hash:
            return False
        try:
            result = await self._request_raw(
                "account",
                "login",
                {"v": session.email, "pwd": session.pwd_hash},
                utoken="",
                devid=session.devid,
                devname=session.devname,
                user_id="",
            )
            new_token = result.get("data", {}).get("token")
            if new_token:
                session.utoken = new_token
                session.login_time = time.time()
                self._sessions[account_id] = session
                await self._session_mgr.save(session)
                logger.debug("Re-login succeeded for account %s", account_id)
                return True
        except QianjiError as exc:
            logger.warning("Re-login failed for account %s: %s", account_id, exc)
        return False

    # ---- Raw request ----

    async def _request_raw(
        self,
        ctrl: str,
        act: str,
        params: dict[str, Any] | None = None,
        *,
        utoken: str,
        devid: str,
        devname: str,
        user_id: str,
    ) -> dict:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout, limits=self._limits)

        reqidv2, tok = generate_sign(ctrl, act)
        headers = {
            "ctrl": ctrl,
            "act": act,
            "utoken": utoken,
            "tok": tok,
            "reqidv2": reqidv2,
            "mk": DEFAULT_MK,
            "vsn": DEFAULT_VSN,
            "vs": DEFAULT_VS,
            "pkg": DEFAULT_PKG,
            "os": DEFAULT_OS,
            "devid": devid,
            "devname": devname,
            "devbrand": DEFAULT_DEVBRAND,
            "clang": DEFAULT_CLANG,
            "cregion": DEFAULT_CREGION,
            "timezoneoffset": DEFAULT_TIMEZONE_OFFSET,
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
        }

        body_params: dict[str, str] = {}
        if params:
            for k, v in params.items():
                if isinstance(v, dict | list):
                    body_params[k] = json.dumps(v, ensure_ascii=False)
                else:
                    body_params[k] = str(v)

        if utoken and user_id:
            body_params.setdefault("uid", user_id)
            body_params.setdefault("fr", user_id)

        url = f"{self._base_url}/{ctrl}/{act}"
        try:
            resp = await self._http.post(url, data=body_params, headers=headers)
            resp.raise_for_status()
            body = resp.json()
        except httpx.TimeoutException as exc:
            logger.error("Upstream timeout calling %s/%s: %s", ctrl, act, exc)
            raise UpstreamTimeoutError(0, f"Upstream timeout calling {ctrl}/{act}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            logger.error("Upstream HTTP %s calling %s/%s", exc.response.status_code, ctrl, act)
            raise UpstreamError(
                exc.response.status_code, f"Upstream HTTP {exc.response.status_code} calling {ctrl}/{act}"
            ) from exc
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.error("Upstream request failed calling %s/%s: %s", ctrl, act, exc)
            raise UpstreamError(0, f"Upstream request failed calling {ctrl}/{act}: {exc}") from exc

        ec = body.get("ec", -1)
        em = body.get("em", "")
        if ec == EC_SIGNATURE_MISMATCH:
            raise SignatureError(ec, em, body.get("data"))
        if ec == EC_AUTH_EXPIRED:
            raise AuthenticationError(ec, em, body.get("data"))
        if ec != 200:
            raise QianjiError(ec, em, body.get("data"))

        return body


install_endpoint_methods(QianjiClient)
