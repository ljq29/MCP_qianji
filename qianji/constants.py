import os
from enum import IntEnum

BASE_URL = "https://api.qianjiapp.com"

DEFAULT_MK = "qianji"
DEFAULT_VSN = "4.3.7"
DEFAULT_VS = "1170"
DEFAULT_PKG = "com.mutangtech.qianji"
DEFAULT_OS = "1"
DEFAULT_CLANG = "zh"
DEFAULT_CREGION = "CN"
DEFAULT_TIMEZONE_OFFSET = "28800"
DEFAULT_DEVBRAND = "MCP"


class BillType(IntEnum):
    EXPENSE = 0
    INCOME = 1
    TRANSFER = 2
    NOT_COUNTED = 3
    LOAN_COLLECTION = 4
    DEBT_PRINCIPAL = 6
    LOAN_PRINCIPAL = 7
    DEBT_REPAYMENT = 9
    DEBT_INTEREST = 10
    LOAN_INTEREST = 11


class LoanType:
    DEBT = 51
    LOAN = 52


# Upstream error codes returned in the `ec` field of response bodies.
EC_SIGNATURE_MISMATCH = 400404
EC_AUTH_EXPIRED = 8888


class BillStatus(IntEnum):
    DELETED = 0
    NORMAL = 1
    NOT_SYNCED = 2


class AssetStatus(IntEnum):
    NORMAL = 0
    FINISHED = 1
    HIDDEN = 2


class CategoryType(IntEnum):
    EXPENSE = 0
    INCOME = 1


DEFAULT_TIMEOUT = float(os.environ.get("QIANJI_TIMEOUT", "30.0"))
DEFAULT_MAX_CONNECTIONS = int(os.environ.get("QIANJI_MAX_CONNECTIONS", "20"))
DEFAULT_MAX_KEEPALIVE = int(os.environ.get("QIANJI_MAX_KEEPALIVE", "10"))

# Soft TTL (seconds) for the local bill cache: an incremental sync is auto-triggered
# when a query finds the cache older than this. 0 disables TTL (trust cache until an
# explicit sync). Per-account overrides live in the account_settings table.
DEFAULT_BILL_CACHE_TTL = int(os.environ.get("QIANJI_BILL_CACHE_TTL", "300"))
