from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx


def compact_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class BaseAPI:
    _http: httpx.AsyncClient | None
    _session_mgr: Any

    if TYPE_CHECKING:
        @property
        def current_account(self) -> str | None: ...

    async def _request(self, ctrl: str, act: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def _user_id(self) -> str:
        raise NotImplementedError

    async def bill_update(self, bill: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def call(self, ctrl: str, act: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request(ctrl, act, params)
