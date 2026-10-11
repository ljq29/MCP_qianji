from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from qianji.auth_token import parse_qianji_auth_token
from qianji.bill_files import export_bills_to_file, generate_report_image_file
from qianji.bill_id import parse_bill_id, stringify_bill_payload_ids
from qianji.bill_ops import DEFAULT_LIST_LIMIT, MAX_LIST_LIMIT
from qianji.bill_ops import get_bills_range as _get_bills_range
from qianji.bill_ops import list_recent_bills as _list_recent_bills
from qianji.bill_ops import search_bills as _search_bills
from qianji.bill_ops import sync_bills_to_cache as _sync_bills_to_cache
from qianji.constants import LoanType
from qianji.endpoints import (
    ENDPOINT_BY_PATH,
    ENDPOINTS,
    EndpointSpec,
    call_endpoint,
    endpoint_request_model,
    params_from_model,
)
from qianji.errors import (
    TAG_FEATURE_DISABLED_HTTP_MESSAGE,
    AuthenticationError,
    QianjiError,
    TagFeatureDisabledError,
    UpstreamTimeoutError,
)
from qianji.icon_cache import ensure_icon
from qianji.image_upload import MAX_BILL_IMAGE_UPLOAD_BYTES, BillImageValidationError
from qianji.server.security import RestAuthFailureLimiter, mask_secret_identifier
from qianji.sign import hash_password
from qianji.write_service import WriteService

logger = logging.getLogger(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
)

# Per-IP throttle for failed REST authentication attempts (invalid tokens and
# rejected credentials), so the service cannot be used to brute-force upstream
# logins. Defaults are env-tunable via QIANJI_AUTH_FAILURE_LIMIT/WINDOW.
auth_failure_limiter = RestAuthFailureLimiter()

AUTH_SCHEME = "bearer "


@limiter.limit("10/minute")
async def _login_rate_limit_marker(request: Request) -> None:
    """Route-independent marker used to rate-limit login before auth runs."""


async def enforce_login_rate_limit(request: Request) -> None:
    if request.scope.get("path") == "/auth/login":
        await _login_rate_limit_marker(request)


