"""Prefetch the Qianji category icons into the package's static directory.

Run at Docker build time so the image ships every icon the console can render,
which keeps the running container off the CDN for the built-in set. Also usable
as a manual refresh tool: re-running skips icons that are already present
unless ``--force`` is given.

    python scripts/prefetch_icons.py [--out DIR] [--force] [--concurrency N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qianji.client import QianjiClient  # noqa: E402
from qianji.icon_cache import fetch_icon, icon_names_from_payload  # noqa: E402

logger = logging.getLogger("prefetch_icons")

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "qianji" / "server" / "static" / "caticons"
DEFAULT_CONCURRENCY = 8


async def prefetch(
    names: list[str],
    *,
    out_dir: Path,
    concurrency: int = DEFAULT_CONCURRENCY,
    force: bool = False,
    fetch=fetch_icon,
) -> tuple[int, int, list[str]]:
    """Download ``names`` into ``out_dir``.

    Returns ``(written, skipped, failures)``. Individual failures are collected
    rather than raised so one unreachable icon never aborts the run.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    written = 0
    skipped = 0
    failures: list[str] = []

    async def one(name: str) -> None:
        nonlocal written, skipped
        target = out_dir / name
        if target.is_file() and not force:
            skipped += 1
            return
        async with semaphore:
            content = await fetch(name)
        if content is None:
            failures.append(name)
            return
        target.write_bytes(content)
        written += 1

    await asyncio.gather(*(one(name) for name in names))
    return written, skipped, sorted(failures)


async def _load_icon_names() -> list[str]:
    # iconlist is served without authentication; uses a throwaway database so
    # the script leaves no session state behind.
    with tempfile.TemporaryDirectory(prefix="qianji-prefetch-") as tmp:
        client = QianjiClient(db_path=Path(tmp) / "session.db")
        async with client:
            payload = await client._request_public("category", "iconlist", {})
    return icon_names_from_payload(payload)


async def _run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    names = await _load_icon_names()
    if not names:
        logger.error("Icon list is empty; nothing to prefetch")
        return 1

    written, skipped, failures = await prefetch(
        names,
        out_dir=out_dir,
        concurrency=args.concurrency,
        force=args.force,
    )
    manifest = {"count": len(names), "names": names}
    try:
        (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write manifest: %s", exc)

    logger.info(
        "Icons: %d written, %d already present, %d failed (total %d)",
        written,
        skipped,
        len(failures),
        len(names),
    )
    for name in failures:
        logger.warning("Failed: %s", name)

    if written + skipped == 0:
        logger.error("No icons could be prefetched")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prefetch Qianji category icons")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="target directory for the .png files")
    parser.add_argument("--force", action="store_true", help="re-download icons that already exist")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="parallel downloads")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # One "HTTP Request" line per icon would bury the summary.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # network unavailable, upstream changed, ...
        logger.error("Icon prefetch failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
