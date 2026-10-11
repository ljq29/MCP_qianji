from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from inspect import Parameter, Signature
from typing import Any

from pydantic import BaseModel, Field, create_model

_MISSING = object()
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")


@dataclass(frozen=True)
class ParamSpec:
    name: str
    annotation: type = str
    required: bool = False
    default: Any = None
    auto: bool = False
    aliases: tuple[str, ...] = ()
    hash_if_alias: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class EndpointSpec:
    ctrl: str
    act: str
    params: tuple[ParamSpec, ...] = ()
    public: bool = False
    description: str = ""
    aliases: tuple[str, ...] = ()

    @property
    def path(self) -> str:
        return f"/{self.ctrl}/{self.act}"

    @property
    def method_name(self) -> str:
        return f"{self.ctrl}_{self.act}".replace("-", "_")

    @property
    def tool_name(self) -> str:
        return self.method_name


def uid() -> ParamSpec:
    return ParamSpec(
        "uid", str, default=None, auto=True, description="User ID; omitted values are filled from the active session."
    )


def p(
    name: str,
    annotation: type = str,
    default: Any = _MISSING,
    *,
    aliases: tuple[str, ...] = (),
    hash_if_alias: tuple[str, ...] = (),
    description: str = "",
) -> ParamSpec:
    if default is _MISSING:
        return ParamSpec(
            name, annotation, required=True, aliases=aliases, hash_if_alias=hash_if_alias, description=description
        )
    return ParamSpec(
        name, annotation, default=default, aliases=aliases, hash_if_alias=hash_if_alias, description=description
    )


