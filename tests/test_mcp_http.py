from __future__ import annotations

import asyncio
import base64
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from qianji.session import SessionData, SessionManager
from qianji.sign import hash_password

MCP_EMAIL = "agent@example.com"
MCP_PASSWORD = "secret-pass"
MCP_PWD_HASH = hash_password(MCP_PASSWORD)
MCP_AUTH_HEADER = {"Authorization": f"Bearer qj1.md5.{MCP_EMAIL}~{MCP_PWD_HASH}"}
MCP_AUTH_HEADER_BASE64 = {
    "Authorization": "Bearer qj1.md5-b64." + base64.b64encode(f"{MCP_EMAIL}~{MCP_PWD_HASH}".encode()).decode("ascii")
}


def test_mcp_bearer_verifier_uses_login_md5(monkeypatch):
    from qianji import mcp_server

    class FakeClient:
        current_account = "agent@example.com"

        def __init__(self):
            self.login_md5_calls: list[tuple[str, str, str | None]] = []

        async def login(self, email: str, password: str, account_id: str | None = None):
            raise AssertionError("bearer verifier must not call plaintext login")

        async def login_md5(self, email: str, pwd_hash: str, account_id: str | None = None):
            self.login_md5_calls.append((email, pwd_hash, account_id))
            return {"data": {"user": {"email": email, "name": "Agent"}}}

    fake = FakeClient()

    async def fake_get_client():
        return fake

    monkeypatch.setattr(mcp_server, "_get_client", fake_get_client)

    async def run():
        verifier = mcp_server.QianjiBearerTokenVerifier()
        return await verifier.verify_token(f"qj1.md5.{MCP_EMAIL}~{MCP_PWD_HASH}")

    token = asyncio.run(run())

    assert token is not None
    assert fake.login_md5_calls == [(MCP_EMAIL, MCP_PWD_HASH, MCP_EMAIL)]


def test_console_scripts_expose_http_mcp_only():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert 'qianji-mcp-http = "qianji.cli:run_mcp_http"' in pyproject
    assert 'qianji-mcp = "qianji.cli:run_mcp"' not in pyproject


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _seed_mcp_session(db_path: Path, email: str = MCP_EMAIL, password: str = MCP_PASSWORD) -> None:
    import asyncio

    mgr = SessionManager(db_path)

    async def _seed():
        try:
            await mgr.save(
                SessionData(
                    account_id=email,
                    email=email,
                    pwd_hash=hash_password(password),
                    utoken="token-agent",
                    user_id="user-agent",
                    user_name="Agent",
                    devid="device-agent",
                    devname="Device Agent",
                )
            )
        finally:
            await mgr.close()

    asyncio.run(_seed())


def test_run_mcp_http_configures_streamable_http(monkeypatch):
    import uvicorn

    from qianji import cli, mcp_server

    captured: dict = {}
    closed: list[bool] = []

    class FakeServer:
        def __init__(self, config):
            captured["config"] = config

        async def serve(self) -> None:
            captured["host"] = mcp_server.mcp.settings.host
            captured["port"] = mcp_server.mcp.settings.port
            captured["path"] = mcp_server.mcp.settings.streamable_http_path
            captured["dns"] = mcp_server.mcp.settings.transport_security.enable_dns_rebinding_protection

    async def fake_close() -> None:
        closed.append(True)

    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "51234")
    monkeypatch.setenv("MCP_PATH", "/custom-mcp")
    monkeypatch.delenv("QIANJI_MCP_MODE", raising=False)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(mcp_server, "close_client", fake_close)

    try:
        cli.run_mcp_http([])
    finally:
        mcp_server.configure_mcp_tools("advanced")

    assert (captured["host"], captured["port"], captured["path"], captured["dns"]) == (
        "127.0.0.1",
        51234,
        "/custom-mcp",
        False,
    )
    assert closed == [True]


