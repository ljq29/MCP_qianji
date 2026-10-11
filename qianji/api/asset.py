from __future__ import annotations

from typing import Any

from qianji.api._base import BaseAPI, compact_json
from qianji.constants import LoanType
from qianji.image_upload import validate_bill_image_keys


def _loan_payload(
    *,
    name: str,
    amount: float,
    stype: int,
    startdate: str,
    enddate: str | None,
    incount: bool,
    asset_id: int | None = None,
) -> dict[str, Any]:
    signed_amount = amount if stype == LoanType.LOAN else -amount
    payload: dict[str, Any] = {
        "name": name,
        "type": 5,
        "stype": stype,
        "money": signed_amount,
        "incount": 1 if incount else 0,
        "loan": {"money": signed_amount, "startdate": startdate, "enddate": enddate, "totalpay": 0.0},
    }
    if asset_id is not None:
        payload["id"] = asset_id
    return payload


def _submit_extra(
    *,
    book_id: int,
    remark: str,
    linked_asset_id: int | None,
    images: list[str] | None,
) -> dict[str, Any]:
    normalized_images = validate_bill_image_keys(images)
    extra: dict[str, Any] = {"bookid": book_id}
    if remark:
        extra["remark"] = remark
    if linked_asset_id is not None:
        extra["accountId"] = linked_asset_id
    if normalized_images is not None:
        extra["images"] = normalized_images
    return extra