def opt(name: str, annotation: type = str, default: Any = None, **kwargs: Any) -> ParamSpec:
    return p(name, annotation, default, **kwargs)


ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec(
        "account",
        "login",
        (p("v", str, aliases=("email",)), p("pwd", str, aliases=("password",), hash_if_alias=("password",))),
        public=True,
        description="Stateless raw login (does NOT persist token; use client.login() to save the session)",
    ),
    EndpointSpec(
        "account",
        "logout",
        (uid(),),
        description="Stateless raw logout (does NOT clear local session; use client.logout())",
    ),
    EndpointSpec(
        "account",
        "register",
        (
            p("v", str, aliases=("email", "phone")),
            p("pwd", str, aliases=("password",), hash_if_alias=("password",)),
            p("code"),
            opt("name"),
            opt("invitecode"),
        ),
        public=True,
        description="Register",
    ),
    EndpointSpec(
        "account",
        "refreshtoken",
        (uid(),),
        description="Stateless raw token refresh (does NOT persist; client._request auto-refreshes on expiry)",
        aliases=("account_refresh_token",),
    ),
    EndpointSpec(
        "account",
        "getverifycode",
        (p("v", str, aliases=("account",)), p("t", int, 2, aliases=("code_type",))),
        public=True,
        description="Get verification code",
        aliases=("account_get_verify_code",),
    ),
    EndpointSpec(
        "account",
        "setpwd",
        (p("account"), p("code"), p("pwd", str, aliases=("new_password",), hash_if_alias=("new_password",))),
        public=True,
        description="Set password",
        aliases=("account_set_password",),
    ),
    EndpointSpec(
        "account",
        "bindaccount",
        (uid(), p("v"), p("pwd", str, aliases=("password",), hash_if_alias=("password",)), p("code")),
        description="Bind email or phone",
    ),
    EndpointSpec("account", "bindwx", (uid(), p("code")), description="Bind WeChat"),
    EndpointSpec("account", "unbind", (uid(), p("t", int)), description="Unbind third-party account"),
    EndpointSpec("account", "loginwx", (p("code"),), public=True, description="WeChat login"),
    EndpointSpec(
        "account", "bind", (p("t", int), p("openid"), p("token")), public=True, description="Third-party login or bind"
    ),
    EndpointSpec("account", "cancelaccount", (uid(), opt("reason")), description="Cancel account"),
    EndpointSpec(
        "account", "cancelthirdaccount", (uid(), p("t", int), opt("reason")), description="Cancel third-party account"
    ),
    EndpointSpec("account", "loginrecords2", (uid(),), description="Login records"),
    EndpointSpec("account", "removelogin", (uid(), p("t", int), p("devid")), description="Remove login record"),
    EndpointSpec("bill", "syncall", (uid(), p("v", str)), description="Sync bills"),
    EndpointSpec(
        "bill",
        "refund2",
        (uid(), p("did", int, aliases=("bill_id",)), p("v", str, aliases=("refund_data",))),
        description="Refund bill",
        aliases=("bill_refund",),
    ),
    # App wires `billIds` as a JSONArray string ("[1,2]"), not `ids`
    # (jadx network/api/bill/e.java); `ids` stays as the friendly alias.
    EndpointSpec("billbatch", "deletev2", (uid(), p("billIds", str, aliases=("ids",))), description="Batch delete bills"),
    EndpointSpec("billbatch", "modify", (uid(), p("billIds", str, aliases=("ids",)), p("values", str, aliases=("v",))), description="Batch modify bills"),
    EndpointSpec(
        "syncv2",
        "pull",
        (
            uid(),
            p("bookid", int, -1, aliases=("book_id",)),
            p("lasttimes", str, aliases=("last_times",)),
            p("pageoffset", int, 0, aliases=("page_offset",)),
            opt("pagesign", str, "", aliases=("page_sign",)),
        ),
        description="Pull incremental data",
        aliases=("sync_pull_raw",),
    ),
    EndpointSpec("asset", "list", (uid(), p("status", int, 0)), description="List assets"),
    EndpointSpec("asset", "info", (uid(), p("did", int, aliases=("asset_id",))), description="Asset detail"),
    EndpointSpec(
        "asset",
        "submit",
        (
            uid(),
            p("v", str, aliases=("asset_data",)),
            opt("diffparams", str, aliases=("diff_params",)),
            opt("type", str, aliases=("extra", "asset_extra")),
        ),
        description="Create or update asset",
    ),
    EndpointSpec("asset", "delete", (uid(), p("did", int, aliases=("asset_id",))), description="Delete asset"),
    EndpointSpec(
        "asset",
        "deletecheck",
        (uid(), p("did", int, aliases=("asset_id",))),
        description="Check before deleting asset",
        aliases=("asset_delete_check",),
    ),
    EndpointSpec("asset", "typelist", (), description="Asset type list", aliases=("asset_type_list",)),
    EndpointSpec("asset", "banklist", (), description="Bank list", aliases=("asset_bank_list",)),
    EndpointSpec(
        "asset",
        "listloan",
        (uid(), p("status", int, 0), p("t", int, aliases=("loan_type", "loantype"))),
        description="Loan asset list",
    ),
    EndpointSpec("asset", "reorder", (uid(), p("v", str, aliases=("asset_ids",))), description="Reorder assets"),
    EndpointSpec(
        "asset", "visible", (uid(), p("did", int, aliases=("asset_id",))), description="Toggle asset visibility"
    ),
    EndpointSpec(
        "asset", "finish", (uid(), p("did", int, aliases=("asset_id",)), opt("v", str)), description="Finish asset"
    ),
    EndpointSpec("asset_group", "list", (uid(),), description="List asset groups"),
    EndpointSpec(
        "asset_group",
        "submit",
        (uid(), opt("groupid", str, "", aliases=("group_id",)), p("name")),
        description="Create or update asset group",
    ),
    EndpointSpec(
        "asset_group", "delete", (uid(), p("groupid", str, aliases=("group_id",))), description="Delete asset group"
    ),
    EndpointSpec(
        "asset_group",
        "addasset",
        (uid(), p("groupid", str, aliases=("group_id",)), p("assetids", str, aliases=("asset_ids",)), opt("sort", int)),
        description="Add assets to group",
    ),
    EndpointSpec(
        "asset_group",
        "removeasset",
        (uid(), p("did", int, aliases=("asset_id",))),
        description="Remove asset from group",
    ),
    EndpointSpec("asset_group", "reorder", (uid(), p("v", str)), description="Reorder asset groups"),
    EndpointSpec(
        "assetline",
        "list",
        (
            uid(),
            p("did", int, aliases=("asset_id",)),
            opt("startdate", str, aliases=("start_date",)),
            opt("enddate", str, aliases=("end_date",)),
        ),
        description="List asset snapshots",
    ),
    EndpointSpec("assetline", "submit", (uid(), p("v", str)), description="Submit asset snapshot"),
    EndpointSpec("assetline", "init", (uid(),), description="Initialize asset line"),
    EndpointSpec("assetline", "reset", (uid(), p("v", str)), description="Reset asset line"),
    EndpointSpec(
        "category",
        "listv2",
        (
            uid(),
            p("bookid", int, -1, aliases=("book_id",)),
            opt("t", int, aliases=("cat_type",)),
            opt("showhide", bool, aliases=("show_hidden",)),
        ),
        description="List categories",
        aliases=("category_list_raw",),
    ),
    EndpointSpec(
        "category",
        "submitv2",
        (
            uid(),
            p("bookid", int, -1, aliases=("book_id",)),
            p("v", str, aliases=("name",)),
            p("t", int, aliases=("cat_type",)),
            opt("did", int, aliases=("cateid", "category_id")),
            opt("parentid", int, aliases=("parent_id",)),
            opt("icon"),
        ),
        description="Create or update category",
    ),
    EndpointSpec(
        "category",
        "submitmultiple",
        (
            uid(),
            p("bookid", int, -1, aliases=("book_id",)),
            p("parentid", int, aliases=("parent_id",)),
            p("t", int, aliases=("cat_type",)),
            p("v", str),
        ),
        description="Create multiple categories",
    ),
    EndpointSpec(
        "category",
        "deletev2",
        (
            uid(),
            p("did", int, aliases=("cateid", "category_id")),
            p("deletebills", bool, False, aliases=("delete_bills",)),
            opt("newcateid", int, aliases=("new_cateid",)),
        ),
        description="Delete category",
    ),
    EndpointSpec(
        "category",
        "deletecheckv2",
        (uid(), p("did", int, aliases=("cateid", "category_id"))),
        description="Check before deleting category",
    ),
    EndpointSpec("category", "iconlist", (), description="Category icon list", aliases=("category_icon_list",)),
    EndpointSpec("category", "reorder3", (uid(), p("v", str)), description="Reorder categories"),
    EndpointSpec(
        "category",
        "changelevel",
        (uid(), p("did", int, aliases=("cateid", "category_id")), opt("parentid", int, aliases=("parent_id",))),
        description="Change category level",
    ),
    EndpointSpec(
        "category",
        "movesubcate",
        (uid(), p("did", int, aliases=("cateid", "category_id")), p("parentid", int, aliases=("parent_id",))),
        description="Move subcategory",
    ),
    EndpointSpec("book", "list", (uid(), opt("t", int, aliases=("book_type",))), description="List books"),
    EndpointSpec("book", "submit", (uid(), p("v", str, aliases=("book_data",))), description="Create or update book"),
    EndpointSpec("book", "delete", (uid(), p("bookid", int, aliases=("book_id",))), description="Delete book"),
    EndpointSpec(
        "book", "deletecheck", (uid(), p("bookid", int, aliases=("book_id",))), description="Check before deleting book"
    ),
    EndpointSpec("book", "copy", (uid(), p("bookid", int, aliases=("book_id",))), description="Copy book"),
    EndpointSpec("book", "members", (uid(), p("bookid", int, aliases=("book_id",))), description="Book members"),
    EndpointSpec(
        "book", "invitecode", (uid(), p("bookid", int, aliases=("book_id",))), description="Generate invite code"
    ),
    EndpointSpec("book", "addbycode", (uid(), p("code")), description="Join book by code"),
    EndpointSpec("book", "infobycode", (uid(), p("code")), description="Book info by invite code"),
    EndpointSpec(
        "book",
        "quit",
        (uid(), p("bookid", int, aliases=("book_id",)), opt("memberid", str, aliases=("member_id",))),
        description="Quit shared book",
    ),
    EndpointSpec("book", "reorder", (uid(), p("v", str)), description="Reorder books"),
    EndpointSpec(
        "book", "config", (uid(), p("bookid", int, aliases=("book_id",)), p("v", str)), description="Save book config"
    ),
    EndpointSpec(
        "book", "visible", (uid(), p("bookid", int, aliases=("book_id",))), description="Toggle book visibility"
    ),
    EndpointSpec("book", "typelist", (uid(),), description="Book type list", aliases=("book_type_list",)),
    EndpointSpec(
        "tag",
        "list",
        (uid(), p("status", int, -1), opt("bookid", int, aliases=("book_id",))),
        description="List tags",
    ),
    EndpointSpec(
        "tag",
        "syncall",
        (uid(), p("lasttime", int, 0, aliases=("last_time",)), p("page", int, 0)),
        description="Sync tags",
        aliases=("tag_sync",),
    ),
    EndpointSpec(
        "tag",
        "submittag",
        (
            uid(),
            opt("did", str, "", aliases=("tagid", "tag_id")),
            p("v", str, aliases=("tag_data",)),
        ),
        description="Create or update tag",
        aliases=("tag_submit",),
    ),
    EndpointSpec(
        "tag",
        "deletetag",
        (uid(), p("tagid", str, aliases=("tag_id",))),
        description="Delete tag",
        aliases=("tag_delete",),
    ),
    EndpointSpec("tag", "archivetag", (uid(), p("tagid", str, aliases=("tag_id",))), description="Archive tag"),
    EndpointSpec(
        "tag",
        "submitgroup",
        (uid(), opt("groupid", str, "", aliases=("group_id",)), p("name")),
        description="Create or update tag group",
        aliases=("tag_submit_group",),
    ),
    EndpointSpec(
        "tag",
        "deletegroup",
        (uid(), p("groupid", str, aliases=("group_id",))),
        description="Delete tag group",
        aliases=("tag_delete_group",),
    ),
    EndpointSpec("tag", "reordertag", (uid(), p("v", str)), description="Reorder tags"),
    EndpointSpec("tag", "reordergroup", (uid(), p("v", str)), description="Reorder tag groups"),
    EndpointSpec(
        "budget",
        "list",
        (
            uid(),
            p("bookid", int, -1, aliases=("book_id",)),
            opt("startdate", str, aliases=("start_date",)),
            opt("enddate", str, aliases=("end_date",)),
            opt("budgettype", str, aliases=("budget_type",)),
        ),
        description="List budgets",
    ),
    EndpointSpec(
        "budget",
        "submitv2",
        (
            uid(),
            p("bookid", int, -1, aliases=("book_id",)),
            p("v", str, aliases=("budgets",)),
            opt("startdate", str, aliases=("start_date",)),
            opt("enddate", str, aliases=("end_date",)),
            opt("budgettype", str, aliases=("budget_type",)),
        ),
        description="Create or update budgets",
    ),
    EndpointSpec("usercard", "listv2", (uid(),), description="List user cards"),
    EndpointSpec(
        "usercard", "submit", (uid(), p("v", str, aliases=("card_data",))), description="Create or update user card"
    ),
    EndpointSpec("usercard", "delete", (uid(), p("did", int, aliases=("card_id",))), description="Delete user card"),
    EndpointSpec("usercard", "reorder", (uid(), p("v", str)), description="Reorder user cards"),
    EndpointSpec("currency", "listv2", (uid(),), description="List currencies", aliases=("currency_list",)),
    EndpointSpec("saving", "list", (uid(),), description="List saving plans"),
    EndpointSpec(
        "saving", "submit", (uid(), p("v", str, aliases=("plan_data",))), description="Create or update saving plan"
    ),
    EndpointSpec(
        "saving",
        "delete",
        (uid(), p("planid", str, aliases=("plan_id",)), opt("deletebill", bool, False, aliases=("delete_bill",))),
        description="Delete saving plan",
    ),
    EndpointSpec(
        "saving",
        "deposit",
        (
            uid(),
            p("planid", str, aliases=("plan_id",)),
            p("money", float),
            opt("date", str),
            opt("billid", int, aliases=("bill_id",)),
        ),
        description="Saving deposit",
    ),
    EndpointSpec(
        "saving",
        "canceldeposit",
        (uid(), p("depositid", str, aliases=("deposit_id",))),
        description="Cancel saving deposit",
    ),
    EndpointSpec(
        "installment",
        "list",
        (uid(), opt("flts", str), opt("bookid", int, aliases=("book_id",))),
        description="List installments",
    ),
    EndpointSpec("installment", "submit", (uid(), p("v", str)), description="Create or update installment"),
    EndpointSpec(
        "installment", "delete", (uid(), p("did", int, aliases=("installment_id",))), description="Delete installment"
    ),
    EndpointSpec(
        "installment",
        "config",
        (uid(), p("did", int, aliases=("installment_id",)), p("v", str)),
        description="Update installment config",
    ),
    EndpointSpec(
        "installment",
        "bindbill",
        (uid(), p("did", int, aliases=("installment_id",)), p("billid", int, aliases=("bill_id",))),
        description="Bind bill to installment",
    ),
    EndpointSpec("installment", "prepay", (uid(), p("v", str)), description="Installment prepay"),
    EndpointSpec("repeat_task", "list", (uid(), opt("status", int)), description="List repeat tasks"),
    EndpointSpec("repeat_task", "submit", (uid(), p("v", str)), description="Create or update repeat task"),
    EndpointSpec(
        "repeat_task", "delete", (uid(), p("did", int, aliases=("task_id",))), description="Delete repeat task"
    ),
    EndpointSpec(
        "repeat_task", "toggle", (uid(), p("did", int, aliases=("task_id",))), description="Toggle repeat task"
    ),
    EndpointSpec(
        "repeat_task",
        "bindbill",
        (uid(), p("did", int, aliases=("task_id",)), p("billid", int, aliases=("bill_id",))),
        description="Bind bill to repeat task",
    ),
    EndpointSpec(
        "repeat_task", "resetcount", (uid(), p("did", int, aliases=("task_id",))), description="Reset repeat task count"
    ),
    EndpointSpec("repeat_task", "holidays", (p("v", str),), description="Get holiday data"),
    EndpointSpec(
        "baoxiao",
        "baoxiao",
        (
            uid(),
            p("v", str, aliases=("reimbursement_data",)),
            opt("did", int, aliases=("reimbursement_bill_id",)),
            opt("bxtime", int, aliases=("baoxiao_time", "reimbursement_time")),
            opt("remark", str),
            opt("images", str),
            opt("tags", str),
        ),
        description="Mark bill reimbursed",
    ),
    EndpointSpec("baoxiao", "cancelbaoxiao", (uid(), p("v", str)), description="Cancel reimbursement"),
    EndpointSpec("baoxiao", "upgradev2", (), description="Upgrade reimbursement data"),
    EndpointSpec("import", "platforms", (), description="Import platforms"),
    EndpointSpec(
        "import",
        "submitfile",
        (
            uid(),
            p("filename"),
            p("bookid", int, aliases=("book_id",)),
            p("platformid", int, aliases=("platform_id",)),
            opt("fileid", int, aliases=("file_id",)),
            opt("v", str),
        ),
        description="Submit import file",
    ),
    EndpointSpec("import", "packs", (), description="Import packs"),
    EndpointSpec(
        "import",
        "packbills",
        (p("page", int), p("pagesize", int, aliases=("page_size",))),
        description="Import pack bills",
    ),
    EndpointSpec(
        "import",
        "confirmresult",
        (p("packid", int, aliases=("pack_id",)), p("confirm", bool), opt("merge", bool)),
        description="Confirm import result",
    ),
    EndpointSpec("import", "deletepack", (p("packid", int, aliases=("pack_id",)),), description="Delete import pack"),
    EndpointSpec("user", "update", (uid(), p("v", str, aliases=("info",))), description="Update user info"),
    EndpointSpec("user", "updateconfig", (uid(), p("v", str, aliases=("config",))), description="Update user config"),
    EndpointSpec("user", "getaddress", (uid(),), description="Get user address"),
    EndpointSpec(
        "user", "submitaddress", (uid(), p("name"), p("phone"), p("address")), description="Submit user address"
    ),
    EndpointSpec("client", "checkfeedback2", (uid(),), description="Check feedback"),
)


