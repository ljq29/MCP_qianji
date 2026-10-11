from __future__ import annotations

import json

from qianji.api._base import BaseAPI, compact_json


class TagAPI(BaseAPI):
    async def tag_list(self, status: int = -1, bookid: int | None = None, book_id: int | None = None) -> dict:
        params: dict[str, object] = {"status": status}
        selected_book_id = book_id if book_id is not None else bookid
        if selected_book_id is not None:
            params["bookid"] = selected_book_id
        return await self._request("tag", "list", params)

    async def tag_submit(
        self,
        name: str,
        groupid: str = "",
        tagid: str = "",
        color: str | None = None,
        group_id: str | None = None,
        tag_id: str | None = None,
    ) -> dict:
        selected_group_id = group_id if group_id is not None else groupid
        selected_tag_id = tag_id if tag_id is not None else tagid
        payload: dict[str, object] = {"groupid": selected_group_id, "name": name}
        if color is not None:
            payload["color"] = color
        params: dict[str, object] = {"v": json.dumps(payload, ensure_ascii=False)}
        if selected_tag_id:
            params["did"] = selected_tag_id
        return await self._request("tag", "submittag", params)

    async def tag_submittag(
        self,
        v: str | dict,
        did: str = "",
        tagid: str = "",
        groupid: str = "",
        name: str | None = None,
        color: str | None = None,
    ) -> dict:
        if isinstance(v, dict):
            payload = json.dumps(v, ensure_ascii=False)
        else:
            payload = v
        params: dict[str, object] = {"v": payload}
        selected_tag_id = did or tagid
        if selected_tag_id:
            params["did"] = selected_tag_id
        return await self._request("tag", "submittag", params)

    async def tag_delete(self, tag_id: str) -> dict:
        return await self._request("tag", "deletetag", {"did": tag_id})

    async def tag_archive(self, tag_id: str) -> dict:
        return await self._request("tag", "archivetag", {"did": tag_id})

    async def tag_submit_group(self, name: str, group_id: str = "") -> dict:
        params: dict[str, object] = {"v": json.dumps({"name": name}, ensure_ascii=False)}
        if group_id:
            params["did"] = group_id
        return await self._request("tag", "submitgroup", params)

    async def tag_delete_group(self, group_id: str) -> dict:
        return await self._request("tag", "deletegroup", {"did": group_id})

    async def tag_reorder_tag(self, tag_ids: list[str]) -> dict:
        return await self._request("tag", "reordertag", {"v": compact_json(tag_ids)})

    async def tag_reorder_group(self, group_ids: list[str]) -> dict:
        return await self._request("tag", "reordergroup", {"v": compact_json(group_ids)})
