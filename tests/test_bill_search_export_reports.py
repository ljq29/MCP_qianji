from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from types import MethodType

import pytest

from qianji.client import QianjiClient
from qianji.session import SessionData


def _raw_bill(
    bill_id: int,
    *,
    bill_time: int,
    money: float,
    bill_type: int = 0,
    book_id: int = -1,
    asset_id: int = 10,
    category_id: int = 20,
    remark: str = "",
    tags: list[str] | None = None,
    images: list[str] | None = None,
    currency: str | None = None,
) -> dict:
    extra = {"tags": tags or []}
    if images is not None:
        extra["images"] = images
    if currency is not None:
        extra["curr"] = {"srcSymbol": currency}
    return {
        "id": bill_id,
        "userid": "user-a",
        "bookid": book_id,
        "type": bill_type,
        "money": money,
        "cateid": category_id,
        "time": bill_time,
        "createtime": bill_time,
        "updatetime": bill_time,
        "remark": remark,
        "status": 1,
        "assetid": asset_id,
        "fromid": asset_id if bill_type == 2 else -1,
        "targetid": 99 if bill_type == 2 else -1,
        "extra": extra,
    }


def _export_item(bill_id: int) -> dict:
    return {
        "id": bill_id,
        "occurred_at": 1_700_000_000 + bill_id,
        "bill_type": "expense",
        "amount": float(bill_id),
        "currency": "CNY",
        "book_id": -1,
        "asset_id": 10,
        "from_asset_id": -1,
        "to_asset_id": -1,
        "category_id": 20,
        "remark": f"bill-{bill_id}",
        "tag_ids": [],
    }


@pytest.fixture
def client(tmp_path):
    c = QianjiClient(db_path=tmp_path / "search.db")
    c._sessions = {
        "a": SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd-a",
            utoken="token-a",
            user_id="user-a",
            user_name="a",
            devid="dev-a",
            devname="Device a",
        )
    }
    c._current_account = "a"

    async def fake_currency_list(self):
        return {"data": {"list": [{"symbol": "CNY", "baseprice": "1.000000"}]}}

    c.currency_list = MethodType(fake_currency_list, c)  # type: ignore[method-assign]
    yield c
    asyncio.run(c.aclose())


async def _seed(client: QianjiClient, bills: list[dict]) -> None:
    await client._session_mgr.cache_upsert_bills("a", bills)
    await client._session_mgr.set_bill_sync_state("a", -1, max(b["updatetime"] for b in bills), time.time())


def test_search_bills_filters_sorts_and_paginates(client):
    from qianji.bill_ops import search_bills

    bills = [
        _raw_bill(1, bill_time=100, money=12, remark="coffee", tags=["food"], images=["img-a"]),
        _raw_bill(2, bill_time=200, money=80, remark="taxi airport", tags=["travel"], currency="USD"),
        _raw_bill(3, bill_time=300, money=5, bill_type=3, remark="ignored", tags=["travel"]),
        _raw_bill(4, bill_time=400, money=130, bill_type=1, remark="taxi reimbursement", tags=["travel"]),
    ]

    async def run():
        await _seed(client, bills)
        return await search_bills(
            client,
            start_at=150,
            end_at=450,
            bill_types=[0, 1],
            tag_ids=["travel"],
            keyword="taxi",
            min_amount=50,
            max_amount=150,
            counted=True,
            sort_by="amount",
            sort_order="desc",
            page=1,
            page_size=1,
        )

    result = asyncio.run(run())

    assert result["total"] == 2
    assert result["has_more"] is True
    assert [item["id"] for item in result["items"]] == [4]
    assert result["items"][0]["remark"] == "taxi reimbursement"


def test_search_bills_supports_bill_id_currency_and_image_filters(client):
    from qianji.bill_ops import search_bills

    bills = [
        _raw_bill(10, bill_time=100, money=12, remark="plain"),
        _raw_bill(11, bill_time=200, money=80, remark="photo", images=["img-a"], currency="USD"),
    ]

    async def run():
        await _seed(client, bills)
        return await search_bills(client, bill_id=11, currency="USD", has_images=True)

    result = asyncio.run(run())

    assert result["total"] == 1
    assert result["items"][0]["id"] == 11
    assert result["items"][0]["currency"] == "USD"


