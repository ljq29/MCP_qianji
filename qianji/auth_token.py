from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from qianji.sign import is_md5_hash


@dataclass(frozen=True)
class QianjiAuthCredentials:
    account: str
    pwd_hash: str


_MD5_PREFIX = "qj1.md5."
_MD5_B64_PREFIX = "qj1.md5-b64."


def parse_qianji_auth_token(token: str) -> QianjiAuthCredentials | None:
    """Parse qj1 MD5-only Qianji auth tokens."""
    credentials = token.strip()
    if not credentials:
        return None
    if credentials.startswith(_MD5_PREFIX):
        return _split_credentials(credentials[len(_MD5_PREFIX) :])
    if credentials.startswith(_MD5_B64_PREFIX):
        decoded = _decode_base64(credentials[len(_MD5_B64_PREFIX) :])
        if decoded is None:
            return None
        return _split_credentials(decoded)
    return None


def format_qianji_auth_token(account: str, pwd_hash: str, *, b64: bool = True) -> str:
    if not account or "~" in account or not is_md5_hash(pwd_hash):
        raise ValueError("Qianji auth token requires account and 32-character MD5 password hash")
    payload = f"{account}~{pwd_hash.lower()}"
    if b64:
        encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        return _MD5_B64_PREFIX + encoded
    return _MD5_PREFIX + payload


def _decode_base64(value: str) -> str | None:
    if len(value) % 4 == 1:
        return None
    padded = value + ("=" * (-len(value) % 4))
    try:
        return base64.b64decode(padded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        try:
            return base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None


def _split_credentials(credentials: str) -> QianjiAuthCredentials | None:
    if "~" not in credentials:
        return None
    account, pwd_hash = credentials.split("~", 1)
    if not account or not is_md5_hash(pwd_hash):
        return None
    return QianjiAuthCredentials(account=account, pwd_hash=pwd_hash.lower())
