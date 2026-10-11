from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from qianji.constants import DEFAULT_BILL_CACHE_TTL, BillType
from qianji.image_upload import validate_bill_image_keys

DEFAULT_RANGE_MAX_PAGES = 50
HARD_RANGE_MAX_PAGES = 200
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500
MAX_SEARCH_PAGE_SIZE = MAX_LIST_LIMIT
BILL_CACHE_TTL_KEY = "bill_cache_ttl"
BASE_CURRENCY_CACHE_TTL = 300
BILL_LOCAL_TZ = timezone(timedelta(hours=8))


def _sync_data(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data", result)
    return data if isinstance(data, dict) else {}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp_max_pages(max_pages: int) -> int:
    return max(1, min(HARD_RANGE_MAX_PAGES, int(max_pages or DEFAULT_RANGE_MAX_PAGES)))


def apply_bill_patch(bill: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(bill)

    if "money" in patch:
        updated["money"] = patch["money"]
    if "type" in patch:
        updated["type"] = patch["type"]
    if "cateid" in patch:
        updated["cateid"] = patch["cateid"]
    if "assetid" in patch:
        updated["assetid"] = patch["assetid"]
    if "remark" in patch:
        updated["remark"] = patch["remark"]
    if "book_id" in patch:
        updated["bookid"] = patch["book_id"]
    if "bill_time" in patch:
        updated["time"] = patch["bill_time"]

    bill_type = _as_int(updated.get("type"))
    if bill_type == 2:
        updated["fromid"] = updated.get("assetid", -1)
        if "target_asset_id" in patch:
            updated["targetid"] = patch["target_asset_id"]
    else:
        updated["fromid"] = -1
        updated["targetid"] = -1

    if "tags" in patch:
        extra = updated.get("extra")
        if not isinstance(extra, dict):
            extra = {}
        else:
            extra = dict(extra)
        extra["tags"] = patch["tags"]
        updated["extra"] = extra

    if "images" in patch:
        updated["images"] = validate_bill_image_keys(patch["images"]) or []

    return updated


_BILL_TYPE_NAMES = {t.value: t.name.lower() for t in BillType}


def normalize_bill(
    raw: dict[str, Any],
    *,
    default_currency: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Project a raw upstream bill dict into a stable, agent-facing shape.

    `currency` is never None: a per-bill currency (cross-currency txns) wins,
    else the account default, else CNY. `currency_source` records which applied.
    """
    type_code = _as_int(raw.get("type"))
    extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else None
    tags = extra.get("tags") if isinstance(extra, dict) else None
    currency, currency_source = _resolve_bill_currency(extra, default_currency)
    occurred_at = _as_int(raw.get("time") or raw.get("createtime"))
    item: dict[str, Any] = {
        "id": _as_int(raw.get("id"), -1),
        "bill_type": _BILL_TYPE_NAMES.get(type_code, f"type_{type_code}"),
        "type_code": type_code,
        "counted": type_code != BillType.NOT_COUNTED.value,
        "amount": raw.get("money"),
        "currency": currency,
        "currency_source": currency_source,
        "occurred_at": occurred_at,
        "occurred_at_iso": datetime.fromtimestamp(occurred_at, tz=BILL_LOCAL_TZ).isoformat(),
        "book_id": _as_int(raw.get("bookid"), -1),
        "asset_id": _as_int(raw.get("assetid"), -1),
        "from_asset_id": _as_int(raw.get("fromid"), -1),
        "to_asset_id": _as_int(raw.get("targetid"), -1),
        "category_id": _as_int(raw.get("cateid"), -1),
        "remark": raw.get("remark", ""),
        "tag_ids": list(tags) if isinstance(tags, list) else [],
    }
    if include_raw:
        item["raw"] = raw
    return item


def _bill_currency(extra: Any) -> str | None:
    if isinstance(extra, dict):
        curr = extra.get("curr")
        if isinstance(curr, dict):
            symbol = curr.get("srcSymbol") or curr.get("symbol")
            if symbol:
                return str(symbol)
    return None


def _resolve_bill_currency(extra: Any, default_currency: str | None) -> tuple[str, str]:
    per_bill = _bill_currency(extra)
    if per_bill:
        return per_bill, "bill"
    if default_currency:
        return default_currency, "account_default"
    return "CNY", "fallback"


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _normalize_filter_values(values: list[Any] | tuple[Any, ...] | set[Any] | None) -> set[str]:
    if values is None:
        return set()
    result: set[str] = set()
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        text = str(value).strip()
        if text:
            result.add(text)
    return result


def _bill_tag_ids(raw: dict[str, Any]) -> list[str]:
    extra = raw.get("extra")
    tags = extra.get("tags") if isinstance(extra, dict) else None
    if not isinstance(tags, list):
        raw_tags = raw.get("tags")
        tags = raw_tags if isinstance(raw_tags, list) else []
    return [str(tag).strip() for tag in tags if tag is not None and not isinstance(tag, bool) and str(tag).strip()]


def _bill_images(raw: dict[str, Any]) -> list[str]:
    candidates = [raw.get("images"), raw.get("imageurls"), raw.get("imageUrls"), raw.get("imgs")]
    extra = raw.get("extra")
    if isinstance(extra, dict):
        candidates.extend([extra.get("images"), extra.get("imageurls"), extra.get("imageUrls"), extra.get("imgs")])
    result: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            result.extend(str(item) for item in candidate if item)
        elif isinstance(candidate, str) and candidate.strip():
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                result.append(candidate.strip())
            else:
                if isinstance(parsed, list):
                    result.extend(str(item) for item in parsed if item)
                elif parsed:
                    result.append(str(parsed))
    return result


def _asset_ids_for_filter(raw: dict[str, Any]) -> set[str]:
    ids = {raw.get("assetid"), raw.get("fromid"), raw.get("targetid")}
    return {str(_as_int(value, -1)) for value in ids if _as_int(value, -1) != -1}


def _keyword_blob(raw: dict[str, Any]) -> str:
    fields = [
        raw.get("remark"),
        raw.get("catename"),
        raw.get("categoryname"),
        raw.get("assetname"),
        raw.get("fromact"),
        raw.get("targetact"),
    ]
    return " ".join(str(value) for value in fields if value is not None).lower()


def _page_number(page: int | None) -> int:
    try:
        normalized = int(page or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, normalized)


def _search_page_size(page_size: int | None) -> int:
    try:
        normalized = int(page_size or DEFAULT_LIST_LIMIT)
    except (TypeError, ValueError):
        return DEFAULT_LIST_LIMIT
    return max(1, min(MAX_SEARCH_PAGE_SIZE, normalized))


def _validate_time_range(start_at: int | None, end_at: int | None) -> None:
    if start_at is not None and end_at is not None and int(end_at) < int(start_at):
        raise ValueError("end_at must be greater than or equal to start_at")


def infer_base_currency(currencies: list[dict]) -> str | None:
    """The account base currency is the one whose baseprice is ~1.0."""
    best_symbol = None
    best_delta = None
    for c in currencies:
        raw_price = c.get("baseprice")
        if raw_price is None:
            continue
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        delta = abs(price - 1.0)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_symbol = c.get("symbol")
    if best_symbol and best_delta is not None and best_delta < 0.01:
        return str(best_symbol)
    return None


async def get_base_currency(client: Any) -> str:
    """Resolve the account's base currency, cached per account with a TTL.

    Upstream errors (timeouts, auth failures) propagate to the caller; the CNY
    fallback only applies when the account genuinely has no base currency.
    """
    account_id = _account_id(client)
    cache = getattr(client, "_base_currency_cache", None)
    if cache is None:
        cache = {}
        client._base_currency_cache = cache
    entry = cache.get(account_id)
    now = time.time()
    if entry is not None and now - entry[1] < BASE_CURRENCY_CACHE_TTL:
        return entry[0]
    result = await client.currency_list()
    currencies = _sync_data(result).get("list", [])
    if not isinstance(currencies, list):
        currencies = []
    base = infer_base_currency(currencies)
    if base is None:
        base = entry[0] if entry is not None else "CNY"
    cache[account_id] = (base, now)
    return base


def encode_cursor(bill_time: int, bill_id: int) -> str:
    payload = json.dumps({"t": int(bill_time), "id": int(bill_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[int, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return int(payload["t"]), int(payload["id"])
    except (binascii.Error, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid pagination cursor: {cursor!r}") from exc


def clamp_limit(limit: int | None) -> int:
    if not limit or limit < 1:
        return DEFAULT_LIST_LIMIT
    return min(MAX_LIST_LIMIT, int(limit))


def _account_id(client: Any) -> str:
    account_id = client.current_account
    if not account_id:
        from qianji.errors import NotLoggedInError

        raise NotLoggedInError()
    return str(account_id)


async def resolve_cache_ttl(client: Any) -> int:
    """Effective bill-cache TTL in seconds: per-account override wins over the env default."""
    account_id = _account_id(client)
    override = await client._session_mgr.get_account_setting(account_id, BILL_CACHE_TTL_KEY)
    if override is not None:
        try:
            return int(override)
        except (TypeError, ValueError):
            pass
    return DEFAULT_BILL_CACHE_TTL


async def sync_bills_to_cache(
    client: Any,
    *,
    book_id: int = -1,
    full: bool = False,
    max_pages: int = HARD_RANGE_MAX_PAGES,
) -> dict[str, Any]:
    """Pull bills via syncv2/pull and persist to cache, mirroring the official client.

    The upstream protocol is server-driven: each page response carries the
    bookid/pageoffset/pagesign to send for the next page, and a multi-book
    `lasttimes` map. A single bookid=-1 sync walks every book. We echo the
    server's cursor verbatim (never increment locally) and store the server's
    returned `lasttimes` as the authoritative incremental watermark.
    """
    account_id = _account_id(client)
    mgr = client._session_mgr
    pages = clamp_max_pages(max_pages)

    if full:
        last_times: dict[str, int] = {str(book_id): 0}
    else:
        last_times = await mgr.get_all_bill_sync_times(account_id)
        if not last_times:
            last_times = {str(book_id): 0}

    synced = 0
    deleted = 0
    page_count = 0
    hasmore = False
    # Server-driven cursor; first page starts at the requested book, offset 0, empty pagesign.
    cur_book = book_id
    cur_offset = 0
    cur_sign = ""
    accumulated_times: dict[str, int] = {}

    for _ in range(pages):
        result = await client.sync_pull(
            book_id=cur_book,
            page_offset=cur_offset,
            last_times=last_times,
            page_sign=cur_sign,
        )
        data = _sync_data(result)
        page_count += 1

        changes = [b for b in (data.get("changes") or []) if isinstance(b, dict)]
        if changes:
            synced += await mgr.cache_upsert_bills(account_id, changes)
        deletes = data.get("deletes") or []
        if deletes:
            deleted += await mgr.cache_delete_bills(account_id, deletes)

        resp_times = data.get("lasttimes")
        if isinstance(resp_times, dict):
            for k, v in resp_times.items():
                accumulated_times[str(k)] = _as_int(v)

        hasmore = bool(data.get("hasmore", 0))
        if not hasmore:
            break
        # Echo the server's cursor for the next page (do NOT increment locally).
        cur_book = _as_int(data.get("bookid"), cur_book)
        cur_offset = _as_int(data.get("pageoffset"), cur_offset)
        cur_sign = str(data.get("pagesign") or "")

    truncated = hasmore and page_count >= pages
    now = time.time()
    if not truncated:
        # Persist only the server's authoritative per-book watermarks. The
        # requested book gets a zero marker only when it has no watermark yet;
        # an existing non-zero watermark must never be reset by a response
        # that simply omitted lasttimes.
        for bid, last_time in accumulated_times.items():
            await mgr.set_bill_sync_state(account_id, int(bid), last_time, now)
        if str(book_id) not in accumulated_times:
            existing_time, _ = await mgr.get_bill_sync_state(account_id, book_id)
            if existing_time == 0:
                await mgr.set_bill_sync_state(account_id, book_id, 0, now)

    return {
        "synced": synced,
        "deleted": deleted,
        "pages": page_count,
        "books": sorted(int(b) for b in (set(accumulated_times) | {str(book_id)})),
        "last_sync_times": {b: accumulated_times.get(b, 0) for b in accumulated_times},
        "truncated": truncated,
    }


async def ensure_bill_cache(client: Any, book_id: int = -1, *, refresh: bool = False) -> None:
    """Make sure the local cache is populated; full sync on first use, else TTL-gated incremental.

    A full sync always starts from book_id=-1 so the server walks every book in
    one pass (matching the official client). After that, an incremental sync runs
    only when forced (`refresh=True`) or when the cache is older than the resolved
    TTL (`resolve_cache_ttl`). TTL=0 means trust the cache until an explicit sync.

    Syncs are serialized per account through the client's bill-sync lock, and the
    sync state is re-checked inside the lock so concurrent cold-cache callers
    trigger exactly one full sync.
    """
    account_id = _account_id(client)
    lock_for = getattr(client, "_bill_sync_lock_for", None)
    if lock_for is None:
        await _ensure_bill_cache_body(client, account_id, book_id, refresh=refresh)
        return
    async with lock_for(account_id):
        await _ensure_bill_cache_body(client, account_id, book_id, refresh=refresh)


async def _ensure_bill_cache_body(client: Any, account_id: str, book_id: int, *, refresh: bool) -> None:
    _, synced_at = await client._session_mgr.get_bill_sync_state(account_id, book_id)
    if synced_at == 0:
        await sync_bills_to_cache(client, book_id=-1, full=True)
        return
    if refresh:
        await sync_bills_to_cache(client, book_id=book_id, full=False)
        return
    ttl = await resolve_cache_ttl(client)
    if ttl > 0 and (time.time() - synced_at) > ttl:
        await sync_bills_to_cache(client, book_id=book_id, full=False)


def _page_from_cache(
    rows: list[dict[str, Any]],
    limit: int,
    *,
    default_currency: str | None = None,
    include_raw: bool = False,
) -> dict[str, Any]:
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [normalize_bill(b, default_currency=default_currency, include_raw=include_raw) for b in page]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = encode_cursor(_as_int(last.get("time") or last.get("createtime")), _as_int(last.get("id"), -1))
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


async def list_recent_bills(
    client: Any,
    *,
    book_id: int = -1,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    account_id = _account_id(client)
    before = decode_cursor(cursor)
    if cursor is None:
        await ensure_bill_cache(client, book_id, refresh=refresh)
    limit = clamp_limit(limit)
    rows = await client._session_mgr.cache_list_bills(
        account_id,
        book_id=book_id,
        limit=limit + 1,
        before_time=before[0] if before else None,
        before_id=before[1] if before else None,
    )
    default_currency = await get_base_currency(client)
    return _page_from_cache(rows, limit, default_currency=default_currency)


async def get_bills_range(
    client: Any,
    *,
    start_at: int,
    end_at: int,
    book_id: int = -1,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    account_id = _account_id(client)
    _validate_time_range(start_at, end_at)
    before = decode_cursor(cursor)
    if cursor is None:
        await ensure_bill_cache(client, book_id, refresh=refresh)
    limit = clamp_limit(limit)
    rows = await client._session_mgr.cache_list_bills(
        account_id,
        book_id=book_id,
        start_time=start_at,
        end_time=end_at,
        limit=limit + 1,
        before_time=before[0] if before else None,
        before_id=before[1] if before else None,
    )
    default_currency = await get_base_currency(client)
    return _page_from_cache(rows, limit, default_currency=default_currency)


async def _filtered_bill_items(
    client: Any,
    *,
    bill_id: int | None = None,
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
    include_raw: bool = False,
) -> list[dict[str, Any]]:
    """Full filtered + sorted match set: one cache load, one filter pass, one sort."""
    account_id = _account_id(client)
    _validate_time_range(start_at, end_at)
    selected_book_id = book_id if book_id is not None else -1
    await ensure_bill_cache(client, selected_book_id, refresh=refresh)

    default_currency = await get_base_currency(client)
    if bill_id is not None:
        raw = await client._session_mgr.cache_get_bill(account_id, int(bill_id))
        rows = [raw] if raw is not None else []
    else:
        rows = await client._session_mgr.cache_search_bills(
            account_id,
            book_id=book_id,
            start_time=start_at,
            end_time=end_at,
            bill_types=bill_types,
            sort_order=sort_order,
        )

    type_filter = {int(value) for value in bill_types or []}
    category_filter = {str(int(value)) for value in category_ids or []}
    asset_filter = {str(int(value)) for value in asset_ids or []}
    tag_filter = _normalize_filter_values(tag_ids)
    keyword_text = (keyword or "").strip().lower()
    currency_filter = (currency or "").strip().upper()
    counted_filter = _as_bool(counted)

    filtered: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        bill_time = _as_int(raw.get("time") or raw.get("createtime"))
        if start_at is not None and bill_time < int(start_at):
            continue
        if end_at is not None and bill_time > int(end_at):
            continue
        if book_id is not None and _as_int(raw.get("bookid"), -1) != int(book_id):
            continue
        type_code = _as_int(raw.get("type"))
        if type_filter and type_code not in type_filter:
            continue
        if category_filter and str(_as_int(raw.get("cateid"), -1)) not in category_filter:
            continue
        if asset_filter and _asset_ids_for_filter(raw).isdisjoint(asset_filter):
            continue
        if tag_filter and set(_bill_tag_ids(raw)).isdisjoint(tag_filter):
            continue
        if keyword_text and keyword_text not in _keyword_blob(raw):
            continue
        amount = _as_float(raw.get("money"))
        if min_amount is not None and (amount is None or amount < float(min_amount)):
            continue
        if max_amount is not None and (amount is None or amount > float(max_amount)):
            continue
        item = normalize_bill(raw, default_currency=default_currency, include_raw=include_raw)
        if currency_filter and str(item.get("currency", "")).upper() != currency_filter:
            continue
        if has_images is not None and bool(_bill_images(raw)) is not bool(has_images):
            continue
        if counted_filter is not None and bool(item.get("counted")) is not counted_filter:
            continue
        filtered.append(item)

    reverse = (sort_order or "desc").lower() != "asc"
    selected_sort = (sort_by or "occurred_at").lower()
    key: Callable[[dict[str, Any]], Any]
    if selected_sort == "amount":
        def amount_key(item: dict[str, Any]) -> tuple[bool, float, Any]:
            amount = _as_float(item.get("amount"))
            return amount is None, amount or 0.0, item.get("id", -1)

        key = amount_key
    elif selected_sort in {"id", "bill_id"}:
        def id_key(item: dict[str, Any]) -> int:
            return int(item.get("id", -1))

        key = id_key
    else:
        def occurred_at_key(item: dict[str, Any]) -> tuple[int, int]:
            return int(item.get("occurred_at", 0)), int(item.get("id", -1))

        key = occurred_at_key
    filtered.sort(key=key, reverse=reverse)
    return filtered


async def search_all_bill_items(client: Any, **criteria: Any) -> list[dict[str, Any]]:
    """Every bill matching the search_bills criteria, unpaginated, in sort order.

    Single cache scan + single filter pass, for callers (exports, reports) that
    need the whole match set instead of one page.
    """
    return await _filtered_bill_items(client, **criteria)


async def search_bills(
    client: Any,
    *,
    bill_id: int | None = None,
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
    include_raw: bool = False,
) -> dict[str, Any]:
    filtered = await _filtered_bill_items(
        client,
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
        refresh=refresh,
        include_raw=include_raw,
    )

    reverse = (sort_order or "desc").lower() != "asc"
    selected_sort = (sort_by or "occurred_at").lower()
    current_page = _page_number(page)
    size = _search_page_size(page_size)
    start = (current_page - 1) * size
    end = start + size
    items = filtered[start:end]
    return {
        "items": items,
        "total": len(filtered),
        "page": current_page,
        "page_size": size,
        "has_more": end < len(filtered),
        "sort_by": selected_sort,
        "sort_order": "desc" if reverse else "asc",
    }


async def get_bill_by_id(
    client: Any, bill_id: int, *, book_id: int = -1, refresh: bool = False
) -> dict[str, Any] | None:
    account_id = _account_id(client)
    raw = None if refresh else await client._session_mgr.cache_get_bill(account_id, bill_id)
    if raw is None:
        await ensure_bill_cache(client, book_id, refresh=refresh)
        raw = await client._session_mgr.cache_get_bill(account_id, bill_id)
    if raw is None:
        return None
    default_currency = await get_base_currency(client)
    return normalize_bill(raw, default_currency=default_currency, include_raw=True)


async def update_bill_by_id(
    client: Any,
    bill_id: int,
    patch: dict[str, Any],
    *,
    book_id: int = -1,
) -> dict[str, Any] | None:
    """Locate a bill via the local cache (no page scanning) and apply a patch upstream."""
    account_id = _account_id(client)
    mgr = client._session_mgr
    raw = await mgr.cache_get_bill(account_id, bill_id)
    if raw is None:
        await ensure_bill_cache(client, book_id)
        raw = await mgr.cache_get_bill(account_id, bill_id)
    if raw is None:
        return None
    updated = apply_bill_patch(raw, patch)
    result = await client.bill_update(updated)
    await mgr.cache_upsert_bills(account_id, [updated])
    return result
