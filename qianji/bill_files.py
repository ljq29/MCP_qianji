from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime
from html import escape
from typing import Any

from qianji.bill_ops import BILL_LOCAL_TZ, search_all_bill_items
from qianji.file_service import build_download_url, create_generated_file, resolve_file_retention_days

EXPORT_FORMATS = {"csv", "json", "txt"}
REPORT_TYPES = {"monthly_summary", "category_summary", "daily_calendar", "trend"}
MAX_EXPORT_ITEMS = 50_000
MAX_REPORT_ROWS = 12

EXPORT_FIELDS = [
    "id",
    "occurred_at",
    "bill_type",
    "amount",
    "currency",
    "book_id",
    "asset_id",
    "from_asset_id",
    "to_asset_id",
    "category_id",
    "remark",
    "tag_ids",
]


def _criteria_without_none(criteria: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in criteria.items() if value is not None and value != ""}


async def _all_search_items(client: Any, criteria: dict[str, Any]) -> dict[str, Any]:
    search_criteria = _criteria_without_none(criteria)
    filtered = await search_all_bill_items(client, **search_criteria)
    items = filtered[:MAX_EXPORT_ITEMS]
    return {
        "items": items,
        "total": len(filtered),
        "total_returned": len(items),
        "truncated": len(filtered) > len(items),
        "max_items": MAX_EXPORT_ITEMS,
    }


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _csv_bytes(items: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        row = {key: _csv_safe(value) for key, value in dict(item).items()}
        row["tag_ids"] = _csv_safe(json.dumps(item.get("tag_ids", []), ensure_ascii=False, separators=(",", ":")))
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _json_bytes(items: list[dict[str, Any]]) -> bytes:
    return json.dumps({"items": items, "total": len(items)}, ensure_ascii=False, indent=2).encode("utf-8")


def _txt_bytes(items: list[dict[str, Any]]) -> bytes:
    lines = []
    for item in items:
        occurred_at = datetime.fromtimestamp(int(item.get("occurred_at", 0)), tz=BILL_LOCAL_TZ).isoformat()
        lines.append(
            f"{item.get('id')} | {occurred_at} | {item.get('bill_type')} | "
            f"{item.get('amount')} {item.get('currency')} | {item.get('remark', '')}"
        )
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _format_export(items: list[dict[str, Any]], file_format: str) -> tuple[bytes, str, str]:
    selected = file_format.lower()
    if selected == "csv":
        return _csv_bytes(items), "bills.csv", "text/csv; charset=utf-8"
    if selected == "json":
        return _json_bytes(items), "bills.json", "application/json"
    if selected == "txt":
        return _txt_bytes(items), "bills.txt", "text/plain; charset=utf-8"
    raise ValueError(f"Unsupported export format: {file_format}")


async def export_bills_to_file(
    client: Any,
    *,
    file_format: str = "csv",
    base_url: str = "",
    **criteria: Any,
) -> dict[str, Any]:
    selected_format = (file_format or "csv").lower()
    if selected_format not in EXPORT_FORMATS:
        raise ValueError(f"format must be one of: {', '.join(sorted(EXPORT_FORMATS))}")
    collected = await _all_search_items(client, criteria)
    items = collected["items"]
    content, filename, content_type = _format_export(items, selected_format)
    mgr = client._session_mgr
    retention_days = await resolve_file_retention_days(mgr)
    file_info = await create_generated_file(
        mgr,
        filename=filename,
        content=content,
        content_type=content_type,
        retention_days=retention_days,
    )
    return {
        **file_info,
        "download_url": build_download_url(file_info["file_id"], base_url=base_url),
        "format": selected_format,
        "total": collected["total"],
        "total_returned": collected["total_returned"],
        "truncated": collected["truncated"],
        "max_items": collected["max_items"],
    }


def _month_label(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=BILL_LOCAL_TZ).strftime("%Y-%m")


def _day_label(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=BILL_LOCAL_TZ).strftime("%Y-%m-%d")


def _summary_rows(items: list[dict[str, Any]], report_type: str) -> tuple[list[tuple[str, float]], bool]:
    totals: dict[str, float] = defaultdict(float)
    for item in items:
        amount = float(item.get("amount") or 0)
        if report_type == "monthly_summary":
            key = _month_label(int(item.get("occurred_at", 0)))
        elif report_type == "category_summary":
            key = f"category:{item.get('category_id', -1)}"
        else:
            # daily_calendar and trend share the same per-day aggregation.
            key = _day_label(int(item.get("occurred_at", 0)))
        totals[key] += amount
    rows = sorted(totals.items(), key=lambda row: row[0])
    return rows[:MAX_REPORT_ROWS], len(rows) > MAX_REPORT_ROWS


def _svg_report(items: list[dict[str, Any]], report_type: str) -> bytes:
    rows, truncated = _summary_rows(items, report_type)
    display_rows = rows or [("no data", 0.0)]
    width = 960
    height = 220 + (len(display_rows) + (1 if truncated else 0)) * 42
    max_amount = max((amount for _, amount in rows), default=1.0) or 1.0
    row_markup = []
    for idx, (label, amount) in enumerate(display_rows):
        y = 150 + idx * 42
        bar_width = int((amount / max_amount) * 520) if max_amount else 0
        row_markup.append(
            f'<text x="48" y="{y}" font-size="18" fill="#1f2937">{escape(label)}</text>'
            f'<rect x="250" y="{y - 18}" width="{bar_width}" height="24" rx="4" fill="#167b83" />'
            f'<text x="{270 + bar_width}" y="{y}" font-size="18" fill="#111827">{amount:.2f}</text>'
        )
    if truncated:
        y = 150 + len(display_rows) * 42
        row_markup.append(
            f'<text x="48" y="{y}" font-size="16" fill="#b91c1c">'
            f"truncated: showing first {MAX_REPORT_ROWS} groups</text>"
        )
    total = sum(float(item.get("amount") or 0) for item in items)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="#f8fafc" />'
        f'<text x="48" y="64" font-size="32" font-weight="700" fill="#111827">{escape(report_type)}</text>'
        f'<text x="48" y="100" font-size="18" fill="#475569">Bills: {len(items)}  Total: {total:.2f}</text>'
        f'{"".join(row_markup)}'
        "</svg>"
    )
    return svg.encode("utf-8")


async def generate_report_image_file(
    client: Any,
    *,
    report_type: str = "monthly_summary",
    base_url: str = "",
    **criteria: Any,
) -> dict[str, Any]:
    selected_type = (report_type or "monthly_summary").lower()
    if selected_type not in REPORT_TYPES:
        raise ValueError(f"report_type must be one of: {', '.join(sorted(REPORT_TYPES))}")
    collected = await _all_search_items(client, criteria)
    items = collected["items"]
    content = _svg_report(items, selected_type)
    mgr = client._session_mgr
    retention_days = await resolve_file_retention_days(mgr)
    file_info = await create_generated_file(
        mgr,
        filename=f"{selected_type}.svg",
        content=content,
        content_type="image/svg+xml",
        retention_days=retention_days,
    )
    return {
        **file_info,
        "download_url": build_download_url(file_info["file_id"], base_url=base_url),
        "report_type": selected_type,
        "total": collected["total"],
        "total_returned": collected["total_returned"],
        "truncated": collected["truncated"],
        "max_items": collected["max_items"],
    }
