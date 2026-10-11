from __future__ import annotations

import json
import time
from typing import Any

from qianji.api._base import BaseAPI
from qianji.bill_id import new_bill_id
from qianji.constants import BillType
from qianji.image_upload import validate_bill_image_keys


def _new_bill_id() -> int:
    return new_bill_id()


def _bill(
    *,
    user_id: str,
    book_id: int,
    bill_type: int,
    amount: float,
    occurred_at: int,
    current_asset_id: int,
    about_asset_id: int,
    remark: str,
    images: list[str] | None,
) -> dict[str, Any]:
    now = int(time.time())
    normalized_images = validate_bill_image_keys(images)
    bill = {
        "id": _new_bill_id(),
        "userid": user_id,
        "bookid": book_id,
        "type": bill_type,
        "money": amount,
        "cateid": 0,
        "time": occurred_at,
        "createtime": now,
        "updatetime": now,
        "remark": remark,
        "status": 2,
        "assetid": current_asset_id,
        "fromid": -1,
        "targetid": -1,
        "extra": {"baoxiaoed": 0, "baoxiaotime": 0, "baoxiaov": -1.0, "transfee": 0.0, "tags": None},
        "platform": 0,
        "packid": -1,
    }
    if normalized_images is not None:
        bill["images"] = normalized_images
    if bill_type == BillType.LOAN_COLLECTION:
        bill["targetid"] = about_asset_id
    elif bill_type == BillType.DEBT_REPAYMENT:
        bill["fromid"] = about_asset_id
    elif bill_type in (BillType.DEBT_PRINCIPAL, BillType.LOAN_PRINCIPAL):
        bill["targetid" if bill_type == BillType.DEBT_PRINCIPAL else "fromid"] = about_asset_id
    elif bill_type in (BillType.DEBT_INTEREST, BillType.LOAN_INTEREST):
        bill["fromid"] = current_asset_id
        bill["assetid"] = about_asset_id
    return bill


