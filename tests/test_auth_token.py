from __future__ import annotations

import base64

from qianji.auth_token import parse_qianji_auth_token

PWD_HASH_UPPER = "ABCDEF0123456789ABCDEF0123456789"
PWD_HASH = PWD_HASH_UPPER.lower()


def test_parse_qj1_md5_token_normalizes_hash():
    result = parse_qianji_auth_token(f"qj1.md5.user@example.com~{PWD_HASH_UPPER}")

    assert result is not None
    assert result.account == "user@example.com"
    assert result.pwd_hash == PWD_HASH


def test_parse_qj1_md5_b64_token_accepts_urlsafe_unpadded_utf8():
    payload = base64.urlsafe_b64encode(f"用户@example.com~{PWD_HASH_UPPER}".encode()).decode().rstrip("=")

    result = parse_qianji_auth_token(f"qj1.md5-b64.{payload}")

    assert result is not None
    assert result.account == "用户@example.com"
    assert result.pwd_hash == PWD_HASH


def test_parse_qj1_auth_rejects_legacy_and_plaintext_forms():
    invalid_tokens = [
        "user@example.com~password",
        base64.b64encode(b"user@example.com~password").decode("ascii"),
        "qj1.plain.user@example.com~password",
        f"qj1.plain-b64.{base64.b64encode(b'user@example.com~password').decode('ascii')}",
        "qj1.md5.user@example.com~password",
        "qj1.md5-b64.not-base64!",
    ]

    for token in invalid_tokens:
        assert parse_qianji_auth_token(token) is None
