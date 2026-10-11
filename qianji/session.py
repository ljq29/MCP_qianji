from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import aiosqlite

logger = logging.getLogger(__name__)


def _coerce_int(value: object, default: int | None = None) -> int | None:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass
class SessionData:
    account_id: str
    email: str
    pwd_hash: str
    utoken: str = ""
    user_id: str = ""
    user_name: str = ""
    devid: str = ""
    devname: str = ""
    books: list[dict] = field(default_factory=list)
    user_configs: dict[str, object] = field(default_factory=dict)
    login_time: float = 0.0
    vip_type: int = 0
    vip_end: int = 0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id   TEXT PRIMARY KEY,
    email        TEXT NOT NULL,
    pwd_hash     TEXT NOT NULL,
    utoken       TEXT DEFAULT '',
    user_id      TEXT DEFAULT '',
    user_name    TEXT DEFAULT '',
    devid        TEXT NOT NULL,
    devname      TEXT NOT NULL,
    books        TEXT DEFAULT '[]',
    login_time   REAL DEFAULT 0,
    vip_type     INTEGER DEFAULT 0,
    vip_end      INTEGER DEFAULT 0
);
"""

_BILL_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bills_cache (
    account_id  TEXT NOT NULL,
    bill_id     INTEGER NOT NULL,
    book_id     INTEGER NOT NULL,
    bill_time   INTEGER NOT NULL,
    updatetime  INTEGER NOT NULL DEFAULT 0,
    status      INTEGER NOT NULL DEFAULT 1,
    bill_json   TEXT NOT NULL,
    PRIMARY KEY (account_id, bill_id)
);
"""

_BILL_CACHE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_bills_cache_time
    ON bills_cache(account_id, book_id, bill_time DESC, bill_id DESC);
"""

_BILL_SYNC_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bill_sync_state (
    account_id  TEXT NOT NULL,
    book_id     INTEGER NOT NULL,
    last_time   INTEGER NOT NULL DEFAULT 0,
    synced_at   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, book_id)
);
"""

_ACCOUNT_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_settings (
    account_id TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (account_id, key)
);
"""

_GLOBAL_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS global_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT 0
);
"""

_GENERATED_FILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS generated_files (
    file_id      TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    content_type TEXT NOT NULL,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    content      BLOB NOT NULL
);
"""

_GENERATED_FILES_EXPIRES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_generated_files_expires
    ON generated_files(expires_at);
"""


