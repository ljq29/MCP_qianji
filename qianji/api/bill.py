from __future__ import annotations

import json
import time

from qianji.api._base import BaseAPI
from qianji.bill_id import new_bill_id
from qianji.image_upload import validate_bill_image_keys


class BillAPI(BaseAPI):
    async def bill_create(
        self,
        money: float,
        bill_type: int = 0,
        cateid: int = 0,
        assetid: int = -1,
        remark: str = "",
        book_id: int = -1,
        target_asset_id: int = -1,
        tags: list[str] | None = None,
        images: list[str] | None = None,
        bill_time: int | None = None,
    ) -> dict:
        now = int(time.time())
        bill_time = now if bill_time is None else bill_time
        bill_id = new_bill_id()
        normalized_images = validate_bill_image_keys(images)
        bill = {
            "id": bill_id,
            "userid": self._user_id,
            "bookid": book_id,
            "type": bill_type,
            "money": money,
            "cateid": cateid,
            "time": bill_time,
            "createtime": now,
            "updatetime": now,
            "remark": remark,
            "status": 2,
            "assetid": assetid,
            "fromid": assetid if bill_type == 2 else -1,
            "targetid": target_asset_id if bill_type == 2 else -1,
            "extra": {"baoxiaoed": 0, "baoxiaotime": 0, "baoxiaov": -1.0, "transfee": 0.0, "tags": tags},
            "platform": 0,
            "packid": -1,
        }
        if normalized_images is not None:
            bill["images"] = normalized_images
        v = {"bills": {"changelist": json.dumps([bill], ensure_ascii=False)}}
        result = await self._request("bill", "syncall", {"v": json.dumps(v, ensure_ascii=False)})
        await self._cache_bill(bill)
        return result

    async def bill_update(self, bill: dict) -> dict:
        if "images" in bill:
            bill = dict(bill)
            bill["images"] = validate_bill_image_keys(bill["images"]) or []
        bill["updatetime"] = int(time.time())
        v = {"bills": {"changelist": json.dumps([bill], ensure_ascii=False)}}
        return await self._request("bill", "syncall", {"v": json.dumps(v, ensure_ascii=False)})

    async def bill_delete(self, bill_ids: list[int]) -> dict:
        v = {"bills": {"dellist": bill_ids}}
        return await self._request("bill", "syncall", {"v": json.dumps(v, ensure_ascii=False)})

    async def _cache_bill(self, bill: dict) -> None:
        """Keep the local bill cache consistent after a create, so queries see it immediately."""
        mgr = getattr(self, "_session_mgr", None)
        account_id = self.current_account
        if mgr is None or not account_id:
            return
        # Local cache treats the bill as synced (status=1); the upstream payload keeps status=2.
        await mgr.cache_upsert_bills(account_id, [{**bill, "status": 1}])
