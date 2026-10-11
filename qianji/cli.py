from __future__ import annotations

import argparse
import os
from collections.abc import Sequence


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configure_logging() -> None:
    import logging

    log_level = os.environ.get("QIANJI_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _base_parser(prog: str, description: str) -> argparse.ArgumentParser:
    from qianji.version import __version__

    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--host", default=None, help="Bind host (overrides the environment default)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (overrides the environment default)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run_api(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    parser = _base_parser("qianji-api", "Run the Qianji HTTP API server")
    parser.add_argument(
        "--reload",
        action="store_true",
        default=None,
        help="Enable uvicorn auto-reload (default: QIANJI_RELOAD env var)",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    host = args.host or os.environ.get("QIANJI_HOST", "127.0.0.1")
    port = args.port if args.port is not None else int(os.environ.get("QIANJI_PORT", "50003"))
    reload = args.reload if args.reload else _env_flag("QIANJI_RELOAD")
    # Proxy headers (X-Forwarded-For/Proto) are only trusted when an explicit
    # allow-list is configured; by default they are ignored so rate limiting
    # and URL generation cannot be spoofed by clients.
    forwarded_allow_ips = os.environ.get("QIANJI_FORWARDED_ALLOW_IPS", "").strip()
    # Write back the effective bind address so the app (and a --reload
    # subprocess) can build correct local URLs.
    os.environ["QIANJI_HOST"] = host
    os.environ["QIANJI_PORT"] = str(port)
    uvicorn.run(
        "qianji.server.app:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[os.getcwd()] if reload else None,
        proxy_headers=bool(forwarded_allow_ips),
        forwarded_allow_ips=forwarded_allow_ips or None,
    )


def run_mcp_http(argv: Sequence[str] | None = None) -> None:
    parser = _base_parser("qianji-mcp-http", "Run the Qianji MCP server over streamable HTTP")
    args = parser.parse_args(argv)

    _configure_logging()

    import anyio
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    from qianji import mcp_server

    mcp_server.configure_mcp_tools()
    mcp = mcp_server.mcp
    mcp.settings.host = args.host or os.environ.get("MCP_HOST", "127.0.0.1")
    mcp.settings.port = args.port if args.port is not None else int(os.environ.get("MCP_PORT", "50004"))
    mcp.settings.streamable_http_path = os.environ.get("MCP_PATH", "/mcp")
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    forwarded_allow_ips = os.environ.get("QIANJI_FORWARDED_ALLOW_IPS", "").strip()

    async def _serve() -> None:
        # Mirror FastMCP.run_streamable_http_async but with explicit
        # proxy-header trust (QIANJI_FORWARDED_ALLOW_IPS, empty = untrusted).
        # Run the server and close the client inside the same event loop: the
        # MCP SessionManager binds asyncio primitives and aiosqlite connections
        # to the loop they were created on, so closing from a fresh loop would
        # raise or leak.
        config = uvicorn.Config(
            mcp.streamable_http_app(),
            host=mcp.settings.host,
            port=mcp.settings.port,
            log_level=mcp.settings.log_level.lower(),
            proxy_headers=bool(forwarded_allow_ips),
            forwarded_allow_ips=forwarded_allow_ips or None,
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            await mcp_server.close_client()

    anyio.run(_serve)