def test_export_bills_creates_csv_json_and_txt_files(client, tmp_path):
    from qianji.bill_files import export_bills_to_file

    async def run():
        await _seed(
            client,
            [
                _raw_bill(1, bill_time=100, money=12.5, remark="coffee"),
                _raw_bill(2, bill_time=200, money=80, remark="taxi"),
            ],
        )
        csv_file = await export_bills_to_file(client, file_format="csv", base_url="https://files.example")
        json_file = await export_bills_to_file(client, file_format="json", base_url="https://files.example")
        txt_file = await export_bills_to_file(client, file_format="txt", base_url="https://files.example")
        csv_record = await client._session_mgr.get_generated_file(csv_file["file_id"], now=1_700_000_000)
        json_record = await client._session_mgr.get_generated_file(json_file["file_id"], now=1_700_000_000)
        txt_record = await client._session_mgr.get_generated_file(txt_file["file_id"], now=1_700_000_000)
        return csv_file, json_file, txt_file, csv_record, json_record, txt_record

    csv_file, json_file, txt_file, csv_record, json_record, txt_record = asyncio.run(run())

    assert csv_file["download_url"].startswith("https://files.example/files/")
    rows = list(csv.DictReader(io.StringIO(csv_record["content"].decode("utf-8"))))
    assert [row["id"] for row in rows] == ["2", "1"]
    assert json.loads(json_record["content"].decode("utf-8"))["items"][0]["id"] == 2
    assert "taxi" in txt_record["content"].decode("utf-8")
    assert txt_file["content_type"].startswith("text/plain")
    assert csv_file["expires_at"] == pytest.approx(time.time() + 7 * 86400, abs=5)
    assert json_file["file_id"] != csv_file["file_id"]


def test_export_bills_collects_all_items_in_single_search(client, monkeypatch):
    from qianji import bill_files

    calls: list[dict] = []

    async def fake_search_all(client_arg, **kwargs):
        calls.append(dict(kwargs))
        return [_export_item(1), _export_item(2), _export_item(3)]

    monkeypatch.setattr(bill_files, "search_all_bill_items", fake_search_all)

    async def run():
        file_info = await bill_files.export_bills_to_file(client, file_format="json")
        record = await client._session_mgr.get_generated_file(file_info["file_id"], now=1_700_000_000)
        return file_info, record

    file_info, record = asyncio.run(run())
    payload = json.loads(record["content"].decode("utf-8"))

    assert len(calls) == 1
    assert "page" not in calls[0] and "page_size" not in calls[0]
    assert [item["id"] for item in payload["items"]] == [1, 2, 3]
    assert file_info["total"] == 3
    assert file_info["total_returned"] == 3
    assert file_info["truncated"] is False


def test_export_and_report_search_only_once_with_real_cache(client, monkeypatch):
    from qianji import bill_files
    from qianji.bill_ops import search_all_bill_items

    calls = 0

    async def counting_search_all(client_arg, **kwargs):
        nonlocal calls
        calls += 1
        return await search_all_bill_items(client_arg, **kwargs)

    monkeypatch.setattr(bill_files, "search_all_bill_items", counting_search_all)

    async def run():
        await _seed(
            client,
            [_raw_bill(bill_id, bill_time=100 + bill_id, money=10) for bill_id in range(1, 8)],
        )
        exported = await bill_files.export_bills_to_file(client, file_format="csv")
        report = await bill_files.generate_report_image_file(client)
        return exported, report

    exported, report = asyncio.run(run())

    assert calls == 2  # one search per file, no per-page re-searching
    assert exported["total"] == 7
    assert exported["total_returned"] == 7
    assert exported["truncated"] is False
    assert report["total"] == 7


def test_export_and_report_mark_results_truncated_at_hard_limit(client, monkeypatch):
    from qianji import bill_files

    async def fake_search_all(client_arg, **kwargs):
        return [_export_item(bill_id) for bill_id in range(1, 6)]

    monkeypatch.setattr(bill_files, "MAX_EXPORT_ITEMS", 3, raising=False)
    monkeypatch.setattr(bill_files, "search_all_bill_items", fake_search_all)

    async def run():
        exported = await bill_files.export_bills_to_file(client, file_format="json")
        report = await bill_files.generate_report_image_file(client)
        export_record = await client._session_mgr.get_generated_file(exported["file_id"], now=1_700_000_000)
        return exported, report, export_record

    exported, report, export_record = asyncio.run(run())
    payload = json.loads(export_record["content"].decode("utf-8"))

    assert [item["id"] for item in payload["items"]] == [1, 2, 3]
    assert exported["total"] == 5
    assert exported["total_returned"] == 3
    assert exported["max_items"] == 3
    assert exported["truncated"] is True
    assert report["total"] == 5
    assert report["total_returned"] == 3
    assert report["max_items"] == 3
    assert report["truncated"] is True


