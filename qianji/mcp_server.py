from __future__ import annotations

import base64
import binascii
import functools
import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool as MCPTool

from qianji.auth_token import parse_qianji_auth_token
from qianji.bill_files import export_bills_to_file, generate_report_image_file
from qianji.bill_id import bill_id_string as _bill_id_string
from qianji.bill_id import parse_bill_id as _bill_id
from qianji.bill_id import stringify_known_bill_ids as _stringify_known_bill_ids
from qianji.bill_ops import BILL_CACHE_TTL_KEY, DEFAULT_LIST_LIMIT, _as_float
from qianji.bill_ops import get_base_currency as _get_base_currency
from qianji.bill_ops import get_bill_by_id as _get_bill_by_id
from qianji.bill_ops import get_bills_range as _get_bills_range
from qianji.bill_ops import infer_base_currency as _infer_base_currency
from qianji.bill_ops import list_recent_bills as _list_recent_bills
from qianji.bill_ops import resolve_cache_ttl as _resolve_cache_ttl
from qianji.bill_ops import search_bills as _search_bills
from qianji.bill_ops import sync_bills_to_cache as _sync_bills_to_cache
from qianji.bill_ops import update_bill_by_id as _update_bill_by_id
from qianji.client import QianjiClient
from qianji.constants import DEFAULT_BILL_CACHE_TTL
from qianji.endpoints import ENDPOINTS, EndpointSpec, endpoint_signature
from qianji.errors import QianjiError, TagFeatureDisabledError
from qianji.image_upload import validate_bill_image_keys
from qianji.tags import TAG_ENABLED_CONFIG_KEY, flatten_tag_items, tags_flag_enabled
from qianji.tags import get_tags_enabled as _get_tags_enabled
from qianji.write_service import WriteService

logger = logging.getLogger(__name__)


class QianjiBearerTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        credentials = parse_qianji_auth_token(token)
        if credentials is None:
            return None
        email = credentials.account
        pwd_hash = credentials.pwd_hash

        client = await _get_client()
        try:
            result = await client.login_md5(email, pwd_hash, account_id=email)
        except QianjiError as exc:
            logger.warning("MCP token verification failed for %s: %s", email, exc)
            return None

        account_id = client.current_account or email
        user = result.get("data", {}).get("user", {})
        return AccessToken(
            token=token,
            client_id=account_id,
            scopes=["qianji"],
            subject=account_id,
            claims={
                "account_id": account_id,
                "email": email,
                "user_name": user.get("name", ""),
            },
        )


mcp = FastMCP(
    "qianji",
    instructions="钱迹记账 API，支持多账户。首次使用先 login，之后用 account_id 区分不同账户。",
    auth=AuthSettings(
        issuer_url="http://qianji.local",
        resource_server_url=None,
        required_scopes=["qianji"],
    ),
    token_verifier=QianjiBearerTokenVerifier(),
)

MCP_MODE_ENV = "QIANJI_MCP_MODE"
DEFAULT_MCP_MODE = "advanced"

# ============ FastMCP private-API compatibility shim ============
# FastMCP exposes no public API for enumerating or replacing registered tools,
# reading the current request context, invoking a tool by name, or resetting
# the streamable-HTTP session manager. All private mcp attribute access in
# this module is funneled through the helpers below; audit this block first
# when upgrading the mcp package.


def _registered_tools() -> dict[str, Any]:
    return mcp._tool_manager._tools


def _list_registered_tool_infos() -> list[Any]:
    return mcp._tool_manager.list_tools()


def _replace_registered_tools(tools: dict[str, Any]) -> None:
    mcp._tool_manager._tools = dict(tools)


def _current_request_context() -> Any:
    return mcp._mcp_server.request_context


async def _call_registered_tool(name: str, arguments: dict[str, Any]) -> Any:
    context = mcp.get_context()
    return await mcp._tool_manager.call_tool(name, arguments, context=context, convert_result=True)


def _install_mode_aware_mcp_handlers() -> None:
    mcp.list_tools = _mode_aware_list_tools  # type: ignore[method-assign]
    mcp.call_tool = _mode_aware_call_tool  # type: ignore[method-assign]
    mcp._mcp_server.list_tools()(mcp.list_tools)
    mcp._mcp_server.call_tool(validate_input=False)(mcp.call_tool)


def reset_streamable_http_session_manager() -> None:
    # FastMCP's streamable HTTP session manager can only be run once; recreate it
    # when the parent FastAPI app is restarted in tests or reload mode.
    mcp._session_manager = None  # type: ignore[attr-defined]


MCP_MODES = {"read", "write", "advanced", "full"}
READ_TOOLS = frozenset(
    {
        "login",
        "list_accounts",
        "switch_account",
        "get_login_status",
        "search_bills",
        "export_bills",
        "generate_report_image",
        "get_assets",
        "get_categories",
        "list_books",
        "get_tags",
        "get_tags_enabled",
        "get_currencies",
        "get_bill_cache_ttl",
        "get_budgets",
    }
)
WRITE_TOOLS = READ_TOOLS | frozenset(
    {
        "create_expense",
        "create_income",
        "create_transfer",
        "update_expense",
        "update_income",
        "update_transfer",
        "sync_bills",
        "set_bill_cache_ttl",
        "set_tags_enabled",
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
    }
)
ADVANCED_TOOLS = WRITE_TOOLS | frozenset(
    {
        "create_refund",
        "update_refund",
        "create_reimbursement",
        "update_reimbursement",
        "cancel_reimbursement",
        "create_loan_asset",
        "update_loan_asset",
        "create_debt_asset",
        "update_debt_asset",
        "add_loan_principal",
        "update_loan_principal",
        "add_debt_principal",
        "update_debt_principal",
        "record_loan_collection",
        "update_loan_collection",
        "record_debt_repayment",
        "update_debt_repayment",
    }
)

_client: QianjiClient | None = None
_ALL_TOOLS: dict[str, Any] = {}
_configured_mcp_mode: str | None = None


def _has_mcp_mode_value(mode: str | None) -> bool:
    return mode is not None and bool(str(mode).strip())


