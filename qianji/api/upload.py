from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

import httpx

from qianji.api._base import BaseAPI
from qianji.constants import DEFAULT_TIMEOUT
from qianji.errors import QianjiError, UpstreamError, UpstreamTimeoutError
from qianji.image_upload import process_bill_image

QINIU_UPLOAD_URL_ENV = "QIANJI_QINIU_UPLOAD_URL"
DEFAULT_QINIU_UPLOAD_URL = "https://upload.qiniup.com"


def _upload_key(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if len(suffix) > 16 or any(ch in suffix for ch in "/\\"):
        suffix = ""
    return f"_{uuid.uuid4().hex}{suffix}"


class UploadAPI(BaseAPI):
    async def common_upload_token(self, dt: int = 1) -> dict:
        return await self._request("common", "uploadtoken", {"dt": int(dt)})

    async def upload_bill_image(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
    ) -> dict:
        processed = await asyncio.to_thread(
            process_bill_image, filename=filename, content=content, mime_type=mime_type
        )
        token_result = await self.common_upload_token(dt=1)
        data = token_result.get("data", {}) if isinstance(token_result, dict) else {}
        token = data.get("v") or data.get("token")
        if not token:
            raise QianjiError(0, "Qianji upload token response did not include token", data)

        key = _upload_key(processed.filename)
        http = getattr(self, "_http", None)
        if http is None:
            options: dict[str, Any] = {"timeout": getattr(self, "_timeout", DEFAULT_TIMEOUT)}
            limits = getattr(self, "_limits", None)
            if limits is not None:
                options["limits"] = limits
            http = httpx.AsyncClient(**options)
            self._http = http
        upload_url = os.environ.get(QINIU_UPLOAD_URL_ENV, DEFAULT_QINIU_UPLOAD_URL)
        try:
            resp = await http.post(
                upload_url,
                data={"token": token, "key": key},
                files={"file": (processed.filename, processed.content, processed.content_type)},
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(0, f"Qiniu upload timed out: {exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError(0, f"Qiniu upload failed: {exc}") from exc

        return {
            "ok": True,
            "image_key": key,
            "remote_key": body.get("key", key),
            "hash": body.get("hash"),
            "token_expires_at": data.get("expireAt"),
            "original": processed.original,
            "uploaded": processed.uploaded,
        }
