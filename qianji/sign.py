import hashlib
import re
import uuid

_ENCREQID_SALT = "michaeljackson"
_TOK_CONSTANT = "1172020"
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


# Upstream Qianji protocol requires raw MD5 — not a security choice.
def hash_password(plaintext: str) -> str:
    return md5(plaintext)


def is_md5_hash(value: object) -> bool:
    return isinstance(value, str) and _MD5_RE.fullmatch(value) is not None


def generate_sign(ctrl: str, act: str) -> tuple[str, str]:
    reqidv2 = md5(str(uuid.uuid4()))
    encreqid = md5(reqidv2 + _ENCREQID_SALT)
    tok = md5(reqidv2 + _TOK_CONSTANT + ctrl + encreqid + act)
    return reqidv2, tok