ENDPOINT_BY_PATH = {endpoint.path: endpoint for endpoint in ENDPOINTS}
ENDPOINT_BY_METHOD = {endpoint.method_name: endpoint for endpoint in ENDPOINTS}
for _endpoint in ENDPOINTS:
    for _alias in _endpoint.aliases:
        ENDPOINT_BY_METHOD[_alias] = _endpoint


def _to_form_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return value


def _normalize_password(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if _MD5_RE.fullmatch(value):
        return value.lower()
    from qianji.sign import hash_password

    return hash_password(value)


def _should_include(spec: ParamSpec, value: Any) -> bool:
    if value is None:
        return False
    if value == "" and not spec.required:
        return False
    return True


def endpoint_signature(
    endpoint: EndpointSpec, *, include_account_id: bool = False, return_annotation: Any = dict
) -> Signature:
    parameters = []
    for spec in endpoint.params:
        if spec.required:
            default = Parameter.empty
        else:
            default = spec.default
        parameters.append(
            Parameter(
                spec.name,
                Parameter.KEYWORD_ONLY,
                default=default,
                annotation=spec.annotation,
            )
        )
    if include_account_id:
        parameters.append(
            Parameter(
                "account_id",
                Parameter.KEYWORD_ONLY,
                default="",
                annotation=str,
            )
        )
    return Signature(parameters, return_annotation=return_annotation)


def endpoint_request_model(endpoint: EndpointSpec) -> type[BaseModel]:
    fields: dict[str, tuple[Any, Any]] = {}
    for spec in endpoint.params:
        if spec.required:
            fields[spec.name] = (spec.annotation, Field(description=spec.description))
        else:
            fields[spec.name] = (spec.annotation | None, Field(default=None, description=spec.description))
    return create_model(f"{endpoint.ctrl.title().replace('_', '')}{endpoint.act.title()}Request", **fields)  # type: ignore[call-overload]


def params_from_model(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude_none=True)


def prepare_params(endpoint: EndpointSpec, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    values = dict(kwargs)
    params: dict[str, Any] = {}

    positional_specs = [spec for spec in endpoint.params if not spec.auto]
    if len(args) > len(positional_specs):
        raise TypeError(f"{endpoint.method_name} expected at most {len(positional_specs)} positional arguments")
    for spec, value in zip(positional_specs, args, strict=False):
        if spec.name in values:
            raise TypeError(f"{endpoint.method_name} got multiple values for argument '{spec.name}'")
        values[spec.name] = value

    for spec in endpoint.params:
        value = values.pop(spec.name, _MISSING)
        used_alias = ""
        if value is _MISSING:
            for alias in spec.aliases:
                if alias in values:
                    value = values.pop(alias)
                    used_alias = alias
                    break
        if value is _MISSING:
            if spec.required:
                raise TypeError(f"{endpoint.method_name} missing required argument '{spec.name}'")
            value = spec.default
        if used_alias in spec.hash_if_alias:
            value = _normalize_password(value)
        elif spec.name == "pwd":
            value = _normalize_password(value)
        if _should_include(spec, value):
            params[spec.name] = _to_form_value(value)

    if values:
        unknown = ", ".join(sorted(values))
        raise TypeError(f"{endpoint.method_name} got unexpected argument(s): {unknown}")
    return params


async def call_endpoint(client: Any, endpoint: EndpointSpec, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict:
    params = prepare_params(endpoint, args, kwargs)
    if endpoint.public:
        return await client._request_public(endpoint.ctrl, endpoint.act, params)
    return await client._request(endpoint.ctrl, endpoint.act, params)


def make_endpoint_method(endpoint: EndpointSpec) -> Callable[..., Any]:
    async def endpoint_method(self: Any, *args: Any, **kwargs: Any) -> dict:
        return await call_endpoint(self, endpoint, args, kwargs)

    endpoint_method.__name__ = endpoint.method_name
    endpoint_method.__qualname__ = endpoint.method_name
    endpoint_method.__doc__ = f"{endpoint.description or endpoint.path} ({endpoint.path})."
    endpoint_method.__signature__ = endpoint_signature(endpoint)  # type: ignore[attr-defined]
    return endpoint_method


def _is_handwritten(cls: type, name: str) -> bool:
    current = getattr(cls, name, None)
    if current is None:
        return False
    # Generated methods set a dotless __qualname__; handwritten mixin methods
    # keep their defining class prefix (e.g. "BillAPI.bill_create").
    return "." in getattr(current, "__qualname__", "")


def install_endpoint_methods(cls: type) -> None:
    for endpoint in ENDPOINTS:
        method = make_endpoint_method(endpoint)
        for name in (endpoint.method_name, *endpoint.aliases):
            if _is_handwritten(cls, name):
                continue
            setattr(cls, name, method)
