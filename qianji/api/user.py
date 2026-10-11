from __future__ import annotations

import json

from qianji.api._base import BaseAPI
from qianji.tags import get_tags_enabled as _get_tags_enabled
from qianji.tags import set_tags_enabled as _set_tags_enabled


class UserAPI(BaseAPI):
    async def user_update_config(self, config: dict) -> dict:
        return await self._request("user", "updateconfig", {"v": json.dumps(config, ensure_ascii=False)})

    async def get_tags_enabled(self) -> bool:
        return await _get_tags_enabled(self)

    async def set_tags_enabled(self, enabled: bool) -> dict:
        return await _set_tags_enabled(self, enabled)
