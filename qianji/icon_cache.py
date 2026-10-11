"""Disk cache and on-demand fetch for Qianji category icons.

The console renders the real Qianji category icons, which live on the Qianji
CDN as flat PNG URLs (``http://res3.qianjiapp.com/<name>.png``). The page CSP
only allows ``img-src 'self'``, so the browser reaches them through the
same-origin ``/category-icons/<name>`` route instead.

Lookup order:

1. icons baked into the image at build time by ``scripts/prefetch_icons.py``
   (``<package>/server/static/caticons``, so they survive as an image layer);
2. a runtime cache directory that survives container restarts (next to the
   SQLite database, i.e. inside the ``/app/data`` volume in Docker);
3. the upstream CDN, whose response is written back to (2) when writable.

The baked and runtime directories are separate on purpose: a named volume
mounted over the data directory would mask anything baked alongside it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# Both hosts serve the same bytes (verified), so the first is primary and the
# rest are fallbacks rather than a per-icon host mapping.
ICON_HOSTS: tuple[str, ...] = ("res3.qianjiapp.com", "res.qianjiapp.com")

BAKED_ICON_DIR = Path(__file__).resolve().parent / "server" / "static" / "caticons"

HTTP_TIMEOUT = 3.0
MAX_ICON_BYTES = 64 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Strict allowlist: no separators, no traversal, no unexpected extension.
_ICON_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.png$")

_INFLIGHT: dict[str, asyncio.Task[bytes | None]] = {}


def is_valid_icon_name(name: str) -> bool:
    """Whether ``name`` is a plain icon basename safe to serve from disk."""
    return bool(name) and bool(_ICON_NAME_RE.match(name))


def basename_from_url(url: str) -> str | None:
    """Extract a validated icon basename from a CDN URL, or ``None``."""
    if not url:
        return None
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    return name if is_valid_icon_name(name) else None


def icon_cache_dir() -> Path:
    """Runtime icon cache directory (env override, else beside the database)."""
    override = os.environ.get("QIANJI_ICON_CACHE_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("QIANJI_DB_PATH", ".qianji.db")).parent / "caticons"


def icon_names_from_payload(payload: Any) -> list[str]:
    """Collect unique icon basenames from a ``category/iconlist`` response.

    Group header entries carry no ``icon`` and are skipped; duplicates are
    dropped while preserving the server's order.
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    entries = data.get("list") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = basename_from_url(str(entry.get("icon") or ""))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def read_icon(name: str) -> bytes | None:
    """Return icon bytes from the baked or runtime directory, if present."""
    if not is_valid_icon_name(name):
        return None
    for directory in (BAKED_ICON_DIR, icon_cache_dir()):
        try:
            path = directory / name
            if path.is_file():
                return path.read_bytes()
        except OSError:  # unreadable cache entry: fall through to the next source
            logger.debug("Icon cache entry unreadable: %s", directory / name)
    return None


def _is_png(content: bytes) -> bool:
    return content.startswith(_PNG_MAGIC) and len(content) <= MAX_ICON_BYTES


def store_icon(name: str, content: bytes) -> bool:
    """Best-effort write into the runtime cache; never raises."""
    try:
        directory = icon_cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        tmp = directory / f"{name}.{os.getpid()}.tmp"
        tmp.write_bytes(content)
        os.replace(tmp, path)
        return True
    except OSError:
        logger.debug("Icon cache not writable, streaming without caching: %s", name)
        return False


async def fetch_icon(name: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes | None:
    """Fetch an icon from the CDN, trying each host until one yields a PNG."""
    if not is_valid_icon_name(name):
        return None
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, transport=transport) as http:
        for host in ICON_HOSTS:
            try:
                response = await http.get(f"http://{host}/{name}")
            except httpx.HTTPError:
                continue
            if response.status_code != 200 or not _is_png(response.content):
                continue
            return response.content
    return None


async def ensure_icon(name: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes | None:
    """Return icon bytes from any source, fetching upstream on a cache miss.

    Concurrent misses for the same name share one upstream request.
    """
    cached = read_icon(name)
    if cached is not None:
        return cached
    if not is_valid_icon_name(name):
        return None

    task = _INFLIGHT.get(name)
    if task is None:
        task = asyncio.create_task(_fetch_and_store(name, transport))
        _INFLIGHT[name] = task

        def _clear(_task: asyncio.Task[bytes | None], _name: str = name) -> None:
            _INFLIGHT.pop(_name, None)

        task.add_done_callback(_clear)
    return await task


async def _fetch_and_store(name: str, transport: httpx.AsyncBaseTransport | None) -> bytes | None:
    content = await fetch_icon(name, transport=transport)
    if content is not None:
        store_icon(name, content)
    return content
