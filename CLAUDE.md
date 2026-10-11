# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**中文项目指南：@AGENTS.md**（版本纪律与发版顺序、架构要点以 AGENTS.md 为准；发版时同步更新其中「当前版本」号）。

## What this is

Reverse-engineered client for the Qianji (钱迹) accounting app. Reimplements the app's request signing in pure Python (no SO emulation / Frida / emulator) and exposes it three ways from one package: a typed async SDK, a FastAPI HTTP API, and an MCP server. The HTTP API and MCP server run in the same process and share one `QianjiClient`.

## Commands

```bash
pip install -e ".[test]"     # install with test deps
python -m pytest -q          # run all tests (offline by default)
python -m pytest tests/test_endpoints.py -q          # single file
python -m pytest tests/test_client.py::test_name -q  # single test
python scripts/run_api.py    # run HTTP API + MCP on :50003 (QIANJI_HOST/QIANJI_PORT to override)
```

Integration tests (`tests/test_integration.py`, marked `integration`) hit the real Qianji API and are skipped unless `QIANJI_TEST_EMAIL` / `QIANJI_TEST_PASSWORD` are set. There is no linter configured.

## Version Policy

- When the user asks to raise a "小版本号" or patch version, increment only `z` in `x.y.z` by 1.
- When the user asks to raise a middle/minor version, increment `y` in `x.y.z`.
- When the user asks to raise a major version, increment `x` in `x.y.z`.
- Every version bump must include a `CHANGELOG.md` entry for the new version.

## Architecture

### The endpoint registry is the source of truth

`qianji/endpoints.py` holds `ENDPOINTS`, a tuple of `EndpointSpec` declarations covering all ~112 upstream endpoints. This single list drives method/route/tool generation across all three surfaces:

- **SDK methods**: `install_endpoint_methods(QianjiClient)` (called at the bottom of `client.py`) attaches a generated async method named `{ctrl}_{act}` (e.g. `asset_list`) for every spec, plus its aliases. `_is_handwritten` skips any name already defined by a hand-written mixin so the registry never clobbers richer logic.
- **HTTP routes**: `qianji/server/routes.py` loops `ENDPOINTS` to register mirrored `POST /{ctrl}/{act}` routes, on top of the hand-written REST routes (`/bills`, `/assets`, etc.) and the generic passthrough `POST /api/{ctrl}/{act}`.
- **MCP tools**: `qianji/mcp_server.py` loops `ENDPOINTS` to register one dynamic tool per endpoint, alongside the curated high-level tools. The curated tools return structured `dict` (annotated `-> dict[str, Any]` so FastMCP emits structuredContent); the dynamic endpoint tools return JSON text via `_fmt`.

When adding or changing an upstream endpoint, edit `ENDPOINTS` — do not hand-write a method unless it needs custom logic (param shaping, multi-call orchestration). `ParamSpec` controls aliases (friendly names like `book_id` → `bookid`), `hash_if_alias` (auto-MD5 passwords), and `auto` (session-filled values like `uid`).

### Hand-written SDK mixins

`QianjiClient` multiple-inherits from mixins in `qianji/api/` (`BillAPI`, `CategoryAPI`, `SyncAPI`, etc.), all extending `BaseAPI`. These hold logic too complex for a declarative spec — e.g. `BillAPI.bill_create` builds the nested `bills.changelist` JSON that `bill/syncall` expects; `SyncAPI.sync_pull` wraps `syncv2/pull`. Bills are created/updated/deleted by submitting full bill dicts through `bill/syncall`, not dedicated endpoints.

### Bill cache and agent-facing query layer

Upstream has **no** bill list / get-by-id endpoint — the only read path is the paginated `syncv2/pull`. So bill reads for agents go through a **local SQLite cache** (`bills_cache` / `bill_sync_state` tables in `session.py`, alongside `accounts`). The cache + query logic lives in `qianji/bill_ops.py`:

- `sync_bills_to_cache` mirrors the official Android client's **server-driven** pagination: the first page sends `pageoffset=0` / `pagesign=""`, and each subsequent page echoes the `bookid`/`pageoffset`/`pagesign` the server returned (it does NOT increment `pageoffset` locally). `lasttimes` is a multi-book map `{"-1": ts, "123": ts, ...}`; one `bookid=-1` sync walks **every** book, and the server's returned `lasttimes` is stored per-book in `bill_sync_state` as the authoritative incremental watermark. `qianji/api/sync.py`'s `sync_pull` therefore takes `last_times` and `page_sign` kwargs.
- `ensure_bill_cache` runs a full sync (always from `bookid=-1`, so all books are covered) on first use, then incremental. Subsequent queries only re-sync when the cache is older than a soft TTL (`QIANJI_BILL_CACHE_TTL`, default 300s; `0` disables; per-account override stored in the `account_settings` KV table via `set_bill_cache_ttl`) or when the caller passes `refresh=True`. `resolve_cache_ttl` resolves per-account override → env default.
- `list_recent_bills` / `get_bills_range` / `get_bill_by_id` read from the cache and return the normalized shape `{items: [Bill], next_cursor, has_more}` via `normalize_bill`. Pagination is keyset (`encode_cursor`/`decode_cursor` over `(bill_time, bill_id)` DESC) so it can walk every bill without `... and N more` truncation. `normalize_bill` resolves a non-null `currency` (per-bill cross-currency value → account base currency via `get_base_currency` → CNY) and records `currency_source`.
- `update_bill_by_id` locates the original bill in the cache (no page scanning), applies `apply_bill_patch`, submits via `client.bill_update`, and writes the result back to the cache. `bill_create` likewise writes the new bill into the cache so it is immediately queryable.

The MCP curated tools and the REST `PATCH /bills/{id}` route both delegate here — keep query-vs-sync separation: only `sync_bills` (MCP) exposes sync internals (`deletes`/`last_sync_token`); normal bookkeeping tools never surface `hasmore`/`lasttimes`/`pageoffset`.

### Request signing

`qianji/sign.py`: every request needs a fresh `tok = MD5(reqidv2 + "1172020" + ctrl + MD5(reqidv2 + "michaeljackson") + act)` where `reqidv2 = MD5(uuid4)`. Passwords are stored and sent as `MD5(plaintext)`. `client._request_raw` assembles all the device/version headers (from `constants.py`) and posts form-encoded params.

### Sessions, accounts, and auto-recovery

`qianji/session.py` persists sessions to SQLite (`.qianji.db`, path via `QIANJI_DB_PATH`). One process can hold many accounts. The active account is chosen by `_effective_account_id()`: a per-request `ContextVar` override (set via `client.use_account(id)`) wins over the process-wide `_current_account`. Always wrap multi-account operations in `with client.use_account(...)`.

`client._request` catches `AuthenticationError` (upstream `ec == 8888`) and auto-recovers by refreshing the token, then re-logging-in with the stored password hash — callers don't handle token expiry. Device identity (`devid`/`devname`) is generated once per account and reused forever.

Upstream `ec` codes map to exceptions in `errors.py`: `400404` → `SignatureError`, `8888` → `AuthenticationError`, any non-200 → `QianjiError`. The FastAPI app translates these to HTTP status codes via exception handlers in `server/app.py`.

### Auth model (HTTP API + MCP)

Both surfaces authenticate with a Bearer token that *is* the Qianji credential: `Bearer account~password` or `Bearer base64(account~password)`, parsed by `qianji/auth_token.py`. The token is used to log in / reuse a session on every request, and operations are hard-bound to that account — passing an `account_id` for a different account returns 403. There is no separate API key.

### Server composition

`qianji/server/app.py` is the entrypoint. Its `lifespan` builds the shared `QianjiClient`, mounts the FastMCP streamable-HTTP app at `/mcp` (path via `MCP_PATH`), migrates any legacy `.qianji_sessions/` JSON, and serves an operation console (`/`), about page, and Swagger/ReDoc docs. `QIANJI_MCP_MODE` (`read` / `write` / `advanced` / `full`, default `advanced`) sets the service default MCP tool scope; `/mcp?mode=write` and the other mode query values override scope for that request only. `read` covers login/account + query tools (`list_recent_bills`/`get_bills_range`/`get_bill_by_id`/`get_assets`/`get_categories`/`list_books`/`get_tags`/`get_currencies`), `write` adds core bookkeeping tools (`create_expense`/`create_income`/`create_transfer` and their update variants) plus `sync_bills`/`set_bill_cache_ttl`/`set_tags_enabled`, `advanced` adds refund/reimbursement/loan/debt business write tools, and `full` exposes every tool including low-level `create_bill`/`update_bill`/`delete_bill`, `call_api`, and all dynamic endpoint tools.

## Conventions

- All client/server/MCP code is `async`; the SDK is built on `httpx.AsyncClient`.
- Upstream params: complex values (dicts/lists) are JSON-stringified, bools become `"1"`/`"0"`, and the convention `v` is the catch-all JSON payload param for submit-style endpoints.
- `tests/test_endpoints.py` auto-generates coverage from `ENDPOINTS` (sample values per `ParamSpec`), so a new spec is exercised without writing a new test.