async def require_api_auth(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(AUTH_SCHEME):
        raise HTTPException(status_code=401, detail="Authentication required")

    client_ip = request.client.host if request.client else "unknown"
    token = parse_qianji_auth_token(header[len(AUTH_SCHEME) :])
    if token is None:
        auth_failure_limiter.record(client_ip)
        logger.warning("Invalid auth token from %s", client_ip)
        raise HTTPException(status_code=401, detail="Invalid Authorization token")
    if not auth_failure_limiter.allowed(client_ip):
        logger.warning("Auth failure rate limit exceeded for %s", client_ip)
        raise HTTPException(status_code=429, detail="Too many failed authentication attempts")
    account = token.account
    pwd_hash = token.pwd_hash

    try:
        result = await _client(request).login_md5(account, pwd_hash, account_id=account)
    except UpstreamTimeoutError as exc:
        logger.warning("Auth upstream timeout for %s: %s", mask_secret_identifier(account), exc)
        raise HTTPException(status_code=504, detail=exc.em) from exc
    except AuthenticationError as exc:
        auth_failure_limiter.record(client_ip)
        logger.warning("Auth credentials rejected for %s: %s", mask_secret_identifier(account), exc)
        raise HTTPException(status_code=401, detail="Invalid Qianji credentials") from exc
    except QianjiError as exc:
        logger.warning("Auth upstream error for %s: %s", mask_secret_identifier(account), exc)
        raise HTTPException(status_code=502, detail=exc.em) from exc

    account_id = _client(request).current_account or account
    request.state.qianji_account_id = account_id
    request.state.qianji_auth_email = account
    request.state.qianji_auth_pwd_hash = pwd_hash
    request.state.qianji_auth_user = result.get("data", {}).get("user", {})
    return account_id


public_router = APIRouter()
router = APIRouter(dependencies=[Depends(enforce_login_rate_limit), Depends(require_api_auth)])


def _client(request: Request):
    return request.app.state.client


def _file_session_mgr(request: Request):
    mgr = getattr(request.app.state, "file_session_mgr", None)
    if mgr is not None:
        return mgr
    client = getattr(request.app.state, "client", None)
    mgr = getattr(client, "_session_mgr", None)
    if mgr is None:
        raise HTTPException(status_code=404, detail="File not found")
    return mgr


def _public_base_url() -> str:
    return os.environ.get("QIANJI_PUBLIC_BASE_URL", "").rstrip("/")


def _external_url(request: Request, path: str) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
    host = (
        request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
        .split(",", 1)[0]
        .strip()
    )
    if not path.startswith("/"):
        path = "/" + path
    return f"{proto}://{host}{path}"


def _download_base_url(request: Request) -> str:
    # Generated download links must work for external clients; prefer the
    # configured public base URL (same behavior as the MCP server) and fall
    # back to the request Host headers.
    return _public_base_url() or _external_url(request, "")


def _content_disposition(filename: str) -> str:
    safe = filename.replace("\\", "_").replace("/", "_").replace('"', "")
    return f'attachment; filename="{safe}"'


def _raise_bill_image_validation_http(exc: BillImageValidationError) -> None:
    raise HTTPException(status_code=422, detail=exc.to_detail()) from exc


async def _read_upload_limited(
    file: UploadFile,
    *,
    max_bytes: int = MAX_BILL_IMAGE_UPLOAD_BYTES,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(chunk_size):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Uploaded file is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _request_account_id(request: Request) -> str:
    account_id = getattr(request.state, "qianji_account_id", None)
    if not account_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(account_id)


def _account_info(request: Request) -> dict[str, Any]:
    account_id = _request_account_id(request)
    for account in _client(request).list_accounts():
        if account["account_id"] == account_id:
            return account
    return {
        "account_id": account_id,
        "user_name": getattr(request.state, "qianji_auth_user", {}).get("name", ""),
        "email": getattr(request.state, "qianji_auth_email", account_id),
        "devname": "",
        "is_current": True,
    }


@contextmanager
def _use_authorized_account(request: Request, account_id: str | None = None):
    auth_account_id = _request_account_id(request)
    if account_id and account_id != auth_account_id:
        raise HTTPException(status_code=403, detail="Cannot access a different Qianji account")
    with _client(request).use_account(auth_account_id):
        yield auth_account_id


def _semantic_bill_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"expense", "income", "transfer"}:
        raise ValueError("bill_type must be one of: expense, income, transfer")
    return normalized


def _required_semantic_bill_id(value: int | None, field_name: str, bill_type: str) -> int:
    if value is None:
        raise ValueError(f"{field_name} is required for {bill_type} bills")
    return value


async def _write_http_result(awaitable):
    try:
        return await awaitable
    except BillImageValidationError as exc:
        _raise_bill_image_validation_http(exc)
    except TagFeatureDisabledError as exc:
        raise HTTPException(status_code=409, detail=TAG_FEATURE_DISABLED_HTTP_MESSAGE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QianjiError as exc:
        status_code = 504 if isinstance(exc, UpstreamTimeoutError) else 502
        raise HTTPException(status_code=status_code, detail=exc.em) from exc


async def _create_semantic_bill(service: WriteService, req: BillCreateRequest) -> dict[str, Any]:
    bill_type = _semantic_bill_type(req.bill_type)
    if bill_type == "expense":
        asset_id = _required_semantic_bill_id(req.asset_id, "asset_id", bill_type)
        category_id = _required_semantic_bill_id(req.category_id, "category_id", bill_type)
        return await service.create_expense(
            amount=req.amount,
            occurred_at=req.occurred_at,
            asset_id=asset_id,
            category_id=category_id,
            remark=req.remark,
            book_id=req.book_id,
            tags=req.tags,
            images=req.images,
        )
    if bill_type == "income":
        asset_id = _required_semantic_bill_id(req.asset_id, "asset_id", bill_type)
        category_id = _required_semantic_bill_id(req.category_id, "category_id", bill_type)
        return await service.create_income(
            amount=req.amount,
            occurred_at=req.occurred_at,
            asset_id=asset_id,
            category_id=category_id,
            remark=req.remark,
            book_id=req.book_id,
            tags=req.tags,
            images=req.images,
        )
    from_asset_id = _required_semantic_bill_id(req.from_asset_id, "from_asset_id", bill_type)
    to_asset_id = _required_semantic_bill_id(req.to_asset_id, "to_asset_id", bill_type)
    return await service.create_transfer(
        amount=req.amount,
        occurred_at=req.occurred_at,
        from_asset_id=from_asset_id,
        to_asset_id=to_asset_id,
        remark=req.remark,
        book_id=req.book_id,
        tags=req.tags,
        images=req.images,
    )


async def _update_semantic_bill(
    service: WriteService,
    bill_id: BillId,
    bill_type: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    if bill_type == "expense":
        return await service.update_expense(bill_id=bill_id, **updates)
    if bill_type == "income":
        return await service.update_income(bill_id=bill_id, **updates)
    return await service.update_transfer(bill_id=bill_id, **updates)


class LoginRequest(BaseModel):
    email: str
    password: str | None = None
    account_id: str | None = None


BillId = int | str


class BillCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    bill_type: str = "expense"
    amount: float
    occurred_at: str
    asset_id: int | None = None
    category_id: int | None = None
    from_asset_id: int | None = None
    to_asset_id: int | None = None
    remark: str = ""
    book_id: int = -1
    tags: list[str] | None = None
    images: list[str] | None = None


class BillUpdateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    bill_type: str
    amount: float | None = None
    occurred_at: str | None = None
    asset_id: int | None = None
    category_id: int | None = None
    from_asset_id: int | None = None
    to_asset_id: int | None = None
    remark: str | None = None
    book_id: int | None = None
    tags: list[str] | None = None
    images: list[str] | None = None


class GenericRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class TagsEnabledRequest(BaseModel):
    enabled: bool


class RefundCreateRequest(BaseModel):
    amount: float
    occurred_at: str
    asset_id: int | None = None
    remark: str = ""
    currency: dict[str, Any] | None = None
    images: list[str] | None = None
    tags: list[str] | None = None


class RefundUpdateRequest(RefundCreateRequest):
    source_bill_id: BillId


class ReimbursementCreateRequest(BaseModel):
    source_bill_ids: list[BillId]
    amount: float
    occurred_at: str
    asset_id: int | None = None
    remark: str = ""
    currency: dict[str, Any] | None = None
    images: list[str] | None = None
    tags: list[str] | None = None


class ReimbursementUpdateRequest(ReimbursementCreateRequest):
    pass


class ReimbursementCancelRequest(BaseModel):
    reimbursement_bill_ids: list[BillId]


class BillSearchRequest(BaseModel):
    bill_id: int | str | None = None
    start_at: int | None = None
    end_at: int | None = None
    book_id: int | None = None
    bill_types: list[int] | None = None
    category_ids: list[int] | None = None
    asset_ids: list[int] | None = None
    tag_ids: list[str] | None = None
    keyword: str = ""
    min_amount: float | None = None
    max_amount: float | None = None
    currency: str = ""
    has_images: bool | None = None
    counted: bool | None = None
    sort_by: str = "occurred_at"
    sort_order: str = "desc"
    refresh: bool = False

    @field_validator("bill_id")
    @classmethod
    def _normalize_bill_id(cls, value: int | str | None) -> int | None:
        if value is None or value == "":
            return None
        return parse_bill_id(value)


class BillExportRequest(BillSearchRequest):
    format: str = "csv"


class ReportImageRequest(BillSearchRequest):
    report_type: str = "monthly_summary"


class TagSubmitRequest(BaseModel):
    name: str
    group_id: str = ""
    color: str | None = None


class TagUpdateRequest(BaseModel):
    name: str
    group_id: str | None = None
    color: str | None = None


class TagGroupSubmitRequest(BaseModel):
    name: str


class TagsReorderRequest(BaseModel):
    tag_ids: list[str]


class TagGroupsReorderRequest(BaseModel):
    group_ids: list[str]


class DebtLoanAssetCreateRequest(BaseModel):
    name: str
    amount: float
    startdate: str
    enddate: str | None = None
    linked_asset_id: int | None = None
    remark: str = ""
    book_id: int = -1
    images: list[str] | None = None
    incount: bool = True


class DebtLoanAssetUpdateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    startdate: str | None = None
    enddate: str | None = None
    linked_asset_id: int | None = None
    remark: str | None = None
    book_id: int | None = None
    images: list[str] | None = None
    incount: bool | None = None


class AssetUpdateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    remark: str | None = None


class LoanCollectionCreateRequest(BaseModel):
    amount: float
    occurred_at: str
    to_asset_id: int = -1
    interest_amount: float = 0.0
    remark: str = ""
    book_id: int = -1
    images: list[str] | None = None


class LoanCollectionUpdateRequest(BaseModel):
    amount: float
    occurred_at: str
    to_asset_id: int = -1
    remark: str = ""
    book_id: int = -1
    images: list[str] | None = None


class DebtRepaymentCreateRequest(BaseModel):
    amount: float
    occurred_at: str
    from_asset_id: int = -1
    interest_amount: float = 0.0
    remark: str = ""
    book_id: int = -1
    images: list[str] | None = None


class DebtRepaymentUpdateRequest(BaseModel):
    amount: float
    occurred_at: str
    from_asset_id: int = -1
    remark: str = ""
    book_id: int = -1
    images: list[str] | None = None


class LoanPrincipalCreateRequest(BaseModel):
    amount: float
    occurred_at: str
    from_asset_id: int = -1
    remark: str = ""
    book_id: int = -1
    images: list[str] | None = None


class LoanPrincipalUpdateRequest(LoanPrincipalCreateRequest):
    pass


class DebtPrincipalCreateRequest(BaseModel):
    amount: float
    occurred_at: str
    to_asset_id: int = -1
    remark: str = ""
    book_id: int = -1
    images: list[str] | None = None


class DebtPrincipalUpdateRequest(DebtPrincipalCreateRequest):
    pass


# --- Public generated files ---


@public_router.get("/files/{file_id}", tags=["files"], summary="Download a generated temporary file")
async def download_generated_file(file_id: str, request: Request):
    record = await _file_session_mgr(request).get_generated_file(file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(
        content=record["content"],
        media_type=record["content_type"],
        headers={"Content-Disposition": _content_disposition(record["filename"])},
    )


@public_router.get(
    "/category-icons/{filename}",
    tags=["icons"],
    summary="Serve a Qianji category icon from the local cache",
)
async def category_icon(filename: str):
    # The console's <img> tags carry no Authorization header, so this route is
    # public by necessity. ``filename`` is validated against a strict allowlist
    # inside icon_cache, so unvalidated input never reaches the filesystem.
    content = await ensure_icon(filename)
    if content is None:
        raise HTTPException(status_code=404, detail="Icon not found")
    return Response(
        content=content,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# --- Auth ---


@router.post("/auth/login", tags=["auth"], summary="Login (creates or updates account session)")
async def login(request: Request, req: LoginRequest | None = Body(default=None)):
    auth_account_id = _request_account_id(request)
    if req:
        if req.email != getattr(request.state, "qianji_auth_email", auth_account_id):
            raise HTTPException(status_code=403, detail="Request body account does not match Authorization account")
        if req.account_id and req.account_id != auth_account_id:
            raise HTTPException(status_code=403, detail="Request body account_id does not match Authorization account")
        if req.password and hash_password(req.password) != getattr(request.state, "qianji_auth_pwd_hash", ""):
            raise HTTPException(status_code=401, detail="Password does not match the Authorization credentials")
    return {"logged_in": True, "account": _account_info(request)}


@router.get("/auth/status", tags=["auth"], summary="Current account status")
async def auth_status(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        if c.is_logged_in:
            s = c.session
            return {
                "logged_in": True,
                "account_id": c.current_account,
                "user_name": s.user_name,
                "email": s.email,
                "devname": s.devname,
            }
        return {"logged_in": False}


@router.get("/auth/accounts", tags=["auth"], summary="List all logged-in accounts")
async def list_accounts(request: Request):
    return {"accounts": [_account_info(request)]}


@router.post("/auth/switch", tags=["auth"], summary="Switch current active account (process-level state)")
async def switch_account(request: Request, account_id: str = Query(...)):
    with _use_authorized_account(request, account_id) as aid:
        _client(request).set_account(aid)
        return {
            "switched_to": aid,
            "note": "Current account is process-level state shared by all requests. "
            "Clients holding tokens for multiple accounts should select the account "
            "via the Authorization header instead of switching.",
        }


@router.post("/auth/logout", tags=["auth"])
async def logout(request: Request, account_id: str | None = None):
    with _use_authorized_account(request, account_id) as aid:
        return await _client(request).logout(account_id=aid)


# --- Bills ---


@router.get("/bills", tags=["bills"], summary="List bills newest-first (cache-backed)")
async def get_bills(
    request: Request,
    book_id: int = -1,
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    cursor: str | None = None,
    refresh: bool = False,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        try:
            return stringify_bill_payload_ids(
                await _list_recent_bills(c, book_id=book_id, limit=limit, cursor=cursor, refresh=refresh)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/bills/sync", tags=["bills"], summary="Sync bills from Qianji into the local cache")
async def sync_bills(
    request: Request,
    book_id: int = -1,
    full: bool = False,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _sync_bills_to_cache(c, book_id=book_id, full=full)


@router.get("/bills/range", tags=["bills"], summary="List bills in a bill-time range (cache-backed)")
async def get_bills_range(
    request: Request,
    start_time: int = Query(..., ge=0),
    end_time: int = Query(..., ge=0),
    book_id: int = -1,
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    cursor: str | None = None,
    refresh: bool = False,
    account_id: str | None = None,
):
    if end_time < start_time:
        raise HTTPException(status_code=422, detail="end_time must be greater than or equal to start_time")
    c = _client(request)
    with _use_authorized_account(request, account_id):
        try:
            return stringify_bill_payload_ids(
                await _get_bills_range(
                    c,
                    start_at=start_time,
                    end_at=end_time,
                    book_id=book_id,
                    limit=limit,
                    cursor=cursor,
                    refresh=refresh,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/bills/search", tags=["bills"], summary="Search bills with unified filters (cache-backed)")
async def search_bills(
    request: Request,
    bill_id: int | None = None,
    start_at: int | None = Query(default=None, ge=0),
    end_at: int | None = Query(default=None, ge=0),
    book_id: int | None = None,
    bill_types: list[int] | None = Query(default=None),
    category_ids: list[int] | None = Query(default=None),
    asset_ids: list[int] | None = Query(default=None),
    tag_ids: list[str] | None = Query(default=None),
    keyword: str = "",
    min_amount: float | None = None,
    max_amount: float | None = None,
    currency: str = "",
    has_images: bool | None = None,
    counted: bool | None = None,
    sort_by: str = "occurred_at",
    sort_order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    refresh: bool = False,
    account_id: str | None = None,
):
    if start_at is not None and end_at is not None and end_at < start_at:
        raise HTTPException(status_code=422, detail="end_at must be greater than or equal to start_at")
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return stringify_bill_payload_ids(
            await _search_bills(
                c,
                bill_id=bill_id,
                start_at=start_at,
                end_at=end_at,
                book_id=book_id,
                bill_types=bill_types,
                category_ids=category_ids,
                asset_ids=asset_ids,
                tag_ids=tag_ids,
                keyword=keyword,
                min_amount=min_amount,
                max_amount=max_amount,
                currency=currency,
                has_images=has_images,
                counted=counted,
                sort_by=sort_by,
                sort_order=sort_order,
                page=page,
                page_size=page_size,
                refresh=refresh,
            )
        )


@router.post("/bills/export", tags=["bills"], summary="Export searched bills to a temporary file")
async def export_bills(req: BillExportRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        try:
            return await export_bills_to_file(
                c,
                file_format=req.format,
                base_url=_download_base_url(request),
                **req.model_dump(exclude={"format"}, exclude_none=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reports/image", tags=["reports"], summary="Generate a report SVG image as a temporary file")
async def generate_report_image(req: ReportImageRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        try:
            return await generate_report_image_file(
                c,
                report_type=req.report_type,
                base_url=_download_base_url(request),
                **req.model_dump(exclude={"report_type"}, exclude_none=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/bills", tags=["bills"], summary="Create a new bill")
async def create_bill(req: BillCreateRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(_create_semantic_bill(WriteService(c), req))


@router.patch("/bills/{bill_id}", tags=["bills"], summary="Update an existing bill")
async def update_bill(
    bill_id: int,
    req: BillUpdateRequest,
    request: Request,
    book_id: int = -1,
    account_id: str | None = None,
):
    patch = req.model_dump(exclude_unset=True, exclude_none=True)
    try:
        bill_type = _semantic_bill_type(patch.pop("bill_type"))
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="bill_type is required") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request_book_id = patch.pop("book_id", None)
    if not patch:
        raise HTTPException(status_code=422, detail="At least one update field is required")
    patch["book_id"] = book_id if request_book_id is None else request_book_id
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(_update_semantic_bill(WriteService(c), bill_id, bill_type, patch))


@router.delete("/bills/{bill_id}", tags=["bills"])
async def delete_bill(bill_id: int, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).delete_bill(bill_id))


# --- Uploads ---


@router.post("/uploads/bill-image", tags=["uploads"], summary="Upload a bill image and return an image key")
async def upload_bill_image(request: Request, file: UploadFile = File(...), account_id: str | None = None):
    c = _client(request)
    content = await _read_upload_limited(file)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).upload_bill_image(
                filename=file.filename or "bill-image",
                content=content,
                mime_type=file.content_type or "application/octet-stream",
            )
        )


# --- Refunds / Reimbursements / Debt-loan records ---


@router.post("/bills/{source_bill_id}/refunds", tags=["refunds"])
async def create_refund(
    source_bill_id: int,
    req: RefundCreateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).create_refund(
                source_bill_id=source_bill_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                asset_id=req.asset_id,
                remark=req.remark,
                currency=req.currency,
                images=req.images,
                tags=req.tags,
            )
        )


@router.patch("/refunds/{refund_bill_id}", tags=["refunds"])
async def update_refund(
    refund_bill_id: int,
    req: RefundUpdateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).update_refund(
                source_bill_id=req.source_bill_id,
                refund_bill_id=refund_bill_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                asset_id=req.asset_id,
                remark=req.remark,
                currency=req.currency,
                images=req.images,
                tags=req.tags,
            )
        )


@router.post("/reimbursements", tags=["reimbursements"])
async def create_reimbursement(
    req: ReimbursementCreateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).create_reimbursement(
                source_bill_ids=req.source_bill_ids,
                amount=req.amount,
                occurred_at=req.occurred_at,
                asset_id=req.asset_id,
                remark=req.remark,
                currency=req.currency,
                images=req.images,
                tags=req.tags,
            )
        )


@router.patch("/reimbursements/{reimbursement_bill_id}", tags=["reimbursements"])
async def update_reimbursement(
    reimbursement_bill_id: int,
    req: ReimbursementUpdateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).update_reimbursement(
                reimbursement_bill_id=reimbursement_bill_id,
                source_bill_ids=req.source_bill_ids,
                amount=req.amount,
                occurred_at=req.occurred_at,
                asset_id=req.asset_id,
                remark=req.remark,
                currency=req.currency,
                images=req.images,
                tags=req.tags,
            )
        )


@router.post("/reimbursements/cancel", tags=["reimbursements"])
async def cancel_reimbursement(req: ReimbursementCancelRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).cancel_reimbursement(req.reimbursement_bill_ids))


@router.post("/assets/loans", tags=["assets"])
async def create_loan_asset(req: DebtLoanAssetCreateRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).create_loan_asset(**req.model_dump()))


@router.patch("/assets/loans/{asset_id}", tags=["assets"])
async def update_loan_asset(
    asset_id: int,
    req: DebtLoanAssetUpdateRequest,
    request: Request,
    account_id: str | None = None,
):
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="At least one update field is required")
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).update_loan_asset(asset_id=asset_id, **updates))


@router.get("/assets/loans", tags=["assets"])
async def list_loan_assets(request: Request, status: int = 0, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.asset_list_loan(loan_type=LoanType.LOAN, status=status)


@router.post("/assets/debts", tags=["assets"])
async def create_debt_asset(req: DebtLoanAssetCreateRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).create_debt_asset(**req.model_dump()))


@router.patch("/assets/debts/{asset_id}", tags=["assets"])
async def update_debt_asset(
    asset_id: int,
    req: DebtLoanAssetUpdateRequest,
    request: Request,
    account_id: str | None = None,
):
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="At least one update field is required")
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).update_debt_asset(asset_id=asset_id, **updates))


@router.get("/assets/debts", tags=["assets"])
async def list_debt_assets(request: Request, status: int = 0, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.asset_list_loan(loan_type=LoanType.DEBT, status=status)


@router.post("/loans/{loan_asset_id}/collections", tags=["debt-loan"])
async def record_loan_collection(
    loan_asset_id: int,
    req: LoanCollectionCreateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).record_loan_collection(
                loan_asset_id=loan_asset_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                to_asset_id=req.to_asset_id,
                interest_amount=req.interest_amount,
                remark=req.remark,
                book_id=req.book_id,
                images=req.images,
            )
        )


@router.patch("/loan-collections/{bill_id}", tags=["debt-loan"])
async def update_loan_collection(
    bill_id: int,
    req: LoanCollectionUpdateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).update_loan_collection(
                bill_id=bill_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                to_asset_id=req.to_asset_id,
                remark=req.remark,
                book_id=req.book_id,
                images=req.images,
            )
        )


@router.post("/loans/{loan_asset_id}/principal-records", tags=["debt-loan"])
async def add_loan_principal(
    loan_asset_id: int,
    req: LoanPrincipalCreateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).add_loan_principal(
                loan_asset_id=loan_asset_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                from_asset_id=req.from_asset_id,
                remark=req.remark,
                book_id=req.book_id,
                images=req.images,
            )
        )


