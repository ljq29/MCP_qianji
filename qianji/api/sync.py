from __future__ import annotations

import json

from qianji.api._base import BaseAPI


class SyncAPI(BaseAPI):
    async def sync_pull(
        self,
        book_id: int = -1,
        last_time: int = 0,
        page_offset: int = 0,
        *,
        last_times: dict | None = None,
        page_sign: str = "",
    ) -> dict:
        times = last_times if last_times is not None else {str(book_id): last_time}
        return await self._request(
            "syncv2",
            "pull",
            {
                "bookid": str(book_id),
                "lasttimes": json.dumps(times),
                "pageoffset": str(page_offset),
                "pagesign": str(page_sign),
            },
        )