def test_run_api_serves_mcp_on_same_port(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "api-mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "QIANJI_HOST": "127.0.0.1",
            "QIANJI_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_MCP_MODE": "write",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_api; run_api()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        names = _wait_for_tools(f"http://127.0.0.1:{port}/mcp", proc, headers=MCP_AUTH_HEADER)
        assert {"login", "get_assets", "create_expense", "create_income", "create_transfer"}.issubset(names)
        assert {"create_bill", "update_bill", "delete_bill", "call_api"}.isdisjoint(names)
        assert httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2.0).json() == {"ok": True}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_default_advanced_mode_uses_business_import_tools():
    from qianji import mcp_server

    try:
        assert mcp_server.get_mcp_mode() == "advanced"
        assert mcp_server.get_mcp_tool_names_for_mode("read") == {
            "login",
            "list_accounts",
            "switch_account",
            "get_login_status",
            "search_bills",
            "export_bills",
            "generate_report_image",
            "get_assets",
            "get_categories",
            "list_books",
            "get_tags",
            "get_tags_enabled",
            "get_currencies",
            "get_bill_cache_ttl",
            "get_budgets",
        }
        assert len(mcp_server.get_mcp_tool_names_for_mode("read")) == 15
        assert {
            "create_expense",
            "create_income",
            "create_transfer",
            "update_expense",
            "update_income",
            "update_transfer",
            "sync_bills",
            "set_bill_cache_ttl",
            "set_tags_enabled",
            "upload_bill_image",
            "create_tag",
            "update_tag",
            "delete_tag",
            "archive_tag",
            "create_tag_group",
            "update_tag_group",
            "delete_tag_group",
            "reorder_tags",
            "reorder_tag_groups",
        }.issubset(mcp_server.get_mcp_tool_names_for_mode("write"))
        assert {
            "create_refund",
            "update_refund",
            "create_reimbursement",
            "update_reimbursement",
            "create_loan_asset",
            "update_loan_asset",
            "add_loan_principal",
            "record_loan_collection",
            "create_debt_asset",
            "record_debt_repayment",
        }.isdisjoint(mcp_server.get_mcp_tool_names_for_mode("write"))
        assert len(mcp_server.get_mcp_tool_names_for_mode("write")) == 34
        assert {
            "create_refund",
            "update_refund",
            "create_reimbursement",
            "update_reimbursement",
            "cancel_reimbursement",
            "create_loan_asset",
            "update_loan_asset",
            "add_loan_principal",
            "record_loan_collection",
            "create_debt_asset",
            "record_debt_repayment",
        }.issubset(mcp_server.get_mcp_tool_names_for_mode("advanced"))
        assert {"create_bill", "update_bill", "delete_bill", "call_api", "asset_info"}.isdisjoint(
            mcp_server.get_mcp_tool_names_for_mode("advanced")
        )
        assert len(mcp_server.get_mcp_tool_names_for_mode("advanced")) == 51
        assert {"create_bill", "update_bill", "delete_bill", "call_api"}.issubset(
            mcp_server.get_mcp_tool_names_for_mode("full")
        )
    finally:
        mcp_server.configure_mcp_tools("advanced")


def test_mcp_rejects_invalid_mode():
    from qianji import mcp_server

    try:
        try:
            mcp_server.configure_mcp_tools("bad")
        except ValueError as exc:
            assert "QIANJI_MCP_MODE" in str(exc)
            assert "read" in str(exc)
            assert "write" in str(exc)
            assert "advanced" in str(exc)
            assert "full" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("Invalid MCP mode was accepted")
    finally:
        mcp_server.configure_mcp_tools("advanced")


def test_configure_mcp_tools_sets_process_default_over_env(monkeypatch):
    from qianji import mcp_server

    monkeypatch.setenv("QIANJI_MCP_MODE", "advanced")
    try:
        assert mcp_server.configure_mcp_tools("write") == "write"
        assert mcp_server.get_mcp_mode() == "write"
        assert len(mcp_server.get_mcp_tool_names_for_mode()) == 34
    finally:
        mcp_server.configure_mcp_tools("advanced")


