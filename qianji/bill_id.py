from __future__ import annotations

import secrets
import time
from itertools import count
from typing import Any

# Single process-wide counter shared by every bill-creating module. Seeding it
# with a random start value additionally decorrelates IDs across processes that
# boot within the same millisecond.
_COUNTER = count(secrets.randbelow(1_000_000))


def new_bill_id() -> int:
    return int(time.time() * 1000) * 1_000_000 + next(_COUNTER) % 1_000_000


def parse_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if normalized <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return normalized


def parse_bill_id(value: Any, field_name: str = "bill_id") -> int:
    return parse_positive_int(value, field_name)


BILL_ID_VALUE_KEYS = frozenset(
    {
        "bill_id",
        "source_bill_id",
        "refund_bill_id",
        "reimbursement_bill_id",
        "interest_bill_id",
    }
)
BILL_ID_LIST_KEYS = frozenset(
    {
        "new_ids",
        "del_ids",
        "update_ids",
        "source_bill_ids",
        "reimbursement_bill_ids",
    }
)


def bill_id_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _stringify_bill_ids(value: Any, value_keys: frozenset[str]) -> Any:
    if isinstance(value, list):
        return [_stringify_bill_ids(item, value_keys) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key in value_keys:
            normalized[key] = bill_id_string(item)
        elif key in BILL_ID_LIST_KEYS and isinstance(item, list):
            normalized[key] = [bill_id_string(bill_id) for bill_id in item]
        else:
            normalized[key] = _stringify_bill_ids(item, value_keys)
    return normalized


def stringify_known_bill_ids(value: Any) -> Any:
    """Recursively stringify known bill-id fields so JSON consumers (e.g.
    JavaScript) never lose precision on 64-bit integers."""
    return _stringify_bill_ids(value, BILL_ID_VALUE_KEYS)


def stringify_bill_payload_ids(value: Any) -> Any:
    """Like stringify_known_bill_ids, but also stringifies the plain `id` key
    used by cache-backed normalized bill shapes (GET /bills* responses)."""
    return _stringify_bill_ids(value, BILL_ID_VALUE_KEYS | {"id"})