class AssetAPI(BaseAPI):
    async def asset_list(self, status: int = 0, uid: str | None = None) -> dict:
        return await self._request("asset", "list", {"status": status})

    async def asset_info(self, asset_id: int | None = None, did: int | None = None, uid: str | None = None) -> dict:
        selected_asset_id = asset_id if asset_id is not None else did
        if selected_asset_id is None:
            raise TypeError("asset_info missing required argument 'asset_id'")
        return await self._request("asset", "info", {"did": selected_asset_id})

    async def asset_list_loan(
        self,
        loan_type: int | None = None,
        status: int = 0,
        t: int | None = None,
        loantype: int | None = None,
        uid: str | None = None,
    ) -> dict:
        selected_type = loan_type if loan_type is not None else t if t is not None else loantype
        if selected_type is None:
            raise TypeError("asset_list_loan missing required argument 'loan_type'")
        return await self._request("asset", "listloan", {"t": selected_type, "status": status})

    async def create_loan_asset(
        self,
        *,
        name: str,
        amount: float,
        startdate: str,
        enddate: str | None = None,
        linked_asset_id: int | None = None,
        remark: str = "",
        book_id: int = -1,
        images: list[str] | None = None,
        incount: bool = True,
    ) -> dict:
        return await self._submit_debtloan_asset(
            stype=LoanType.LOAN,
            name=name,
            amount=amount,
            startdate=startdate,
            enddate=enddate,
            linked_asset_id=linked_asset_id,
            remark=remark,
            book_id=book_id,
            images=images,
            incount=incount,
        )

    async def create_debt_asset(
        self,
        *,
        name: str,
        amount: float,
        startdate: str,
        enddate: str | None = None,
        linked_asset_id: int | None = None,
        remark: str = "",
        book_id: int = -1,
        images: list[str] | None = None,
        incount: bool = True,
    ) -> dict:
        return await self._submit_debtloan_asset(
            stype=LoanType.DEBT,
            name=name,
            amount=amount,
            startdate=startdate,
            enddate=enddate,
            linked_asset_id=linked_asset_id,
            remark=remark,
            book_id=book_id,
            images=images,
            incount=incount,
        )

    async def update_loan_asset(self, asset_id: int, **updates: Any) -> dict:
        return await self._update_debtloan_asset(asset_id, expected_stype=LoanType.LOAN, **updates)

    async def update_debt_asset(self, asset_id: int, **updates: Any) -> dict:
        return await self._update_debtloan_asset(asset_id, expected_stype=LoanType.DEBT, **updates)

    async def update_asset(
        self, asset_id: int, *, name: str | None = None, remark: str | None = None
    ) -> dict:
        """Rename/annotate any asset. Debt/loan (type 5) assets reuse the debt/loan submit path."""
        info = await self.asset_info(asset_id)
        data = info.get("data", {}) if isinstance(info, dict) else {}
        asset = data.get("asset", data)
        if not isinstance(asset, dict):
            raise ValueError(f"Asset not found: {asset_id}")
        if int(asset.get("type", 0)) == 5:
            stype = int(asset.get("stype", LoanType.DEBT))
            method = self.update_loan_asset if stype == LoanType.LOAN else self.update_debt_asset
            updates: dict[str, Any] = {}
            if name is not None:
                updates["name"] = name
            if remark is not None:
                updates["remark"] = remark
            return await method(asset_id, asset=asset, **updates)

        updated_asset = dict(asset)
        updated_asset["id"] = asset_id
        if name is not None:
            updated_asset["name"] = name
        if remark is not None:
            updated_asset["remark"] = remark
        return await self._request("asset", "submit", {"v": compact_json(updated_asset)})

    async def update_asset_balance(
        self,
        asset_id: int,
        new_balance: float,
        *,
        generate_adjustment: bool = False,
        category_id: int | None = None,
        bill_flag: int = 0,
        remark: str = "",
    ) -> dict:
        info = await self.asset_info(asset_id)
        data = info.get("data", {}) if isinstance(info, dict) else {}
        asset = data.get("asset", data)
        if not isinstance(asset, dict):
            raise ValueError(f"Asset not found: {asset_id}")

        current_balance = float(asset.get("money", 0))
        changed = current_balance != float(new_balance)
        if generate_adjustment and changed and category_id is None:
            raise ValueError("category_id is required when generate_adjustment=True and the balance changes")

        updated_asset = dict(asset)
        updated_asset["money"] = new_balance
        params = {"v": compact_json(updated_asset)}
        if generate_adjustment and changed:
            params["diffparams"] = compact_json(
                {
                    "cateid": category_id,
                    "billflag": bill_flag,
                    "remark": remark,
                }
            )
        return await self._request("asset", "submit", params)

    async def _submit_debtloan_asset(
        self,
        *,
        stype: int,
        name: str,
        amount: float,
        startdate: str,
        enddate: str | None,
        linked_asset_id: int | None,
        remark: str,
        book_id: int,
        images: list[str] | None,
        incount: bool,
    ) -> dict:
        asset = _loan_payload(
            name=name,
            amount=amount,
            stype=stype,
            startdate=startdate,
            enddate=enddate,
            incount=incount,
        )
        extra = _submit_extra(book_id=book_id, remark=remark, linked_asset_id=linked_asset_id, images=images)
        return await self._request("asset", "submit", {"v": compact_json(asset), "type": compact_json(extra)})

    async def _update_debtloan_asset(
        self, asset_id: int, *, expected_stype: int, asset: dict[str, Any] | None = None, **updates: Any
    ) -> dict:
        has_images_update = "images" in updates and updates["images"] is not None
        normalized_images = validate_bill_image_keys(updates["images"]) if has_images_update else None
        if asset is None:
            info = await self.asset_info(asset_id)
            data = info.get("data", {}) if isinstance(info, dict) else {}
            asset = data.get("asset", data)
        if not isinstance(asset, dict):
            raise ValueError(f"Asset not found: {asset_id}")
        if int(asset.get("stype", expected_stype)) != expected_stype:
            raise ValueError(f"asset_id={asset_id} is not the expected debt/loan asset type")

        asset = dict(asset)
        asset["id"] = asset_id
        for key in ("name", "incount"):
            if key in updates and updates[key] is not None:
                asset[key] = 1 if key == "incount" and updates[key] else updates[key]
        loan = dict(asset.get("loan") or {})
        if updates.get("startdate") is not None:
            loan["startdate"] = updates["startdate"]
        if "enddate" in updates:
            loan["enddate"] = updates["enddate"]
        asset["loan"] = loan

        extra = _submit_extra(
            book_id=int(updates.get("book_id", -1)),
            remark=str(updates.get("remark") or ""),
            linked_asset_id=updates.get("linked_asset_id"),
            images=normalized_images if has_images_update else None,
        )
        return await self._request("asset", "submit", {"v": compact_json(asset), "type": compact_json(extra)})