@router.patch("/loan-principal-records/{bill_id}", tags=["debt-loan"])
async def update_loan_principal(
    bill_id: int,
    req: LoanPrincipalUpdateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).update_loan_principal(
                bill_id=bill_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                from_asset_id=req.from_asset_id,
                remark=req.remark,
                book_id=req.book_id,
                images=req.images,
            )
        )


@router.post("/debts/{debt_asset_id}/repayments", tags=["debt-loan"])
async def record_debt_repayment(
    debt_asset_id: int,
    req: DebtRepaymentCreateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).record_debt_repayment(
                debt_asset_id=debt_asset_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                from_asset_id=req.from_asset_id,
                interest_amount=req.interest_amount,
                remark=req.remark,
                book_id=req.book_id,
                images=req.images,
            )
        )


@router.post("/debts/{debt_asset_id}/principal-records", tags=["debt-loan"])
async def add_debt_principal(
    debt_asset_id: int,
    req: DebtPrincipalCreateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).add_debt_principal(
                debt_asset_id=debt_asset_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                to_asset_id=req.to_asset_id,
                remark=req.remark,
                book_id=req.book_id,
                images=req.images,
            )
        )


@router.patch("/debt-principal-records/{bill_id}", tags=["debt-loan"])
async def update_debt_principal(
    bill_id: int,
    req: DebtPrincipalUpdateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).update_debt_principal(
                bill_id=bill_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                to_asset_id=req.to_asset_id,
                remark=req.remark,
                book_id=req.book_id,
                images=req.images,
            )
        )