def _normalize_mcp_mode(mode: str | None) -> str:
    selected = (mode or DEFAULT_MCP_MODE).strip().lower()
    if selected not in MCP_MODES:
        raise ValueError(f"Invalid {MCP_MODE_ENV}={selected!r}. Expected one of: {', '.join(sorted(MCP_MODES))}")
    return selected


def get_mcp_mode(mode: str | None = None) -> str:
    if _has_mcp_mode_value(mode):
        return _normalize_mcp_mode(mode)
    return _normalize_mcp_mode(_configured_mcp_mode or os.environ.get(MCP_MODE_ENV) or DEFAULT_MCP_MODE)


def get_mcp_tool_names_for_mode(mode: str | None = None) -> set[str]:
    selected = get_mcp_mode(mode)
    if selected == "read":
        return set(READ_TOOLS)
    if selected == "write":
        return set(WRITE_TOOLS)
    if selected == "advanced":
        return set(ADVANCED_TOOLS)
    return set(_ALL_TOOLS or _registered_tools())


def configure_mcp_tools(mode: str | None = None) -> str:
    global _configured_mcp_mode
    selected = _normalize_mcp_mode(mode if _has_mcp_mode_value(mode) else os.environ.get(MCP_MODE_ENV))
    _configured_mcp_mode = selected
    if not _ALL_TOOLS:
        return selected

    _replace_registered_tools(_ALL_TOOLS)
    return selected


def _query_mode_from_current_request() -> str | None:
    try:
        request_context = _current_request_context()
    except LookupError:
        return None

    request = request_context.request
    query_params = getattr(request, "query_params", None)
    if query_params is None:
        return None

    raw_mode = query_params.get("mode")
    if raw_mode is None or not str(raw_mode).strip():
        return None
    return str(raw_mode)


def _mcp_mode_for_current_request() -> str:
    return get_mcp_mode(_query_mode_from_current_request())


def _to_mcp_tool(info: Any) -> MCPTool:
    return MCPTool(
        name=info.name,
        title=info.title,
        description=info.description,
        inputSchema=info.parameters,
        outputSchema=info.output_schema,
        annotations=info.annotations,
        icons=info.icons,
        _meta=info.meta,
    )


async def _mode_aware_list_tools() -> list[MCPTool]:
    selected = _mcp_mode_for_current_request()
    allowed = get_mcp_tool_names_for_mode(selected)
    return [_to_mcp_tool(info) for info in _list_registered_tool_infos() if info.name in allowed]


async def _mode_aware_call_tool(name: str, arguments: dict[str, Any]) -> Any:
    selected = _mcp_mode_for_current_request()
    if name not in get_mcp_tool_names_for_mode(selected):
        raise ValueError(f"Tool {name!r} is not allowed in MCP mode {selected!r}")
    return await _call_registered_tool(name, arguments)


async def _get_client() -> QianjiClient:
    global _client
    if _client is None:
        default_db = Path(__file__).resolve().parent.parent / ".qianji.db"
        db_path = Path(os.environ.get("QIANJI_DB_PATH", str(default_db)))
        _client = QianjiClient(db_path=db_path)
        await _client._load_all_sessions()
    return _client