def test_mcp_http_server_lists_default_advanced_tools(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    env.pop("QIANJI_MCP_MODE", None)
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        names = _wait_for_tools(f"http://127.0.0.1:{port}/mcp", proc, headers=MCP_AUTH_HEADER)
        assert {
            "login",
            "get_assets",
            "create_expense",
            "create_income",
            "create_transfer",
            "create_refund",
            "create_reimbursement",
            "create_loan_asset",
            "create_debt_asset",
        }.issubset(names)
        assert len(names) == 51
        assert {"create_bill", "update_bill", "delete_bill", "call_api"}.isdisjoint(names)
        assert "asset_info" not in names
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_http_server_uses_env_write_without_query(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_MCP_MODE": "write",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        names = _wait_for_tools(f"http://127.0.0.1:{port}/mcp", proc, headers=MCP_AUTH_HEADER)
        assert {"login", "get_assets", "create_expense", "create_income", "create_transfer"}.issubset(names)
        assert {"create_refund", "create_reimbursement", "create_loan_asset", "create_debt_asset"}.isdisjoint(names)
        assert {"create_bill", "update_bill", "delete_bill", "call_api"}.isdisjoint(names)
        assert len(names) == 34
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_http_query_mode_can_narrow_advanced_default_to_write(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_MCP_MODE": "advanced",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        names = _wait_for_tools(f"http://127.0.0.1:{port}/mcp?mode=write", proc, headers=MCP_AUTH_HEADER)
        assert {"login", "get_assets", "create_expense", "create_income", "create_transfer"}.issubset(names)
        assert {"create_refund", "create_reimbursement", "create_loan_asset", "create_debt_asset"}.isdisjoint(names)
        assert len(names) == 34
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_http_query_mode_can_widen_write_default_to_advanced(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_MCP_MODE": "write",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        names = _wait_for_tools(f"http://127.0.0.1:{port}/mcp?mode=advanced", proc, headers=MCP_AUTH_HEADER)
        assert {"create_refund", "create_reimbursement", "create_loan_asset", "create_debt_asset"}.issubset(names)
        assert {"create_bill", "update_bill", "delete_bill", "call_api"}.isdisjoint(names)
        assert len(names) == 51
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_http_query_mode_blocks_disallowed_tool_calls(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_MCP_MODE": "advanced",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        write_text, write_is_error = _wait_for_tool_result(
            f"http://127.0.0.1:{port}/mcp?mode=write",
            "create_refund",
            headers=MCP_AUTH_HEADER,
        )
        advanced_text, advanced_is_error = _wait_for_tool_result(
            f"http://127.0.0.1:{port}/mcp?mode=advanced",
            "create_refund",
            headers=MCP_AUTH_HEADER,
        )
        assert write_is_error
        assert "not allowed in MCP mode 'write'" in write_text
        assert advanced_is_error
        assert "not allowed" not in advanced_text
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_http_query_mode_rejects_invalid_mode(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_MCP_MODE": "advanced",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        error = _wait_for_list_tools_error(f"http://127.0.0.1:{port}/mcp?mode=bad", proc, headers=MCP_AUTH_HEADER)
        assert "Invalid QIANJI_MCP_MODE='bad'" in error
        assert "advanced" in error
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_http_server_lists_full_tools(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_MCP_MODE": "full",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        names = _wait_for_tools(f"http://127.0.0.1:{port}/mcp", proc, headers=MCP_AUTH_HEADER)
        assert {
            "login",
            "get_assets",
            "create_expense",
            "create_income",
            "create_transfer",
            "create_bill",
            "update_bill",
            "delete_bill",
            "call_api",
            "asset_info",
        }.issubset(names)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_http_server_requires_bearer_auth(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        status = _wait_for_status(f"http://127.0.0.1:{port}/mcp", proc)
        assert status == 401
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_http_auth_selects_bearer_account(tmp_path):
    port = _free_port()
    env = os.environ.copy()
    db_path = tmp_path / "mcp.db"
    _seed_mcp_session(db_path)
    env.update(
        {
            "MCP_HOST": "127.0.0.1",
            "MCP_PORT": str(port),
            "MCP_PATH": "/mcp",
            "QIANJI_DB_PATH": str(db_path),
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from qianji.cli import run_mcp_http; run_mcp_http()"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        result, is_error = _wait_for_tool_result(
            f"http://127.0.0.1:{port}/mcp",
            "list_accounts",
            headers=MCP_AUTH_HEADER_BASE64,
        )
        assert not is_error
        assert "agent@example.com" in result
        assert '"account_id": "agent@example.com"' in result
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _wait_for_tools(url: str, proc: subprocess.Popen[str], headers: dict[str, str]) -> set[str]:
    deadline = time.time() + 20
    last_error: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            raise AssertionError(
                f"MCP server exited early with {proc.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )
        try:
            return asyncio.run(_list_tools(url, headers=headers))
        except Exception as exc:  # pragma: no cover - retry loop diagnostics
            last_error = exc
            time.sleep(0.5)
    raise AssertionError(f"MCP HTTP server did not become ready: {last_error!r}")


def _wait_for_tool_result(url: str, tool_name: str, headers: dict[str, str]) -> tuple[str, bool]:
    deadline = time.time() + 20
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return asyncio.run(_call_tool(url, tool_name, headers=headers))
        except Exception as exc:  # pragma: no cover - retry loop diagnostics
            last_error = exc
            time.sleep(0.5)
    raise AssertionError(f"MCP HTTP server did not become ready: {last_error!r}")


def _wait_for_list_tools_error(url: str, proc: subprocess.Popen[str], headers: dict[str, str]) -> str:
    deadline = time.time() + 20
    last_error: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            raise AssertionError(
                f"MCP server exited early with {proc.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )
        try:
            asyncio.run(_list_tools(url, headers=headers))
        except Exception as exc:
            error = _exception_text(exc)
            if "Invalid QIANJI_MCP_MODE" in error:
                return error
            last_error = exc
        time.sleep(0.5)
    raise AssertionError(f"MCP HTTP server did not reject invalid mode: {last_error!r}")


def _exception_text(exc: BaseException) -> str:
    parts = [str(exc)]
    for nested in getattr(exc, "exceptions", ()):
        parts.append(_exception_text(nested))
    return "\n".join(part for part in parts if part)


def _wait_for_status(url: str, proc: subprocess.Popen[str]) -> int:
    deadline = time.time() + 20
    last_error: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            raise AssertionError(
                f"MCP server exited early with {proc.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )
        try:
            return httpx.get(url, timeout=2.0).status_code
        except Exception as exc:  # pragma: no cover - retry loop diagnostics
            last_error = exc
            time.sleep(0.5)
    raise AssertionError(f"MCP HTTP server did not become ready: {last_error!r}")


async def _list_tools(url: str, headers: dict[str, str]) -> set[str]:
    async with httpx.AsyncClient(headers=headers) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return {tool.name for tool in result.tools}


async def _call_tool(url: str, tool_name: str, headers: dict[str, str]) -> tuple[str, bool]:
    async with httpx.AsyncClient(headers=headers) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, {})
                return "\n".join(part.text for part in result.content if getattr(part, "text", None)), bool(
                    result.isError
                )
