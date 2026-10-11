from __future__ import annotations

import pytest

from qianji import cli
from qianji.version import __version__


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("QIANJI_HOST", "QIANJI_PORT", "QIANJI_RELOAD", "MCP_HOST", "MCP_PORT", "QIANJI_FORWARDED_ALLOW_IPS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _capture_uvicorn(monkeypatch) -> dict:
    import uvicorn

    captured: dict = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return captured


def test_run_api_defaults_to_localhost(clean_env):
    captured = _capture_uvicorn(clean_env)

    cli.run_api([])

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 50003
    assert captured["reload"] is False


def test_run_api_env_overrides_defaults(clean_env):
    clean_env.setenv("QIANJI_HOST", "0.0.0.0")
    clean_env.setenv("QIANJI_PORT", "59999")
    captured = _capture_uvicorn(clean_env)

    cli.run_api([])

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 59999


def test_run_api_cli_args_override_env(clean_env):
    clean_env.setenv("QIANJI_HOST", "0.0.0.0")
    clean_env.setenv("QIANJI_PORT", "59999")
    captured = _capture_uvicorn(clean_env)

    cli.run_api(["--host", "192.168.1.5", "--port", "8123"])

    assert captured["host"] == "192.168.1.5"
    assert captured["port"] == 8123


def test_run_api_reload_flag(clean_env):
    captured = _capture_uvicorn(clean_env)

    cli.run_api(["--reload"])

    assert captured["reload"] is True
    assert captured["reload_dirs"]


def test_run_api_version(clean_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.run_api(["--version"])

    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_run_api_help(clean_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.run_api(["--help"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--host" in out and "--port" in out and "--version" in out


def _patch_mcp_run(monkeypatch) -> dict:
    import uvicorn

    from qianji import mcp_server

    captured: dict = {"closed": 0}

    class FakeServer:
        def __init__(self, config):
            captured["config"] = config

        async def serve(self) -> None:
            captured["host"] = captured["config"].host
            captured["port"] = captured["config"].port

    async def fake_close() -> None:
        captured["closed"] += 1

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(mcp_server, "close_client", fake_close)
    return captured


def test_run_mcp_http_defaults_to_localhost(clean_env):
    captured = _patch_mcp_run(clean_env)

    cli.run_mcp_http([])

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 50004
    assert captured["closed"] == 1


def test_run_mcp_http_cli_args_override_env(clean_env):
    clean_env.setenv("MCP_HOST", "0.0.0.0")
    clean_env.setenv("MCP_PORT", "59998")
    captured = _patch_mcp_run(clean_env)

    cli.run_mcp_http(["--host", "10.0.0.2", "--port", "9000"])

    assert captured["host"] == "10.0.0.2"
    assert captured["port"] == 9000


def test_run_mcp_http_closes_client_when_server_raises(clean_env):
    import uvicorn

    captured = _patch_mcp_run(clean_env)

    class FailingServer:
        def __init__(self, config):
            pass

        async def serve(self) -> None:
            raise RuntimeError("boom")

    clean_env.setattr(uvicorn, "Server", FailingServer)

    with pytest.raises(RuntimeError, match="boom"):
        cli.run_mcp_http([])

    assert captured["closed"] == 1


def test_run_mcp_http_version(clean_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.run_mcp_http(["--version"])

    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_run_api_does_not_trust_proxy_headers_by_default(clean_env):
    captured = _capture_uvicorn(clean_env)

    cli.run_api([])

    assert captured["proxy_headers"] is False
    assert captured["forwarded_allow_ips"] is None


def test_run_api_forwarded_allow_ips_from_env(clean_env):
    clean_env.setenv("QIANJI_FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    captured = _capture_uvicorn(clean_env)

    cli.run_api([])

    assert captured["proxy_headers"] is True
    assert captured["forwarded_allow_ips"] == "10.0.0.0/8"


def test_run_api_writes_back_effective_host_and_port(clean_env):
    import os

    _capture_uvicorn(clean_env)

    cli.run_api(["--host", "192.168.1.5", "--port", "8123"])

    assert os.environ["QIANJI_HOST"] == "192.168.1.5"
    assert os.environ["QIANJI_PORT"] == "8123"


def test_run_mcp_http_does_not_trust_proxy_headers_by_default(clean_env):
    captured = _patch_mcp_run(clean_env)

    cli.run_mcp_http([])

    assert captured["config"].proxy_headers is False


def test_run_mcp_http_forwarded_allow_ips_from_env(clean_env):
    clean_env.setenv("QIANJI_FORWARDED_ALLOW_IPS", "192.168.0.0/16")
    captured = _patch_mcp_run(clean_env)

    cli.run_mcp_http([])

    assert captured["config"].proxy_headers is True
    assert captured["config"].forwarded_allow_ips == "192.168.0.0/16"