def test_export_csv_prefixes_formula_like_text(client):
    from qianji.bill_files import export_bills_to_file

    async def run():
        await _seed(
            client,
            [
                _raw_bill(1, bill_time=100, money=12.5, remark="=SUM(A1:A2)"),
                _raw_bill(2, bill_time=200, money=80, remark="+cmd"),
                _raw_bill(3, bill_time=300, money=5, remark="@mention"),
                _raw_bill(4, bill_time=400, money=3, remark="plain note"),
            ],
        )
        csv_file = await export_bills_to_file(client, file_format="csv")
        record = await client._session_mgr.get_generated_file(csv_file["file_id"], now=1_700_000_000)
        return record["content"].decode("utf-8")

    content = asyncio.run(run())

    assert "'=SUM(A1:A2)" in content
    assert "'+cmd" in content
    assert "'@mention" in content
    assert "plain note" in content
    assert "'plain note" not in content


def test_export_txt_uses_local_timezone(client):
    from qianji.bill_files import export_bills_to_file

    async def run():
        await _seed(client, [_raw_bill(1, bill_time=1_700_000_000, money=12.5, remark="coffee")])
        txt_file = await export_bills_to_file(client, file_format="txt")
        record = await client._session_mgr.get_generated_file(txt_file["file_id"], now=1_700_000_000)
        return record["content"].decode("utf-8")

    content = asyncio.run(run())

    assert "2023-11-15T06:13:20+08:00" in content


def test_report_labels_use_local_timezone():
    from qianji.bill_files import _day_label, _month_label

    # 2023-12-31T23:59:59Z is already 2024-01-01 in UTC+8.
    assert _month_label(1_704_067_199) == "2024-01"
    assert _day_label(1_704_067_199) == "2024-01-01"


def test_category_summary_marks_truncated_rows(client):
    from qianji.bill_files import generate_report_image_file

    async def run():
        await _seed(
            client,
            [_raw_bill(i, bill_time=100 + i, money=10, category_id=1000 + i) for i in range(1, 15)],
        )
        file_info = await generate_report_image_file(client, report_type="category_summary")
        record = await client._session_mgr.get_generated_file(file_info["file_id"], now=1_700_000_000)
        return record["content"].decode("utf-8")

    svg = asyncio.run(run())

    assert "truncated" in svg


def test_trend_report_remains_supported_with_daily_aggregation(client):
    from qianji.bill_files import generate_report_image_file

    async def run():
        await _seed(client, [_raw_bill(1, bill_time=1_700_000_000, money=12.5)])
        file_info = await generate_report_image_file(client, report_type="trend")
        record = await client._session_mgr.get_generated_file(file_info["file_id"], now=1_700_000_000)
        return file_info, record["content"].decode("utf-8")

    file_info, svg = asyncio.run(run())

    assert file_info["report_type"] == "trend"
    assert "2023-11-15" in svg


def test_cache_search_bills_pushes_type_filter_and_limit_to_sql(client):
    async def run():
        await _seed(
            client,
            [
                _raw_bill(1, bill_time=100, money=10, bill_type=0),
                _raw_bill(2, bill_time=200, money=20, bill_type=1),
                _raw_bill(3, bill_time=300, money=30, bill_type=0),
            ],
        )
        typed = await client._session_mgr.cache_search_bills("a", bill_types=[0])
        limited = await client._session_mgr.cache_search_bills("a", bill_types=[0], limit=1)
        return typed, limited

    typed, limited = asyncio.run(run())

    assert [row["id"] for row in typed] == [3, 1]
    assert [row["id"] for row in limited] == [3]


def test_generate_report_image_creates_downloadable_svg(client):
    from qianji.bill_files import generate_report_image_file

    async def run():
        await _seed(
            client,
            [
                _raw_bill(1, bill_time=1_704_067_200, money=12.5, remark="coffee", category_id=5),
                _raw_bill(2, bill_time=1_704_153_600, money=80, remark="taxi", category_id=6),
            ],
        )
        file_info = await generate_report_image_file(
            client,
            report_type="category_summary",
            base_url="https://files.example",
        )
        record = await client._session_mgr.get_generated_file(file_info["file_id"], now=1_700_000_000)
        return file_info, record

    file_info, record = asyncio.run(run())
    svg = record["content"].decode("utf-8")

    assert file_info["content_type"] == "image/svg+xml"
    assert file_info["download_url"].startswith("https://files.example/files/")
    assert svg.startswith("<svg")
    assert "category_summary" in svg
    assert "80" in svg
