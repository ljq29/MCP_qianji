from __future__ import annotations

from typing import Any

from qianji.api._base import BaseAPI, compact_json
from qianji.image_upload import validate_bill_image_keys


class ReimbursementAPI(BaseAPI):
    async def create_reimbursement(
        self,
        *,
        source_bill_ids: list[int],
        amount: float,
        occurred_at: int,
        asset_id: int | None = None,
        remark: str = "",
        currency: dict[str, Any] | None = None,
        images: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        return await self._submit_reimbursement(
            reimbursement_bill_id=None,
            source_bill_ids=source_bill_ids,
            amount=amount,
            occurred_at=occurred_at,
            asset_id=asset_id,
            remark=remark,
            currency=currency,
            images=images,
            tags=tags,
        )

    async def update_reimbursement(
        self,
        *,
        reimbursement_bill_id: int,
        source_bill_ids: list[int],
        amount: float,
        occurred_at: int,
        asset_id: int | None = None,
        remark: str = "",
        currency: dict[str, Any] | None = None,
        images: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        return await self._submit_reimbursement(
            reimbursement_bill_id=reimbursement_bill_id,
            source_bill_ids=source_bill_ids,
            amount=amount,
            occurred_at=occurred_at,
            asset_id=asset_id,
            remark=remark,
            currency=currency,
            images=images,
            tags=tags,
        )

    async def _submit_reimbursement(
        self,
        *,
        reimbursement_bill_id: int | None,
        source_bill_ids: list[int],
        amount: float,
        occurred_at: int,
        asset_id: int | None,
        remark: str,
        currency: dict[str, Any] | None,
        images: list[str] | None,
        tags: list[str] | None,
    ) -> dict:
        normalized_images = validate_bill_image_keys(images)
        payload: dict[str, Any] = {"billids": source_bill_ids, "money": amount}
        if asset_id is not None:
            payload["assetid"] = asset_id
        if currency is not None:
            payload["currency"] = currency
        params: dict[str, Any] = {"v": compact_json(payload), "bxtime": occurred_at}
        if reimbursement_bill_id is not None:
            params["did"] = reimbursement_bill_id
        if remark:
            params["remark"] = remark
        if normalized_images is not None:
            params["images"] = compact_json(normalized_images)
        if tags:
            params["tags"] = compact_json(tags)
        return await self._request("baoxiao", "baoxiao", params)

    async def cancel_reimbursement(self, reimbursement_bill_ids: list[int]) -> dict:
        return await self._request("baoxiao", "cancelbaoxiao", {"v": compact_json(reimbursement_bill_ids)})
