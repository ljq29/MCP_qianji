from __future__ import annotations

from typing import Any

from qianji.api._base import BaseAPI, compact_json
from qianji.image_upload import validate_bill_image_keys


class RefundAPI(BaseAPI):
    async def create_refund(
        self,
        *,
        source_bill_id: int,
        amount: float,
        occurred_at: int,
        asset_id: int | None = None,
        remark: str = "",
        currency: dict[str, Any] | None = None,
        images: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        return await self._submit_refund(
            source_bill_id=source_bill_id,
            refund_bill_id=None,
            amount=amount,
            occurred_at=occurred_at,
            asset_id=asset_id,
            remark=remark,
            currency=currency,
            images=images,
            tags=tags,
        )

    async def update_refund(
        self,
        *,
        source_bill_id: int,
        refund_bill_id: int,
        amount: float,
        occurred_at: int,
        asset_id: int | None = None,
        remark: str = "",
        currency: dict[str, Any] | None = None,
        images: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        return await self._submit_refund(
            source_bill_id=source_bill_id,
            refund_bill_id=refund_bill_id,
            amount=amount,
            occurred_at=occurred_at,
            asset_id=asset_id,
            remark=remark,
            currency=currency,
            images=images,
            tags=tags,
        )

    async def _submit_refund(
        self,
        *,
        source_bill_id: int,
        refund_bill_id: int | None,
        amount: float,
        occurred_at: int,
        asset_id: int | None,
        remark: str,
        currency: dict[str, Any] | None,
        images: list[str] | None,
        tags: list[str] | None,
    ) -> dict:
        normalized_images = validate_bill_image_keys(images)
        payload: dict[str, Any] = {"money": amount, "time": occurred_at}
        if refund_bill_id is not None:
            payload["billid"] = refund_bill_id
        if asset_id is not None:
            payload["assetid"] = asset_id
        if remark:
            payload["remark"] = remark
        if currency is not None:
            payload["currency"] = currency
        if normalized_images is not None:
            payload["images"] = normalized_images
        if tags:
            payload["tags"] = tags
        return await self._request("bill", "refund2", {"did": source_bill_id, "v": compact_json(payload)})