class DebtLoanAPI(BaseAPI):
    async def add_loan_principal(
        self,
        *,
        loan_asset_id: int,
        amount: float,
        occurred_at: int,
        from_asset_id: int = -1,
        remark: str = "",
        book_id: int = -1,
        images: list[str] | None = None,
    ) -> dict:
        return await self._sync_debtloan_record(
            bill_type=BillType.LOAN_PRINCIPAL,
            current_asset_id=loan_asset_id,
            about_asset_id=from_asset_id,
            amount=amount,
            occurred_at=occurred_at,
            remark=remark,
            book_id=book_id,
            images=images,
        )

    async def add_debt_principal(
        self,
        *,
        debt_asset_id: int,
        amount: float,
        occurred_at: int,
        to_asset_id: int = -1,
        remark: str = "",
        book_id: int = -1,
        images: list[str] | None = None,
    ) -> dict:
        return await self._sync_debtloan_record(
            bill_type=BillType.DEBT_PRINCIPAL,
            current_asset_id=debt_asset_id,
            about_asset_id=to_asset_id,
            amount=amount,
            occurred_at=occurred_at,
            remark=remark,
            book_id=book_id,
            images=images,
        )

    async def record_loan_collection(
        self,
        *,
        loan_asset_id: int,
        amount: float,
        occurred_at: int,
        to_asset_id: int = -1,
        interest_amount: float = 0.0,
        remark: str = "",
        book_id: int = -1,
        images: list[str] | None = None,
    ) -> dict:
        return await self._sync_debtloan_record(
            bill_type=BillType.LOAN_COLLECTION,
            current_asset_id=loan_asset_id,
            about_asset_id=to_asset_id,
            amount=amount,
            occurred_at=occurred_at,
            remark=remark,
            book_id=book_id,
            images=images,
            interest_type=BillType.LOAN_INTEREST,
            interest_amount=interest_amount,
        )

    async def record_debt_repayment(
        self,
        *,
        debt_asset_id: int,
        amount: float,
        occurred_at: int,
        from_asset_id: int = -1,
        interest_amount: float = 0.0,
        remark: str = "",
        book_id: int = -1,
        images: list[str] | None = None,
    ) -> dict:
        return await self._sync_debtloan_record(
            bill_type=BillType.DEBT_REPAYMENT,
            current_asset_id=debt_asset_id,
            about_asset_id=from_asset_id,
            amount=amount,
            occurred_at=occurred_at,
            remark=remark,
            book_id=book_id,
            images=images,
            interest_type=BillType.DEBT_INTEREST,
            interest_amount=interest_amount,
        )

    async def update_loan_collection(self, bill_id: int, **updates: Any) -> dict:
        return await self._update_debtloan_bill(
            bill_id, expected_type=BillType.LOAN_COLLECTION, about_key="targetid", **updates
        )

    async def update_debt_repayment(self, bill_id: int, **updates: Any) -> dict:
        return await self._update_debtloan_bill(
            bill_id, expected_type=BillType.DEBT_REPAYMENT, about_key="fromid", **updates
        )

    async def update_loan_principal(self, bill_id: int, **updates: Any) -> dict:
        return await self._update_debtloan_bill(
            bill_id, expected_type=BillType.LOAN_PRINCIPAL, about_key="fromid", **updates
        )

    async def update_debt_principal(self, bill_id: int, **updates: Any) -> dict:
        return await self._update_debtloan_bill(
            bill_id, expected_type=BillType.DEBT_PRINCIPAL, about_key="targetid", **updates
        )

    async def _sync_debtloan_record(
        self,
        *,
        bill_type: int,
        current_asset_id: int,
        about_asset_id: int,
        amount: float,
        occurred_at: int,
        remark: str,
        book_id: int,
        images: list[str] | None,
        interest_type: int | None = None,
        interest_amount: float = 0.0,
    ) -> dict:
        bills = [
            _bill(
                user_id=self._user_id,
                book_id=book_id,
                bill_type=bill_type,
                amount=amount,
                occurred_at=occurred_at,
                current_asset_id=current_asset_id,
                about_asset_id=about_asset_id,
                remark=remark,
                images=images,
            )
        ]
        if interest_type is not None and interest_amount > 0:
            bills.append(
                _bill(
                    user_id=self._user_id,
                    book_id=book_id,
                    bill_type=interest_type,
                    amount=interest_amount,
                    occurred_at=occurred_at,
                    current_asset_id=current_asset_id,
                    about_asset_id=about_asset_id,
                    remark=remark,
                    images=None,
                )
            )
        payload = {"bills": {"changelist": json.dumps(bills, ensure_ascii=False)}}
        result = await self._request("bill", "syncall", {"v": json.dumps(payload, ensure_ascii=False)})
        for bill in bills:
            await self._cache_bill_if_available(bill)
        response = {"ok": True, "bill_id": bills[0]["id"], "result": result}
        if len(bills) > 1:
            response["interest_bill_id"] = bills[1]["id"]
        return response

    async def _update_debtloan_bill(self, bill_id: int, *, expected_type: int, about_key: str, **updates: Any) -> dict:
        has_images_update = "images" in updates and updates["images"] is not None
        normalized_images = validate_bill_image_keys(updates["images"]) if has_images_update else None
        account_id = self.current_account
        raw = None
        mgr = getattr(self, "_session_mgr", None)
        if mgr is not None and account_id:
            raw = await mgr.cache_get_bill(account_id, bill_id)
        if raw is None and mgr is not None and account_id:
            # Never fabricate a partial bill for the upstream update: sync the
            # cache first and only proceed with the full stored record.
            from qianji.bill_ops import ensure_bill_cache

            await ensure_bill_cache(self)
            raw = await mgr.cache_get_bill(account_id, bill_id)
        if raw is None:
            raise ValueError(
                f"bill_id={bill_id} not found in the local bill cache (even after a sync); "
                "refusing to send an incomplete bill update upstream"
            )
        if int(raw.get("type", expected_type)) != expected_type:
            raise ValueError(f"bill_id={bill_id} is not the expected debt/loan bill type")
        bill = dict(raw)
        bill["status"] = 2
        bill["updatetime"] = int(time.time())
        if updates.get("amount") is not None:
            bill["money"] = updates["amount"]
        if updates.get("occurred_at") is not None:
            bill["time"] = updates["occurred_at"]
        if updates.get("remark") is not None:
            bill["remark"] = updates["remark"]
        if has_images_update:
            bill["images"] = normalized_images or []
        about_asset_id = updates.get("to_asset_id", updates.get("from_asset_id"))
        if about_asset_id is not None:
            bill[about_key] = about_asset_id
        result = await self.bill_update(bill)
        await self._cache_bill_if_available(bill)
        return {"ok": True, "bill_id": bill_id, "result": result}

    async def _cache_bill_if_available(self, bill: dict[str, Any]) -> None:
        mgr = getattr(self, "_session_mgr", None)
        account_id = self.current_account
        if mgr is not None and account_id:
            await mgr.cache_upsert_bills(account_id, [{**bill, "status": 1}])
