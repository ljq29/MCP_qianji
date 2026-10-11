from __future__ import annotations

from qianji.api._base import BaseAPI


class AccountAPI(BaseAPI):
    async def account_login_records(self) -> dict:
        return await self._request("account", "loginrecords2")