@router.patch("/debt-repayments/{bill_id}", tags=["debt-loan"])
async def update_debt_repayment(
    bill_id: int,
    req: DebtRepaymentUpdateRequest,
    request: Request,
    account_id: str | None = None,
):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).update_debt_repayment(
                bill_id=bill_id,
                amount=req.amount,
                occurred_at=req.occurred_at,
                from_asset_id=req.from_asset_id,
                remark=req.remark,
                book_id=req.book_id,
                images=req.images,
            )
        )


# --- Assets ---


@router.get("/assets", tags=["assets"])
async def get_assets(request: Request, status: int = 0, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.asset_list(status=status)


@router.get("/assets/types", tags=["assets"])
async def get_asset_types(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.asset_type_list()


@router.patch("/assets/{asset_id}", tags=["assets"])
async def update_asset(asset_id: int, req: AssetUpdateRequest, request: Request, account_id: str | None = None):
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="At least one update field is required")
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).update_asset(asset_id=asset_id, **updates))


@router.delete("/assets/{asset_id}", tags=["assets"])
async def delete_asset(asset_id: int, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.asset_delete(asset_id)


# --- Categories ---


@router.get("/categories", tags=["categories"])
async def get_categories(request: Request, book_id: int = -1, type: int | None = None, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.category_list(book_id=book_id, cat_type=type)


@router.get("/categories/icons", tags=["categories"])
async def get_category_icons(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.category_icon_list()


# --- Books ---


@router.get("/books", tags=["books"])
async def get_books(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.book_list()


# --- Tags ---


@router.get("/tags", tags=["tags"])
async def get_tags(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.tag_list()


@router.post("/tags", tags=["tags"])
async def create_tag(req: TagSubmitRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).create_tag(name=req.name, group_id=req.group_id, color=req.color)
        )


@router.patch("/tags/{tag_id}", tags=["tags"])
async def update_tag(tag_id: str, req: TagUpdateRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(
            WriteService(c).update_tag(tag_id=tag_id, name=req.name, group_id=req.group_id, color=req.color)
        )


@router.delete("/tags/{tag_id}", tags=["tags"])
async def delete_tag(tag_id: str, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).delete_tag(tag_id=tag_id))


@router.post("/tags/{tag_id}/archive", tags=["tags"])
async def archive_tag(tag_id: str, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).archive_tag(tag_id=tag_id))


@router.post("/tags/reorder", tags=["tags"])
async def reorder_tags(req: TagsReorderRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).reorder_tags(tag_ids=req.tag_ids))


@router.get("/tags/enabled", tags=["tags"])
async def get_tags_enabled(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return {"enabled": await c.get_tags_enabled()}


@router.put("/tags/enabled", tags=["tags"])
async def put_tags_enabled(req: TagsEnabledRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).set_tags_enabled(req.enabled))


@router.get("/tags/sync", tags=["tags"])
async def sync_tags(request: Request, last_time: int = 0, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.tag_sync(last_time=last_time)


@router.post("/tag-groups", tags=["tags"])
async def create_tag_group(req: TagGroupSubmitRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).create_tag_group(name=req.name))


@router.patch("/tag-groups/{group_id}", tags=["tags"])
async def update_tag_group(group_id: str, req: TagGroupSubmitRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).update_tag_group(group_id=group_id, name=req.name))


@router.delete("/tag-groups/{group_id}", tags=["tags"])
async def delete_tag_group(group_id: str, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).delete_tag_group(group_id=group_id))


