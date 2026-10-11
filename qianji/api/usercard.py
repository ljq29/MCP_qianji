from __future__ import annotations

from qianji.api._base import BaseAPI


class UserCardAPI(BaseAPI):
    async def usercard_list(self) -> dict:
        return await self._request("usercard", "listv2")