class SessionManager:
    def __init__(self, db_path: Path | str = ".qianji.db"):
        self._db_path = Path(db_path)
        self._lock = asyncio.Lock()
        self._conn: aiosqlite.Connection | None = None

    async def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
            await self._conn.execute(_SCHEMA)
            await self._conn.execute(_BILL_CACHE_SCHEMA)
            await self._conn.execute(_BILL_CACHE_INDEX)
            await self._conn.execute(_BILL_SYNC_STATE_SCHEMA)
            await self._conn.execute(_ACCOUNT_SETTINGS_SCHEMA)
            await self._conn.execute(_GLOBAL_SETTINGS_SCHEMA)
            await self._conn.execute(_GENERATED_FILES_SCHEMA)
            await self._conn.execute(_GENERATED_FILES_EXPIRES_INDEX)
            await self._ensure_accounts_user_configs_column(self._conn)
            await self._conn.commit()
        return self._conn

    async def _ensure_accounts_user_configs_column(self, conn: aiosqlite.Connection) -> None:
        cur = await conn.execute("PRAGMA table_info(accounts)")
        columns = await cur.fetchall()
        if "user_configs" not in {row["name"] for row in columns}:
            await conn.execute("ALTER TABLE accounts ADD COLUMN user_configs TEXT DEFAULT '{}'")

    def _row_to_session(self, row: sqlite3.Row) -> SessionData:
        user_configs = _json_object(row["user_configs"]) if "user_configs" in row.keys() else {}
        return SessionData(
            account_id=row["account_id"],
            email=row["email"],
            pwd_hash=row["pwd_hash"],
            utoken=row["utoken"] or "",
            user_id=row["user_id"] or "",
            user_name=row["user_name"] or "",
            devid=row["devid"],
            devname=row["devname"],
            books=json.loads(row["books"]) if row["books"] else [],
            user_configs=user_configs,
            login_time=row["login_time"] or 0.0,
            vip_type=row["vip_type"] or 0,
            vip_end=row["vip_end"] or 0,
        )

    async def find_by_credentials(self, email: str, pwd_hash: str) -> SessionData | None:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                "SELECT * FROM accounts WHERE email = ? AND pwd_hash = ? LIMIT 1",
                (email, pwd_hash),
            )
            row = await cur.fetchone()
        return self._row_to_session(row) if row else None

    async def find_by_email(self, email: str) -> SessionData | None:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                "SELECT * FROM accounts WHERE email = ? ORDER BY login_time DESC LIMIT 1",
                (email,),
            )
            row = await cur.fetchone()
        return self._row_to_session(row) if row else None

    async def find_migrated_entry(self, email: str) -> SessionData | None:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                "SELECT * FROM accounts WHERE email = ? AND pwd_hash = '' LIMIT 1",
                (email,),
            )
            row = await cur.fetchone()
        return self._row_to_session(row) if row else None

    async def load(self, account_id: str) -> SessionData | None:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,))
            row = await cur.fetchone()
        return self._row_to_session(row) if row else None

    async def save(self, data: SessionData) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """INSERT OR REPLACE INTO accounts
                   (account_id, email, pwd_hash, utoken, user_id, user_name,
                    devid, devname, books, user_configs, login_time, vip_type, vip_end)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data.account_id,
                    data.email,
                    data.pwd_hash,
                    data.utoken,
                    data.user_id,
                    data.user_name,
                    data.devid,
                    data.devname,
                    json.dumps(data.books, ensure_ascii=False),
                    json.dumps(data.user_configs, ensure_ascii=False),
                    data.login_time,
                    data.vip_type,
                    data.vip_end,
                ),
            )
            await conn.commit()

    async def update_token(self, account_id: str, utoken: str) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                "UPDATE accounts SET utoken = ? WHERE account_id = ?",
                (utoken, account_id),
            )
            await conn.commit()

    async def delete(self, account_id: str) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
            await conn.commit()

    async def list_accounts(self) -> list[dict]:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                "SELECT account_id, email, user_name, devname FROM accounts ORDER BY login_time DESC"
            )
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def load_all(self) -> dict[str, SessionData]:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute("SELECT * FROM accounts ORDER BY login_time DESC")
            rows = await cur.fetchall()
            return {row["account_id"]: self._row_to_session(row) for row in rows}

    async def get_or_create_device_identity(self, account_id: str) -> tuple[str, str]:
        session = await self.load(account_id)
        if session and session.devid:
            return session.devid, session.devname
        devid = str(uuid.uuid4())
        devname = f"MCP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return devid, devname

    async def generate_account_id(self, email: str) -> str:
        if not await self.load(email):
            return email
        n = 2
        while await self.load(f"{email}#{n}"):
            n += 1
        return f"{email}#{n}"

    async def migrate_from_json(self, json_dir: Path | str) -> int:
        json_dir = Path(json_dir)
        if not json_dir.exists():
            return 0
        count = 0
        for p in json_dir.glob("*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                email = raw.get("email", "")
                if not email:
                    continue
                existing = await self.load(email)
                if existing:
                    continue
                await self.save(
                    SessionData(
                        account_id=email,
                        email=email,
                        pwd_hash=raw.get("pwd_hash", ""),
                        utoken=raw.get("utoken", ""),
                        user_id=raw.get("user_id", ""),
                        user_name=raw.get("user_name", ""),
                        devid=raw.get("devid", str(uuid.uuid4())),
                        devname=raw.get("devname", f"MCP_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
                        books=raw.get("books", []),
                        login_time=raw.get("login_time", 0.0),
                        vip_type=raw.get("vip_type", 0),
                        vip_end=raw.get("vip_end", 0),
                    )
                )
                count += 1
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Skipping malformed session file %s: %s", p, exc)
                continue
        return count

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    # ---- Bill cache ----

    async def cache_upsert_bills(self, account_id: str, bills: list[dict]) -> int:
        if not bills:
            return 0
        rows = []
        for bill in bills:
            bill_id = _coerce_int(bill.get("id"))
            if bill_id is None:
                continue
            rows.append(
                (
                    account_id,
                    bill_id,
                    _coerce_int(bill.get("bookid"), -1),
                    _coerce_int(bill.get("time") or bill.get("createtime"), 0),
                    _coerce_int(bill.get("updatetime"), 0),
                    _coerce_int(bill.get("status"), 1),
                    json.dumps(bill, ensure_ascii=False),
                )
            )
        if not rows:
            return 0
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.executemany(
                """INSERT OR REPLACE INTO bills_cache
                   (account_id, bill_id, book_id, bill_time, updatetime, status, bill_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            await conn.commit()
        return len(rows)

    async def cache_delete_bills(self, account_id: str, bill_ids: list) -> int:
        ids = [bid for bid in (_coerce_int(b) for b in bill_ids) if bid is not None]
        if not ids:
            return 0
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.executemany(
                "DELETE FROM bills_cache WHERE account_id = ? AND bill_id = ?",
                [(account_id, bid) for bid in ids],
            )
            await conn.commit()
        return len(ids)

    async def cache_get_bill(self, account_id: str, bill_id: int) -> dict | None:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                "SELECT bill_json FROM bills_cache WHERE account_id = ? AND bill_id = ?",
                (account_id, int(bill_id)),
            )
            row = await cur.fetchone()
        return json.loads(row["bill_json"]) if row else None

    async def cache_list_bills(
        self,
        account_id: str,
        *,
        book_id: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 50,
        before_time: int | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        clauses = ["account_id = ?", "status != 0"]
        params: list = [account_id]
        if book_id is not None:
            clauses.append("book_id = ?")
            params.append(int(book_id))
        if start_time is not None:
            clauses.append("bill_time >= ?")
            params.append(int(start_time))
        if end_time is not None:
            clauses.append("bill_time <= ?")
            params.append(int(end_time))
        if before_time is not None and before_id is not None:
            clauses.append("(bill_time < ? OR (bill_time = ? AND bill_id < ?))")
            params.extend([int(before_time), int(before_time), int(before_id)])
        sql = (
            "SELECT bill_json FROM bills_cache WHERE "
            + " AND ".join(clauses)
            + " ORDER BY bill_time DESC, bill_id DESC LIMIT ?"
        )
        params.append(int(limit))
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
        return [json.loads(row["bill_json"]) for row in rows]

    async def cache_search_bills(
        self,
        account_id: str,
        *,
        book_id: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        bill_types: list[int] | None = None,
        limit: int | None = None,
        sort_order: str = "desc",
    ) -> list[dict]:
        clauses = ["account_id = ?", "status != 0"]
        params: list = [account_id]
        if book_id is not None:
            clauses.append("book_id = ?")
            params.append(int(book_id))
        if start_time is not None:
            clauses.append("bill_time >= ?")
            params.append(int(start_time))
        if end_time is not None:
            clauses.append("bill_time <= ?")
            params.append(int(end_time))
        type_codes = [int(value) for value in bill_types or []]
        if type_codes:
            placeholders = ", ".join("?" for _ in type_codes)
            clauses.append(f"CAST(json_extract(bill_json, '$.type') AS INTEGER) IN ({placeholders})")
            params.extend(type_codes)
        order = "ASC" if sort_order.lower() == "asc" else "DESC"
        sql = (
            "SELECT bill_json FROM bills_cache WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY bill_time {order}, bill_id {order}"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
        return [json.loads(row["bill_json"]) for row in rows]

    async def get_bill_sync_state(self, account_id: str, book_id: int) -> tuple[int, float]:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                "SELECT last_time, synced_at FROM bill_sync_state WHERE account_id = ? AND book_id = ?",
                (account_id, int(book_id)),
            )
            row = await cur.fetchone()
        if row is None:
            return 0, 0.0
        return int(row["last_time"] or 0), float(row["synced_at"] or 0.0)

    async def get_all_bill_sync_times(self, account_id: str) -> dict[str, int]:
        """Per-book last_time watermarks as the {book_id: ts} map syncv2/pull expects."""
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                "SELECT book_id, last_time FROM bill_sync_state WHERE account_id = ?",
                (account_id,),
            )
            rows = await cur.fetchall()
        return {str(row["book_id"]): int(row["last_time"] or 0) for row in rows}

    async def set_bill_sync_state(self, account_id: str, book_id: int, last_time: int, synced_at: float) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """INSERT OR REPLACE INTO bill_sync_state (account_id, book_id, last_time, synced_at)
                   VALUES (?, ?, ?, ?)""",
                (account_id, int(book_id), int(last_time), float(synced_at)),
            )
            await conn.commit()

    # ---- Per-account settings (generic KV) ----

    async def get_account_setting(self, account_id: str, key: str) -> str | None:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                "SELECT value FROM account_settings WHERE account_id = ? AND key = ?",
                (account_id, key),
            )
            row = await cur.fetchone()
        return row["value"] if row else None

    async def set_account_setting(self, account_id: str, key: str, value: str) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                "INSERT OR REPLACE INTO account_settings (account_id, key, value) VALUES (?, ?, ?)",
                (account_id, key, str(value)),
            )
            await conn.commit()

    async def delete_account_setting(self, account_id: str, key: str) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                "DELETE FROM account_settings WHERE account_id = ? AND key = ?",
                (account_id, key),
            )
            await conn.commit()

    # ---- Global settings (generic KV) ----

    async def get_global_setting(self, key: str) -> str | None:
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute("SELECT value FROM global_settings WHERE key = ?", (key,))
            row = await cur.fetchone()
        return row["value"] if row else None

    async def set_global_setting(self, key: str, value: str, *, updated_at: float | None = None) -> None:
        timestamp = datetime.now().timestamp() if updated_at is None else float(updated_at)
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                "INSERT OR REPLACE INTO global_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, str(value), timestamp),
            )
            await conn.commit()

    async def delete_global_setting(self, key: str) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute("DELETE FROM global_settings WHERE key = ?", (key,))
            await conn.commit()

    # ---- Generated temporary files ----

    async def store_generated_file(
        self,
        *,
        file_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        created_at: float,
        expires_at: float,
    ) -> None:
        async with self._lock:
            conn = await self._ensure_conn()
            await conn.execute(
                """INSERT OR REPLACE INTO generated_files
                   (file_id, filename, content_type, created_at, expires_at, content)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (file_id, filename, content_type, float(created_at), float(expires_at), content),
            )
            await conn.commit()

    async def get_generated_file(self, file_id: str, *, now: float | None = None) -> dict | None:
        timestamp = datetime.now().timestamp() if now is None else float(now)
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute(
                """SELECT file_id, filename, content_type, created_at, expires_at, content
                   FROM generated_files
                   WHERE file_id = ? AND expires_at >= ?""",
                (file_id, timestamp),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "file_id": row["file_id"],
            "filename": row["filename"],
            "content_type": row["content_type"],
            "created_at": float(row["created_at"]),
            "expires_at": float(row["expires_at"]),
            "content": bytes(row["content"]),
        }

    async def delete_expired_generated_files(self, *, now: float | None = None) -> int:
        timestamp = datetime.now().timestamp() if now is None else float(now)
        async with self._lock:
            conn = await self._ensure_conn()
            cur = await conn.execute("DELETE FROM generated_files WHERE expires_at < ?", (timestamp,))
            await conn.commit()
        return int(cur.rowcount or 0)