def set_client(client: QianjiClient | None) -> None:
    global _client
    _client = client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _fmt(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


async def _base_currency(client: QianjiClient) -> str:
    return await _get_base_currency(client)


def _resolve_currency(raw_currency: Any, base_currency: str) -> tuple[str, str]:
    if raw_currency:
        return str(raw_currency), "asset"
    if base_currency:
        return base_currency, "account_default"
    return "CNY", "fallback"


def _normalize_book(book: dict) -> dict[str, Any]:
    book_id = book.get("bookid", book.get("id", -1))
    try:
        book_id = int(book_id)
    except (TypeError, ValueError):
        book_id = -1
    return {
        "id": book_id,
        "name": book.get("name", ""),
        "is_default": book_id == -1,
        "type": book.get("type"),
    }


def _auth_account_id() -> str | None:
    token = get_access_token()
    if not token:
        return None
    claims = token.claims or {}
    account_id = claims.get("account_id") or token.subject or token.client_id
    return str(account_id) if account_id else None


def _authorized_account_id(account_id: str = "") -> str | None:
    requested = str(account_id or "").strip()
    authenticated = _auth_account_id()
    if authenticated is None:
        return requested or None
    if requested and requested != authenticated:
        raise ValueError("Cannot access a different Qianji account")
    return authenticated


def _public_base_url() -> str:
    return os.environ.get("QIANJI_PUBLIC_BASE_URL", "").rstrip("/")


def _tags_enabled_from_configs(configs: dict[str, Any] | None) -> bool:
    return tags_flag_enabled((configs or {}).get(TAG_ENABLED_CONFIG_KEY))


def _optional_bill_id(value: Any, field_name: str = "bill_id") -> int | None:
    if value is None:
        return None
    return _bill_id(value, field_name)


_BILL_ID_STRING_TOOL_FIELDS = {
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
_BILL_ID_STRING_ARRAY_TOOL_FIELDS = {
    "create_reimbursement": ("source_bill_ids",),
    "update_reimbursement": ("source_bill_ids",),
    "cancel_reimbursement": ("reimbursement_bill_ids",),
}
_BILL_ID_SCHEMA_DESCRIPTION = "Pass as a string to avoid JavaScript number precision loss."


def _stringify_bill_record(value: Any) -> Any:
    if not isinstance(value, dict):
        return value

    record = _stringify_known_bill_ids(value)
    if "id" in record:
        record["id"] = _bill_id_string(record["id"])

    raw = record.get("raw")
    if isinstance(raw, dict):
        raw_record = dict(raw)
        if "id" in raw_record:
            raw_record["id"] = _bill_id_string(raw_record["id"])
        record["raw"] = raw_record
    return record


def _stringify_bill_result(value: Any) -> Any:
    result = _stringify_known_bill_ids(value)
    if not isinstance(result, dict):
        return result

    if isinstance(result.get("bill"), dict):
        result["bill"] = _stringify_bill_record(result["bill"])
    if isinstance(result.get("items"), list):
        result["items"] = [_stringify_bill_record(item) for item in result["items"]]
    return result


def _with_bill_id_schema_description(schema: dict[str, Any]) -> None:
    description = str(schema.get("description") or "").strip()
    if _BILL_ID_SCHEMA_DESCRIPTION in description:
        return
    schema["description"] = f"{description} {_BILL_ID_SCHEMA_DESCRIPTION}".strip()


def _schema_as_bill_id_string(schema: dict[str, Any]) -> None:
    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        schema["anyOf"] = [
            {"type": "string"} if isinstance(item, dict) and item.get("type") == "integer" else item
            for item in schema["anyOf"]
        ]
        if {"type": "string"} not in schema["anyOf"]:
            schema["anyOf"].insert(0, {"type": "string"})
        schema.pop("type", None)
    else:
        schema["type"] = "string"
    for numeric_keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        schema.pop(numeric_keyword, None)
    _with_bill_id_schema_description(schema)


def _schema_as_bill_id_string_array(schema: dict[str, Any]) -> None:
    schema["type"] = "array"
    items = schema.setdefault("items", {})
    if isinstance(items, dict):
        _schema_as_bill_id_string(items)
    _with_bill_id_schema_description(schema)


def _patch_bill_id_tool_schemas() -> None:
    for tool_name, field_names in _BILL_ID_STRING_TOOL_FIELDS.items():
        info = _registered_tools().get(tool_name)
        if info is None:
            continue
        properties = info.parameters.get("properties", {})
        for field_name in field_names:
            schema = properties.get(field_name)
            if isinstance(schema, dict):
                _schema_as_bill_id_string(schema)

    for tool_name, field_names in _BILL_ID_STRING_ARRAY_TOOL_FIELDS.items():
        info = _registered_tools().get(tool_name)
        if info is None:
            continue
        properties = info.parameters.get("properties", {})
        for field_name in field_names:
            schema = properties.get(field_name)
            if isinstance(schema, dict):
                _schema_as_bill_id_string_array(schema)


# ============ Account Management ============


@mcp.tool()
async def login(email: str, password: str, account_id: str = "") -> dict[str, Any]:
    """Login to Qianji and save session. Supports multiple accounts.

    Args:
        email: Qianji account email
        password: Password (plaintext, will be MD5 hashed)
        account_id: Optional account alias. Defaults to email.
    """
    target_account = _authorized_account_id(account_id or email)
    client = await _get_client()
    result = await client.login(email, password, account_id=target_account or account_id or email)
    user = result["data"]["user"]
    session = client.session
    return {
        "ok": True,
        "account_id": client.current_account,
        "user_name": user.get("name", ""),
        "email": user.get("email", ""),
        "tags_enabled": _tags_enabled_from_configs(session.user_configs if session else None),
    }


@mcp.tool()
async def list_accounts() -> dict[str, Any]:
    """List all logged-in Qianji accounts."""
    client = await _get_client()
    authenticated = _auth_account_id()
    accounts = client.list_accounts()
    if authenticated is not None:
        accounts = [account for account in accounts if str(account.get("account_id")) == authenticated]
    return {"accounts": accounts}


@mcp.tool()
async def switch_account(account_id: str) -> dict[str, Any]:
    """Switch the active account for subsequent operations.

    This flips process-level global state on the shared client. In multi-account
    setups behind Bearer tokens, prefer passing each tool's account_id parameter
    instead of relying on a switch.

    Args:
        account_id: The account ID to switch to (usually the email)
    """
    client = await _get_client()
    authorized_account = _authorized_account_id(account_id)
    if not authorized_account:
        raise ValueError("account_id is required")
    client.set_account(authorized_account)
    return {"ok": True, "switched_to": authorized_account}


@mcp.tool()
async def get_login_status() -> dict[str, Any]:
    """Check current login status."""
    client = await _get_client()
    with client.use_account(_auth_account_id()):
        if not client.is_logged_in or client.session is None:
            return {"logged_in": False}
        tags_enabled = await _get_tags_enabled(client)
        s = client.session
        return {
            "logged_in": True,
            "account_id": client.current_account,
            "user_name": s.user_name,
            "email": s.email,
            "devname": s.devname,
            "tags_enabled": tags_enabled,
        }


# ============ Query Tools ============


@mcp.tool()
async def list_recent_bills(
    limit: int = DEFAULT_LIST_LIMIT, cursor: str = "", book_id: int = -1, refresh: bool = False, account_id: str = ""
) -> dict[str, Any]:
    """List bills newest-first from the local cache. Page with next_cursor.

    Args:
        limit: Max bills to return (1-500, default 50)
        cursor: Pass the previous response's next_cursor to fetch the next page
        book_id: Book ID (-1 = default book)
        refresh: Bypass the cache TTL and force an incremental sync first
        account_id: Which account (empty = current)

    Returns: {items: [Bill], next_cursor, has_more}. Each Bill has id, bill_type,
    type_code, counted, amount, currency, occurred_at, book_id, asset_id,
    from_asset_id, to_asset_id, category_id, remark, tag_ids.
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await _list_recent_bills(client, book_id=book_id, limit=limit, cursor=cursor or None, refresh=refresh)
    return _stringify_bill_result(result)


@mcp.tool()
async def get_bills_range(
    start_at: int,
    end_at: int,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str = "",
    book_id: int = -1,
    refresh: bool = False,
    account_id: str = "",
) -> dict[str, Any]:
    """List bills whose occurred_at falls in [start_at, end_at], newest-first, from cache.

    Args:
        start_at: Range start as Unix seconds (inclusive)
        end_at: Range end as Unix seconds (inclusive)
        limit: Max bills to return (1-500, default 50)
        cursor: Pass the previous response's next_cursor to fetch the next page
        book_id: Book ID (-1 = default book)
        refresh: Bypass the cache TTL and force an incremental sync first
        account_id: Which account (empty = current)

    Returns: {items: [Bill], next_cursor, has_more}.
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await _get_bills_range(
            client,
            start_at=start_at,
            end_at=end_at,
            book_id=book_id,
            limit=limit,
            cursor=cursor or None,
            refresh=refresh,
        )
    return _stringify_bill_result(result)


@mcp.tool()
async def get_bill_by_id(
    bill_id: Any, book_id: int = -1, refresh: bool = False, account_id: str = ""
) -> dict[str, Any]:
    """Fetch a single bill by ID from the local cache (includes raw upstream fields).

    Args:
        bill_id: Bill ID to fetch
        book_id: Book ID to sync if the bill is not yet cached (-1 = default book)
        refresh: Bypass the cache TTL and force an incremental sync first
        account_id: Which account (empty = current)

    Returns: {ok: true, bill: Bill} or {ok: false, error: "bill_not_found"}.
    """
    normalized_bill_id = _bill_id(bill_id, "bill_id")
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        bill = await _get_bill_by_id(client, normalized_bill_id, book_id=book_id, refresh=refresh)
    if bill is None:
        return {"ok": False, "error": "bill_not_found", "bill_id": _bill_id_string(normalized_bill_id)}
    return {"ok": True, "bill": _stringify_bill_record(bill)}


@mcp.tool()
async def search_bills(
    bill_id: Any | None = None,
    start_at: int | None = None,
    end_at: int | None = None,
    book_id: int | None = None,
    bill_types: list[int] | None = None,
    category_ids: list[int] | None = None,
    asset_ids: list[int] | None = None,
    tag_ids: list[str] | None = None,
    keyword: str = "",
    min_amount: float | None = None,
    max_amount: float | None = None,
    currency: str = "",
    has_images: bool | None = None,
    counted: bool | None = None,
    sort_by: str = "occurred_at",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = DEFAULT_LIST_LIMIT,
    refresh: bool = False,
    account_id: str = "",
) -> dict[str, Any]:
    """Unified cache-backed bill search for agents.

    Replaces list_recent_bills, get_bills_range, and get_bill_by_id in normal
    MCP modes. full mode still exposes those legacy tools for debugging.
    """
    normalized_bill_id = _optional_bill_id(bill_id, "bill_id")
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await _search_bills(
            client,
            bill_id=normalized_bill_id,
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
    return _stringify_bill_result(result)


@mcp.tool()
async def export_bills(
    format: str = "csv",
    bill_id: Any | None = None,
    start_at: int | None = None,
    end_at: int | None = None,
    book_id: int | None = None,
    bill_types: list[int] | None = None,
    category_ids: list[int] | None = None,
    asset_ids: list[int] | None = None,
    tag_ids: list[str] | None = None,
    keyword: str = "",
    min_amount: float | None = None,
    max_amount: float | None = None,
    currency: str = "",
    has_images: bool | None = None,
    counted: bool | None = None,
    sort_by: str = "occurred_at",
    sort_order: str = "desc",
    refresh: bool = False,
    account_id: str = "",
) -> dict[str, Any]:
    """Export searched bills to a temporary CSV, JSON, or TXT file."""
    normalized_bill_id = _optional_bill_id(bill_id, "bill_id")
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        return await export_bills_to_file(
            client,
            file_format=format,
            base_url=_public_base_url(),
            bill_id=normalized_bill_id,
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
            refresh=refresh,
        )


@mcp.tool()
async def generate_report_image(
    report_type: str = "monthly_summary",
    bill_id: Any | None = None,
    start_at: int | None = None,
    end_at: int | None = None,
    book_id: int | None = None,
    bill_types: list[int] | None = None,
    category_ids: list[int] | None = None,
    asset_ids: list[int] | None = None,
    tag_ids: list[str] | None = None,
    keyword: str = "",
    min_amount: float | None = None,
    max_amount: float | None = None,
    currency: str = "",
    has_images: bool | None = None,
    counted: bool | None = None,
    sort_by: str = "occurred_at",
    sort_order: str = "desc",
    refresh: bool = False,
    account_id: str = "",
) -> dict[str, Any]:
    """Generate a report SVG image file from searched bills."""
    normalized_bill_id = _optional_bill_id(bill_id, "bill_id")
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        return await generate_report_image_file(
            client,
            report_type=report_type,
            base_url=_public_base_url(),
            bill_id=normalized_bill_id,
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
            refresh=refresh,
        )


@mcp.tool()
async def get_assets(account_id: str = "") -> dict[str, Any]:
    """List all financial assets with balances and resolved currency.

    Each asset includes `currency` (never null) and `currency_source`, one of:
    "asset" (from the asset itself), "account_default" (the account's base
    currency), or "fallback" (CNY when nothing else is known).

    Args:
        account_id: Which account (empty = current)
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await client.asset_list()
        base_currency = await _base_currency(client)
    raw_assets = result.get("data", {}).get("list", [])
    items = []
    for a in raw_assets:
        currency, source = _resolve_currency(a.get("currency"), base_currency)
        items.append(
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "balance": a.get("money"),
                "currency": currency,
                "currency_source": source,
                "type": a.get("type"),
                "stype": a.get("stype"),
                "status": a.get("status"),
            }
        )
    return {"items": items, "account_default_currency": base_currency}


@mcp.tool()
async def get_categories(cat_type: int = -1, book_id: int = -1, account_id: str = "") -> dict[str, Any]:
    """List categories. type: 0=expense, 1=income, -1=all.

    Args:
        cat_type: 0=expense, 1=income, -1=all
        book_id: Book ID (-1 = default book)
        account_id: Which account (empty = current)
    """
    client = await _get_client()
    t = cat_type if cat_type >= 0 else None
    with client.use_account(_authorized_account_id(account_id)):
        result = await client.category_list(book_id=book_id, cat_type=t)
    cats = result.get("data", {}).get("list", [])
    items = []
    for c in cats:
        items.append(
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "type": c.get("type"),
                "sublist": [{"id": sub.get("id"), "name": sub.get("name")} for sub in (c.get("sublist") or [])],
            }
        )
    return {"items": items}


@mcp.tool()
async def list_books(account_id: str = "") -> dict[str, Any]:
    """List accounting books. book_id -1 is the implicit default book.

    Always returns at least the default book. Each book has id, name, is_default, type.

    Args:
        account_id: Which account (empty = current)
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await client.book_list()
        raw_books = result.get("data", {}).get("list", [])
        if not raw_books and client.session is not None:
            raw_books = client.session.books or []
    items = [_normalize_book(b) for b in raw_books]
    if not items:
        items = [{"id": -1, "name": "默认账本", "is_default": True, "type": -1}]
    return {"items": items}


@mcp.tool()
async def get_tags(account_id: str = "") -> dict[str, Any]:
    """List all tags.

    Args:
        account_id: Which account (empty = current)
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await client.tag_list()
    return {"items": flatten_tag_items(result)}


@mcp.tool()
async def get_tags_enabled(account_id: str = "") -> dict[str, Any]:
    """Return whether Qianji's app-level tag feature switch is enabled.

    Args:
        account_id: Which account (empty = current)
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        return {"enabled": await _get_tags_enabled(client)}


async def set_tags_enabled(enabled: bool, account_id: str = "") -> dict[str, Any]:
    """Enable or disable Qianji's app-level tag feature switch.

    Args:
        enabled: True to enable Qianji tags; False to disable them.
        account_id: Which account (empty = current)
    """


@mcp.tool()
async def get_currencies(account_id: str = "") -> dict[str, Any]:
    """List currencies with explicit exchange-rate direction.

    Each currency reports `base_per_unit`: how many units of base_currency
    (the account's base currency, baseprice≈1.0) one unit of quote_currency is
    worth. E.g. base_currency=CNY, quote_currency=USD, base_per_unit=6.77 means
    1 USD = 6.77 CNY.

    Args:
        account_id: Which account (empty = current)
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await client.currency_list()
    currencies = result.get("data", {}).get("list", [])
    base_currency = _infer_base_currency(currencies) or "CNY"
    items = []
    for c in currencies:
        items.append(
            {
                "base_currency": base_currency,
                "quote_currency": c.get("symbol"),
                "base_per_unit": _as_float(c.get("baseprice")),
                "symbol": c.get("symbol"),
                "sign": c.get("sign"),
                "name": c.get("name"),
                "is_common": bool(c.get("iscommon", 0) == 1),
                "price_time": c.get("pricetime"),
            }
        )
    return {"base_currency": base_currency, "items": items}


@mcp.tool()
async def get_budgets(book_id: int = -1, account_id: str = "") -> dict[str, Any]:
    """List budgets read-only.

    Args:
        book_id: Book ID (-1 = default book).
        account_id: Which account (empty = current).
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        return await client.budget_list(book_id=book_id)


# ============ Write Tools ============


@mcp.tool()
async def upload_bill_image(
    filename: str,
    content_base64: str,
    mime_type: str = "application/octet-stream",
    account_id: str = "",
) -> dict[str, Any]:
    """Upload a bill image and return an image_key for create/update tools."""
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 must be valid base64") from exc
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        return await WriteService(client).upload_bill_image(filename=filename, content=content, mime_type=mime_type)


async def create_tag(name: str, group_id: str = "", color: str | None = None, account_id: str = "") -> dict[str, Any]:
    """Create a tag."""


async def update_tag(
    tag_id: str,
    name: str,
    group_id: str | None = None,
    color: str | None = None,
    account_id: str = "",
) -> dict[str, Any]:
    """Update a tag."""


async def delete_tag(tag_id: str, account_id: str = "") -> dict[str, Any]:
    """Delete a tag."""


async def archive_tag(tag_id: str, account_id: str = "") -> dict[str, Any]:
    """Archive a tag."""


async def create_tag_group(name: str, account_id: str = "") -> dict[str, Any]:
    """Create a tag group."""


async def update_tag_group(group_id: str, name: str, account_id: str = "") -> dict[str, Any]:
    """Update a tag group."""


async def delete_tag_group(group_id: str, account_id: str = "") -> dict[str, Any]:
    """Delete a tag group."""


async def reorder_tags(tag_ids: list[str], account_id: str = "") -> dict[str, Any]:
    """Reorder tags by Qianji tag ID."""


async def reorder_tag_groups(group_ids: list[str], account_id: str = "") -> dict[str, Any]:
    """Reorder tag groups by Qianji group ID."""


async def create_expense(
    amount: float,
    occurred_at: str,
    asset_id: int,
    category_id: int,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    tags: list[str] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Create an expense transaction.

    Args:
        amount: Positive expense amount. Do not use negative values to express expenses.
        occurred_at: Transaction time as timezone-aware ISO8601, e.g. 2026-04-12T19:30:00+08:00.
        asset_id: Source asset ID from get_assets; must be a valid positive ID.
        category_id: Expense category ID from get_categories(cat_type=0); required.
        remark: Optional note.
        book_id: Book ID (-1 = default book).
        account_id: Which account (empty = current).
        tags: Optional list of tag names. Missing tags are created automatically.
        images: Optional uploaded bill image keys from upload_bill_image.
    """


async def create_income(
    amount: float,
    occurred_at: str,
    asset_id: int,
    category_id: int,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    tags: list[str] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Create an income transaction.

    Args:
        amount: Positive income amount.
        occurred_at: Transaction time as timezone-aware ISO8601, e.g. 2026-04-12T19:30:00+08:00.
        asset_id: Destination asset ID from get_assets; must be a valid positive ID.
        category_id: Income category ID from get_categories(cat_type=1); required.
        remark: Optional note.
        book_id: Book ID (-1 = default book).
        account_id: Which account (empty = current).
        tags: Optional list of tag names. Missing tags are created automatically.
        images: Optional uploaded bill image keys from upload_bill_image.
    """


async def create_transfer(
    amount: float,
    occurred_at: str,
    from_asset_id: int,
    to_asset_id: int,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    tags: list[str] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Create a transfer transaction between two assets.

    Args:
        amount: Positive transfer amount.
        occurred_at: Transaction time as timezone-aware ISO8601, e.g. 2026-04-12T19:30:00+08:00.
        from_asset_id: Source asset ID from get_assets; must be valid and different from to_asset_id.
        to_asset_id: Destination asset ID from get_assets; must be valid and different from from_asset_id.
        remark: Optional note.
        book_id: Book ID (-1 = default book).
        account_id: Which account (empty = current).
        tags: Optional list of tag names. Missing tags are created automatically.
        images: Optional uploaded bill image keys from upload_bill_image.
    """


async def update_expense(
    bill_id: int,
    amount: float | None = None,
    occurred_at: str | None = None,
    asset_id: int | None = None,
    category_id: int | None = None,
    remark: str | None = None,
    book_id: int = -1,
    account_id: str = "",
    tags: list[str] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing expense transaction without changing its bill type.

    Args:
        bill_id: Existing expense bill ID to update.
        amount: New positive expense amount.
        occurred_at: New transaction time as timezone-aware ISO8601, e.g. 2026-04-12T19:30:00+08:00.
        asset_id: New asset ID from get_assets; must be valid when provided.
        category_id: New expense category ID from get_categories(cat_type=0); must be valid when provided.
        remark: New note. Pass an empty string to clear it.
        book_id: Book ID used to find/sync the bill (-1 = default book); does not move the bill between books.
        account_id: Which account (empty = current).
        tags: Optional list of tag names. Missing tags are created automatically. Pass [] to clear tags.
        images: Optional uploaded bill image keys from upload_bill_image. Pass [] to clear images.
    """


async def update_income(
    bill_id: int,
    amount: float | None = None,
    occurred_at: str | None = None,
    asset_id: int | None = None,
    category_id: int | None = None,
    remark: str | None = None,
    book_id: int = -1,
    account_id: str = "",
    tags: list[str] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing income transaction without changing its bill type.

    Args:
        bill_id: Existing income bill ID to update.
        amount: New positive income amount.
        occurred_at: New transaction time as timezone-aware ISO8601, e.g. 2026-04-12T19:30:00+08:00.
        asset_id: New asset ID from get_assets; must be valid when provided.
        category_id: New income category ID from get_categories(cat_type=1); must be valid when provided.
        remark: New note. Pass an empty string to clear it.
        book_id: Book ID used to find/sync the bill (-1 = default book); does not move the bill between books.
        account_id: Which account (empty = current).
        tags: Optional list of tag names. Missing tags are created automatically. Pass [] to clear tags.
        images: Optional uploaded bill image keys from upload_bill_image. Pass [] to clear images.
    """


async def update_transfer(
    bill_id: int,
    amount: float | None = None,
    occurred_at: str | None = None,
    from_asset_id: int | None = None,
    to_asset_id: int | None = None,
    remark: str | None = None,
    book_id: int = -1,
    account_id: str = "",
    tags: list[str] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing transfer transaction without changing its bill type.

    Args:
        bill_id: Existing transfer bill ID to update.
        amount: New positive transfer amount.
        occurred_at: New transaction time as timezone-aware ISO8601, e.g. 2026-04-12T19:30:00+08:00.
        from_asset_id: New source asset ID from get_assets; must be valid and different from to_asset_id.
        to_asset_id: New destination asset ID from get_assets; must be valid and different from from_asset_id.
        remark: New note. Pass an empty string to clear it.
        book_id: Book ID used to find/sync the bill (-1 = default book); does not move the bill between books.
        account_id: Which account (empty = current).
        tags: Optional list of tag names. Missing tags are created automatically. Pass [] to clear tags.
        images: Optional uploaded bill image keys from upload_bill_image. Pass [] to clear images.
    """


async def create_refund(
    source_bill_id: int,
    amount: float,
    occurred_at: str,
    asset_id: int | None = None,
    remark: str = "",
    account_id: str = "",
    currency: dict[str, Any] | None = None,
    images: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create a refund for an existing spending or reimbursement bill.

    currency: Optional foreign-currency override (e.g. {"code": "USD", "rate": 7.2}); omit to use the base currency.
    """


async def update_refund(
    source_bill_id: int,
    refund_bill_id: int,
    amount: float,
    occurred_at: str,
    asset_id: int | None = None,
    remark: str = "",
    account_id: str = "",
    images: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing refund bill using bill/refund2 with v.billid."""


async def create_reimbursement(
    source_bill_ids: list[int],
    amount: float,
    occurred_at: str,
    asset_id: int | None = None,
    remark: str = "",
    account_id: str = "",
    images: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create reimbursement records for one or more source bills."""


async def update_reimbursement(
    reimbursement_bill_id: int,
    source_bill_ids: list[int],
    amount: float,
    occurred_at: str,
    asset_id: int | None = None,
    remark: str = "",
    account_id: str = "",
    images: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing reimbursement record using baoxiao/baoxiao did."""


async def cancel_reimbursement(reimbursement_bill_ids: list[int], account_id: str = "") -> dict[str, Any]:
    """Cancel reimbursement status for one or more reimbursement bills."""


async def create_loan_asset(
    name: str,
    amount: float,
    startdate: str,
    enddate: str | None = None,
    linked_asset_id: int | None = None,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
    incount: bool = True,
) -> dict[str, Any]:
    """Create a loan asset for money lent out."""


async def update_loan_asset(
    asset_id: int,
    name: str | None = None,
    startdate: str | None = None,
    enddate: str | None = None,
    linked_asset_id: int | None = None,
    remark: str | None = None,
    book_id: int | None = None,
    account_id: str = "",
    images: list[str] | None = None,
    incount: bool | None = None,
) -> dict[str, Any]:
    """Update loan asset metadata. Principal changes use add/update loan principal tools."""


async def create_debt_asset(
    name: str,
    amount: float,
    startdate: str,
    enddate: str | None = None,
    linked_asset_id: int | None = None,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
    incount: bool = True,
) -> dict[str, Any]:
    """Create a debt asset for money borrowed in."""


async def update_debt_asset(
    asset_id: int,
    name: str | None = None,
    startdate: str | None = None,
    enddate: str | None = None,
    linked_asset_id: int | None = None,
    remark: str | None = None,
    book_id: int | None = None,
    account_id: str = "",
    images: list[str] | None = None,
    incount: bool | None = None,
) -> dict[str, Any]:
    """Update debt asset metadata. Principal changes use add/update debt principal tools."""


async def add_loan_principal(
    loan_asset_id: int,
    amount: float,
    occurred_at: str,
    from_asset_id: int = -1,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Add more lent principal to an existing loan asset."""


async def update_loan_principal(
    bill_id: int,
    amount: float,
    occurred_at: str,
    from_asset_id: int = -1,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing lent principal bill."""


async def add_debt_principal(
    debt_asset_id: int,
    amount: float,
    occurred_at: str,
    to_asset_id: int = -1,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Add more borrowed principal to an existing debt asset."""


async def update_debt_principal(
    bill_id: int,
    amount: float,
    occurred_at: str,
    to_asset_id: int = -1,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing borrowed principal bill."""


async def record_loan_collection(
    loan_asset_id: int,
    amount: float,
    occurred_at: str,
    to_asset_id: int = -1,
    interest_amount: float = 0.0,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Record collection against an existing loan asset, with optional interest income."""


async def update_loan_collection(
    bill_id: int,
    amount: float,
    occurred_at: str,
    to_asset_id: int = -1,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing loan collection bill."""


async def record_debt_repayment(
    debt_asset_id: int,
    amount: float,
    occurred_at: str,
    from_asset_id: int = -1,
    interest_amount: float = 0.0,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Record repayment against an existing debt asset, with optional interest spend."""


async def update_debt_repayment(
    bill_id: int,
    amount: float,
    occurred_at: str,
    from_asset_id: int = -1,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing debt repayment bill."""


# The templates above are registered below through one shared WriteService
# wrapper body. Signatures and docstrings stay on the templates because MCP
# clients consume them as the tool schema; each tool forwards its arguments
# to the WriteService method of the same name.


def _make_write_tool(template: Any, *, drop_none: bool = False) -> Any:
    @functools.wraps(template)
    async def write_tool(**kwargs: Any) -> dict[str, Any]:
        account_id = kwargs.pop("account_id", "")
        if drop_none:
            kwargs = {key: value for key, value in kwargs.items() if value is not None}
        client = await _get_client()
        with client.use_account(_authorized_account_id(account_id)):
            try:
                return await getattr(WriteService(client), template.__name__)(**kwargs)
            except TagFeatureDisabledError as exc:
                raise ValueError(exc.em.replace("set_tags_enabled(True)", "set_tags_enabled(true)")) from exc

    return write_tool


@mcp.tool()
async def create_bill(
    money: float,
    type: int = 0,
    category_id: int = 0,
    asset_id: int = -1,
    remark: str = "",
    book_id: int = -1,
    account_id: str = "",
    bill_time: int | None = None,
    target_asset_id: int = -1,
    tags: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    """low-level/full-mode/admin compatibility tool for creating a raw bill.

    Prefer create_expense/create_income/create_transfer for normal agent bookkeeping.

    Args:
        money: Amount
        type: 0=expense, 1=income, 2=transfer
        category_id: Category ID (get from get_categories)
        asset_id: Asset ID (get from get_assets, -1=unspecified)
        remark: Note
        book_id: Book ID (-1 = default book)
        account_id: Which account (empty = current)
        bill_time: Original bill time as Unix seconds
        target_asset_id: Transfer target asset ID, used only when type=2
        tags: Comma-separated tag names
        images: Optional uploaded bill image keys from upload_bill_image
    """
    tag_names = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else None
    client = await _get_client()
    normalized_images = validate_bill_image_keys(images)
    with client.use_account(_authorized_account_id(account_id)):
        create_kwargs = {
            "money": money,
            "bill_type": type,
            "cateid": category_id,
            "assetid": asset_id,
            "remark": remark,
            "book_id": book_id,
            "bill_time": bill_time,
            "target_asset_id": target_asset_id,
            "tags": tag_names,
        }
        if normalized_images is not None:
            create_kwargs["images"] = normalized_images
        result = await client.bill_create(**create_kwargs)
    sync = result.get("data", {}).get("sync_result", {}).get("bill", {})
    new_ids = sync.get("new_ids", [])
    return _stringify_known_bill_ids(
        {"ok": bool(new_ids), "bill_id": new_ids[0] if new_ids else None, "new_ids": new_ids}
    )


@mcp.tool()
async def delete_bill(bill_id: int, account_id: str = "") -> dict[str, Any]:
    """Delete a bill by ID.

    The local cache is only cleared after Qianji confirms the deletion upstream;
    otherwise the tool returns ok=false with error="upstream_not_confirmed".

    Args:
        bill_id: Bill ID to delete
        account_id: Which account (empty = current)
    """
    normalized_bill_id = _bill_id(bill_id, "bill_id")
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await WriteService(client).delete_bill(normalized_bill_id)
    deleted = bool(result.get("ok"))
    response: dict[str, Any] = {
        "ok": deleted,
        "bill_id": str(result.get("ids", {}).get("bill_id") or normalized_bill_id),
        "deleted": deleted,
    }
    if not deleted:
        response["error"] = result.get("error_code", "upstream_not_confirmed")
        response["message"] = result.get("message", "")
    return response


@mcp.tool()
async def update_bill(
    bill_id: int,
    money: float | None = None,
    type: int | None = None,
    category_id: int | None = None,
    asset_id: int | None = None,
    remark: str | None = None,
    book_id: int | None = None,
    target_asset_id: int | None = None,
    tags: str = "",
    images: list[str] | None = None,
    bill_time: int | None = None,
    account_id: str = "",
) -> dict[str, Any]:
    """Update a bill by ID. Locates the bill via the local cache (no page scanning).

    Args:
        bill_id: Bill ID to update
        money: New amount
        type: 0=expense, 1=income, 2=transfer
        category_id: New category ID
        asset_id: New asset ID
        remark: New note
        book_id: New book ID (-1 = default book)
        target_asset_id: New transfer target asset ID
        tags: Comma-separated tag names
        images: Optional uploaded bill image keys from upload_bill_image. Pass [] to clear images.
        bill_time: New bill time as Unix seconds
        account_id: Which account (empty = current)
    """
    normalized_bill_id = _bill_id(bill_id, "bill_id")
    patch: dict[str, Any] = {}
    if money is not None:
        patch["money"] = money
    if type is not None:
        patch["type"] = type
    if category_id is not None:
        patch["cateid"] = category_id
    if asset_id is not None:
        patch["assetid"] = asset_id
    if remark is not None:
        patch["remark"] = remark
    if book_id is not None:
        patch["book_id"] = book_id
    if target_asset_id is not None:
        patch["target_asset_id"] = target_asset_id
    if tags:
        patch["tags"] = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if images is not None:
        patch["images"] = validate_bill_image_keys(images) or []
    if bill_time is not None:
        patch["bill_time"] = bill_time
    if not patch:
        return {"ok": False, "error": "no_update_fields", "bill_id": _bill_id_string(normalized_bill_id)}

    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        result = await _update_bill_by_id(
            client, normalized_bill_id, patch, book_id=book_id if book_id is not None else -1
        )
    if result is None:
        return {"ok": False, "error": "bill_not_found", "bill_id": _bill_id_string(normalized_bill_id)}
    sync = result.get("data", {}).get("sync_result", {}).get("bill", {})
    return _stringify_known_bill_ids({"ok": True, "bill_id": normalized_bill_id, "sync_result": sync})


@mcp.tool()
async def sync_bills(book_id: int = -1, full: bool = False, account_id: str = "") -> dict[str, Any]:
    """Sync bills from Qianji into the local cache (use before/around heavy querying).

    This is the only tool that touches incremental-sync internals. Normal
    bookkeeping should use list_recent_bills / get_bills_range / get_bill_by_id,
    which auto-sync on first use.

    Args:
        book_id: Book ID to sync (-1 = default book)
        full: True to re-pull everything; False for an incremental sync
        account_id: Which account (empty = current)

    Returns: {synced, deleted, pages, books, last_sync_times, truncated}.
    A single sync with book_id=-1 walks every book (server-driven).
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        return await _sync_bills_to_cache(client, book_id=book_id, full=full)


@mcp.tool()
async def get_bill_cache_ttl(account_id: str = "") -> dict[str, Any]:
    """Show the effective bill-cache TTL (seconds) and where it comes from.

    Args:
        account_id: Which account (empty = current)

    Returns: {ttl_seconds, source: "account_override"|"env_default", env_default}.
    """
    client = await _get_client()
    with client.use_account(_authorized_account_id(account_id)):
        aid = client.current_account
        override = await client._session_mgr.get_account_setting(aid, BILL_CACHE_TTL_KEY)
        ttl = await _resolve_cache_ttl(client)
    source = "account_override" if override is not None else "env_default"
    return {"ttl_seconds": ttl, "source": source, "env_default": DEFAULT_BILL_CACHE_TTL}


async def set_bill_cache_ttl(seconds: int, account_id: str = "") -> dict[str, Any]:
    """Set this account's bill-cache TTL (seconds). 0 disables the TTL (trust cache
    until an explicit sync). A negative value clears the override and falls back to
    the env default (QIANJI_BILL_CACHE_TTL).

    Args:
        seconds: TTL in seconds; 0 to disable, negative to clear the override
        account_id: Which account (empty = current)

    Returns: {ok, account_id, ttl_seconds, source}.
    """


# drop_none mirrors the old hand-written update_loan_asset/update_debt_asset
# bodies, which omitted unset optional fields instead of forwarding None.
_WRITE_TOOL_TEMPLATES: tuple[tuple[Any, bool], ...] = (
    (set_tags_enabled, False),
    (set_bill_cache_ttl, False),
    (create_tag, False),
    (update_tag, False),
    (delete_tag, False),
    (archive_tag, False),
    (create_tag_group, False),
    (update_tag_group, False),
    (delete_tag_group, False),
    (reorder_tags, False),
    (reorder_tag_groups, False),
    (create_expense, False),
    (create_income, False),
    (create_transfer, False),
    (update_expense, False),
    (update_income, False),
    (update_transfer, False),
    (create_refund, False),
    (update_refund, False),
    (create_reimbursement, False),
    (update_reimbursement, False),
    (cancel_reimbursement, False),
    (create_loan_asset, False),
    (update_loan_asset, True),
    (create_debt_asset, False),
    (update_debt_asset, True),
    (add_loan_principal, False),
    (update_loan_principal, False),
    (add_debt_principal, False),
    (update_debt_principal, False),
    (record_loan_collection, False),
    (update_loan_collection, False),
    (record_debt_repayment, False),
    (update_debt_repayment, False),
)

for _template, _drop_none in _WRITE_TOOL_TEMPLATES:
    mcp.tool()(_make_write_tool(_template, drop_none=_drop_none))


# ============ Generic ============


@mcp.tool()
async def call_api(ctrl: str, act: str, params: str = "{}", account_id: str = "") -> str:
    """Call any Qianji API endpoint directly.

    Args:
        ctrl: Controller (e.g. 'asset', 'bill', 'book', 'budget', 'saving')
        act: Action (e.g. 'list', 'submit', 'delete')
        params: JSON string of parameters
        account_id: Which account (empty = current)
    """
    client = await _get_client()
    p = json.loads(params) if params and params != "{}" else None
    with client.use_account(_authorized_account_id(account_id)):
        result = await client.call(ctrl, act, p)
    return _fmt(result)


# ============ Upstream-compatible dedicated tools ============


def _make_endpoint_tool(endpoint: EndpointSpec):
    async def endpoint_tool(**kwargs) -> str:
        account_id = kwargs.pop("account_id", "")
        client = await _get_client()
        method = getattr(client, endpoint.method_name)
        if endpoint.public:
            _authorized_account_id(account_id)
            result = await method(**kwargs)
        else:
            with client.use_account(_authorized_account_id(account_id)):
                result = await method(**kwargs)
        return _fmt(result)

    endpoint_tool.__name__ = endpoint.tool_name
    endpoint_tool.__qualname__ = endpoint.tool_name
    endpoint_tool.__doc__ = f"{endpoint.description or endpoint.path} ({endpoint.path})."
    endpoint_tool.__signature__ = endpoint_signature(endpoint, include_account_id=True, return_annotation=str)  # type: ignore[attr-defined]
    return endpoint_tool


for _endpoint in ENDPOINTS:
    mcp.tool(name=_endpoint.tool_name, description=_endpoint.description or _endpoint.path)(
        _make_endpoint_tool(_endpoint)
    )

_patch_bill_id_tool_schemas()
_ALL_TOOLS = dict(_registered_tools())
_install_mode_aware_mcp_handlers()
configure_mcp_tools()
