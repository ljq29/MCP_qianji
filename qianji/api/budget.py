from __future__ import annotations

import json

from qianji.api._base import BaseAPI


class BudgetAPI(BaseAPI):
    async def budget_submit(
        self, budgets: list[dict], book_id: int = -1, start_date: str = "", end_date: str = ""
    ) -> dict:
        params: dict[str, str] = {
            "bookid": str(book_id),
            "v": json.dumps(budgets, ensure_ascii=False),
        }
        if start_date:
            params["startdate"] = start_date
        if end_date:
            params["enddate"] = end_date
        return await self._request("budget", "submitv2", params)
