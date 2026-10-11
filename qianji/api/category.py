from __future__ import annotations

from qianji.api._base import BaseAPI


class CategoryAPI(BaseAPI):
    async def category_list(self, book_id: int = -1, cat_type: int | None = None, show_hidden: bool = False) -> dict:
        params: dict[str, str] = {"bookid": str(book_id)}
        if cat_type is not None:
            params["t"] = str(cat_type)
        if show_hidden:
            params["showhide"] = "1"
        return await self._request("category", "listv2", params)

    async def category_submit(
        self,
        name: str,
        icon: str,
        cat_type: int = 0,
        book_id: int = -1,
        parent_id: int = -1,
        cateid: int = -1,
    ) -> dict:
        # Mirrors the app: name goes in `v`, category id in `did`; parentid/did/icon
        # are only sent when meaningful (did>0, parentid>0, icon non-empty).
        params: dict[str, str] = {"bookid": str(book_id), "v": name, "t": str(cat_type)}
        if cateid > 0:
            params["did"] = str(cateid)
        if parent_id > 0:
            params["parentid"] = str(parent_id)
        if icon:
            params["icon"] = icon
        return await self._request("category", "submitv2", params)

    async def category_delete(
        self, cateid: int, book_id: int = -1, force: bool = False, new_cateid: int | None = None
    ) -> dict:
        # The app sends did + deletebills ("1"/"0") + optional newcateid; book_id is
        # kept in the signature for backward compatibility but is not sent upstream.
        params: dict[str, str] = {"did": str(cateid), "deletebills": "1" if force else "0"}
        if new_cateid is not None:
            params["newcateid"] = str(new_cateid)
        return await self._request("category", "deletev2", params)