@router.post("/tag-groups/reorder", tags=["tags"])
async def reorder_tag_groups(req: TagGroupsReorderRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await _write_http_result(WriteService(c).reorder_tag_groups(group_ids=req.group_ids))


# --- Budgets ---


@router.get("/budgets", tags=["budgets"])
async def get_budgets(request: Request, book_id: int = -1, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.budget_list(book_id=book_id)


# --- Currencies ---


@router.get("/currencies", tags=["currencies"])
async def get_currencies(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.currency_list()


# --- Savings ---


@router.get("/savings", tags=["savings"])
async def get_savings(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.saving_list()


# --- User Cards ---


@router.get("/usercards", tags=["usercards"])
async def get_usercards(request: Request, account_id: str | None = None):
    c = _client(request)
    with _use_authorized_account(request, account_id):
        return await c.usercard_list()


# --- Generic Passthrough ---


@router.post("/api/{ctrl}/{act}", tags=["generic"], summary="Generic API passthrough (all 112 endpoints)")
async def generic_api(ctrl: str, act: str, req: GenericRequest, request: Request, account_id: str | None = None):
    c = _client(request)
    endpoint = ENDPOINT_BY_PATH.get(f"/{ctrl}/{act}")
    if endpoint is None:
        raise HTTPException(status_code=404, detail=f"Unknown endpoint: /{ctrl}/{act}")
    try:
        with _use_authorized_account(request, account_id):
            return await call_endpoint(c, endpoint, (), dict(req.params))
    except TypeError as exc:
        logger.warning("TypeError in generic API %s/%s: %s", ctrl, act, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --- Upstream-compatible mirrored routes ---


def _make_mirrored_route(endpoint: EndpointSpec):
    model = endpoint_request_model(endpoint)

    async def mirrored_route(request: Request, body: Any = Body(default=None), account_id: str | None = None):
        c = _client(request)
        params = params_from_model(body) if body is not None else {}
        if endpoint.ctrl == "account" and endpoint.act == "login":
            body_account = str(params.get("v") or "")
            auth_account = str(getattr(request.state, "qianji_auth_email", _request_account_id(request)))
            if body_account and body_account != auth_account:
                raise HTTPException(status_code=403, detail="Request body account does not match Authorization account")
        method = getattr(c, endpoint.method_name)
        try:
            if endpoint.public:
                if account_id and account_id != _request_account_id(request):
                    raise HTTPException(status_code=403, detail="Cannot access a different Qianji account")
                return await method(**params)
            with _use_authorized_account(request, account_id):
                return await method(**params)
        except TypeError as exc:
            logger.warning("TypeError in mirrored route %s: %s", endpoint.path, exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    mirrored_route.__name__ = f"mirror_{endpoint.ctrl}_{endpoint.act}"
    mirrored_route.__qualname__ = mirrored_route.__name__
    mirrored_route.__doc__ = endpoint.description or endpoint.path
    mirrored_route.__annotations__ = {
        "request": Request,
        "body": model,
        "account_id": str | None,
    }
    return mirrored_route


for _endpoint in ENDPOINTS:
    router.post(
        _endpoint.path,
        tags=[_endpoint.ctrl],
        summary=_endpoint.description or _endpoint.path,
        name=f"mirror:{_endpoint.ctrl}:{_endpoint.act}",
    )(_make_mirrored_route(_endpoint))
