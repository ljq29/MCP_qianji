from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from qianji.bill_id import parse_bill_id as _bill_id
from qianji.bill_id import parse_positive_int as _positive_int
from qianji.bill_id import stringify_known_bill_ids as _stringify_known_bill_ids
from qianji.bill_ops import BILL_CACHE_TTL_KEY, resolve_cache_ttl, update_bill_by_id
from qianji.bill_ops import get_bill_by_id as _get_bill_by_id
from qianji.image_upload import validate_bill_image_keys
from qianji.tags import normalize_tag_names, resolve_tag_names


class WriteService:
    def __init__(self, client: Any):
        self.client = client

    async def create_expense(
        self,
        *,
        amount: float,
        occurred_at: str,
        asset_id: int,
        category_id: int,
        remark: str = "",
        book_id: int = -1,
        tags: list[str] | None = None,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self._create_transaction(
            command="create_expense",
            bill_type=0,
            amount=amount,
            occurred_at=occurred_at,
            asset_id=asset_id,
            category_id=category_id,
            remark=remark,
            book_id=book_id,
            tags=tags,
            images=images,
        )

    async def create_income(
        self,
        *,
        amount: float,
        occurred_at: str,
        asset_id: int,
        category_id: int,
        remark: str = "",
        book_id: int = -1,
        tags: list[str] | None = None,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self._create_transaction(
            command="create_income",
            bill_type=1,
            amount=amount,
            occurred_at=occurred_at,
            asset_id=asset_id,
            category_id=category_id,
            remark=remark,
            book_id=book_id,
            tags=tags,
            images=images,
        )

    async def create_transfer(
        self,
        *,
        amount: float,
        occurred_at: str,
        from_asset_id: int,
        to_asset_id: int,
        remark: str = "",
        book_id: int = -1,
        tags: list[str] | None = None,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_amount = _positive_amount(amount)
        bill_time = _parse_occurred_at(occurred_at)
        normalized_from = _positive_int(from_asset_id, "from_asset_id")
        normalized_to = _positive_int(to_asset_id, "to_asset_id")
        if normalized_from == normalized_to:
            raise ValueError("from_asset_id and to_asset_id must be different")
        await self._validate_asset_ids({"from_asset_id": normalized_from, "to_asset_id": normalized_to})
        tag_names, tag_ids = await _resolve_tags(self.client, tags)
        image_keys = validate_bill_image_keys(images)

        kwargs: dict[str, Any] = {
            "money": normalized_amount,
            "bill_type": 2,
            "cateid": 0,
            "assetid": normalized_from,
            "remark": remark,
            "book_id": int(book_id),
            "bill_time": bill_time,
            "target_asset_id": normalized_to,
            "tags": tag_ids or None,
        }
        if image_keys is not None:
            kwargs["images"] = image_keys
        raw = await self.client.bill_create(**kwargs)
        return _created_bill_result(
            "create_transfer",
            raw,
            {
                "bill_type": "transfer",
                "amount": normalized_amount,
                "occurred_at": occurred_at,
                "book_id": int(book_id),
                "from_asset_id": normalized_from,
                "to_asset_id": normalized_to,
                "remark": remark,
                "tags": tag_names,
                "tag_ids": tag_ids,
                **({"images": image_keys} if image_keys is not None else {}),
            },
        )

    async def update_expense(self, *, bill_id: Any, **updates: Any) -> dict[str, Any]:
        return await self._update_transaction(
            command="update_expense",
            bill_id=bill_id,
            expected_type=0,
            **updates,
        )

    async def update_income(self, *, bill_id: Any, **updates: Any) -> dict[str, Any]:
        return await self._update_transaction(
            command="update_income",
            bill_id=bill_id,
            expected_type=1,
            **updates,
        )

    async def update_transfer(self, *, bill_id: Any, **updates: Any) -> dict[str, Any]:
        return await self._update_transaction(
            command="update_transfer",
            bill_id=bill_id,
            expected_type=2,
            **updates,
        )

    async def delete_bill(self, bill_id: Any) -> dict[str, Any]:
        normalized_id = _bill_id(bill_id)
        raw = await self.client.bill_delete([normalized_id])
        ids: dict[str, Any] = {"bill_id": str(normalized_id)}
        sync = _bill_sync(raw)
        if _sync_failed(sync):
            return _error_result(
                "delete_bill", "bill", "upstream_not_confirmed", "Qianji failed to delete bill", ids, raw
            )
        del_ids = sync.get("del_ids") if isinstance(sync, dict) else None
        if isinstance(del_ids, list) and str(normalized_id) not in {str(item) for item in del_ids}:
            return _error_result(
                "delete_bill",
                "bill",
                "upstream_not_confirmed",
                f"Qianji did not confirm deletion for bill: {normalized_id}",
                ids,
                raw,
            )
        mgr = getattr(self.client, "_session_mgr", None)
        account_id = getattr(self.client, "current_account", None)
        if mgr is not None and account_id:
            await mgr.cache_delete_bills(account_id, [normalized_id])
        if isinstance(del_ids, list):
            ids["del_ids"] = [str(item) for item in del_ids]
        return _success_result("delete_bill", "bill", ids=ids, data={"deleted": True}, raw_result=raw)

    async def create_refund(
        self,
        *,
        source_bill_id: Any,
        amount: float,
        occurred_at: str,
        asset_id: int | None = None,
        remark: str = "",
        currency: dict[str, Any] | None = None,
        images: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_source = _bill_id(source_bill_id, "source_bill_id")
        normalized_amount = _positive_amount(amount)
        tag_names, tag_ids = await _resolve_tags(self.client, tags)
        image_keys = validate_bill_image_keys(images)
        raw = await self.client.create_refund(
            source_bill_id=normalized_source,
            amount=normalized_amount,
            occurred_at=_parse_occurred_at(occurred_at),
            asset_id=asset_id,
            remark=remark,
            currency=currency,
            images=image_keys,
            tags=tag_ids or None,
        )
        ids = {"source_bill_id": str(normalized_source)}
        refund_id = _first_present(raw, ("refund_bill_id", "bill_id"))
        if refund_id is not None:
            ids["refund_bill_id"] = str(refund_id)
        return _success_result(
            "create_refund",
            "refund",
            ids=ids,
            data={
                "amount": normalized_amount,
                "occurred_at": occurred_at,
                "asset_id": asset_id,
                "remark": remark,
                "tags": tag_names,
                "tag_ids": tag_ids,
                **({"images": image_keys} if image_keys is not None else {}),
            },
            raw_result=raw,
        )

    async def update_refund(self, *, source_bill_id: Any, refund_bill_id: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._submit_refund_like("update_refund", source_bill_id, refund_bill_id, **kwargs)

    async def create_reimbursement(
        self,
        *,
        source_bill_ids: list[Any],
        amount: float,
        occurred_at: str,
        asset_id: int | None = None,
        remark: str = "",
        currency: dict[str, Any] | None = None,
        images: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_sources = [_bill_id(source_bill_id, "source_bill_ids") for source_bill_id in source_bill_ids]
        normalized_amount = _positive_amount(amount)
        tag_names, tag_ids = await _resolve_tags(self.client, tags)
        image_keys = validate_bill_image_keys(images)
        raw = await self.client.create_reimbursement(
            source_bill_ids=normalized_sources,
            amount=normalized_amount,
            occurred_at=_parse_occurred_at(occurred_at),
            asset_id=asset_id,
            remark=remark,
            currency=currency,
            images=image_keys,
            tags=tag_ids or None,
        )
        ids: dict[str, Any] = {"source_bill_ids": [str(source_bill_id) for source_bill_id in normalized_sources]}
        reimbursement_id = _first_present(raw, ("reimbursement_bill_id", "bill_id"))
        if reimbursement_id is not None:
            ids["reimbursement_bill_id"] = str(reimbursement_id)
        return _success_result(
            "create_reimbursement",
            "reimbursement",
            ids=ids,
            data={
                "amount": normalized_amount,
                "occurred_at": occurred_at,
                "asset_id": asset_id,
                "remark": remark,
                "tags": tag_names,
                "tag_ids": tag_ids,
                **({"images": image_keys} if image_keys is not None else {}),
            },
            raw_result=raw,
        )

    async def update_reimbursement(self, *, reimbursement_bill_id: Any, source_bill_ids: list[Any], **kwargs: Any):
        normalized_id = _bill_id(reimbursement_bill_id, "reimbursement_bill_id")
        normalized_sources = [_bill_id(source_bill_id, "source_bill_ids") for source_bill_id in source_bill_ids]
        normalized_amount = _positive_amount(kwargs["amount"])
        tag_names, tag_ids = await _resolve_tags(self.client, kwargs.get("tags"))
        image_keys = validate_bill_image_keys(kwargs.get("images"))
        raw = await self.client.update_reimbursement(
            reimbursement_bill_id=normalized_id,
            source_bill_ids=normalized_sources,
            amount=normalized_amount,
            occurred_at=_parse_occurred_at(kwargs["occurred_at"]),
            asset_id=kwargs.get("asset_id"),
            remark=kwargs.get("remark", ""),
            currency=kwargs.get("currency"),
            images=image_keys,
            tags=tag_ids or None,
        )
        return _success_result(
            "update_reimbursement",
            "reimbursement",
            ids={
                "reimbursement_bill_id": str(normalized_id),
                "source_bill_ids": [str(source_bill_id) for source_bill_id in normalized_sources],
            },
            data={"amount": normalized_amount, "tags": tag_names, "tag_ids": tag_ids},
            raw_result=raw,
        )

    async def cancel_reimbursement(self, reimbursement_bill_ids: list[Any]) -> dict[str, Any]:
        normalized_ids = [_bill_id(bill_id, "reimbursement_bill_ids") for bill_id in reimbursement_bill_ids]
        raw = await self.client.cancel_reimbursement(normalized_ids)
        return _success_result(
            "cancel_reimbursement",
            "reimbursement",
            ids={"reimbursement_bill_ids": [str(bill_id) for bill_id in normalized_ids]},
            data={"cancelled": True},
            raw_result=raw,
        )

    async def create_loan_asset(self, **kwargs: Any) -> dict[str, Any]:
        return await self._asset_command("create_loan_asset", "loan_asset", self.client.create_loan_asset, **kwargs)

    async def update_loan_asset(self, *, asset_id: int, **kwargs: Any) -> dict[str, Any]:
        raw = await self.client.update_loan_asset(asset_id=asset_id, **_with_valid_images(kwargs))
        return _success_result(
            "update_loan_asset", "loan_asset", ids={"asset_id": asset_id}, data=_without_raw(kwargs), raw_result=raw
        )

    async def create_debt_asset(self, **kwargs: Any) -> dict[str, Any]:
        return await self._asset_command("create_debt_asset", "debt_asset", self.client.create_debt_asset, **kwargs)

    async def update_debt_asset(self, *, asset_id: int, **kwargs: Any) -> dict[str, Any]:
        raw = await self.client.update_debt_asset(asset_id=asset_id, **_with_valid_images(kwargs))
        return _success_result(
            "update_debt_asset", "debt_asset", ids={"asset_id": asset_id}, data=_without_raw(kwargs), raw_result=raw
        )

    async def update_asset(
        self, *, asset_id: int, name: str | None = None, remark: str | None = None
    ) -> dict[str, Any]:
        if name is None and remark is None:
            return _error_result(
                "update_asset",
                "asset",
                "no_update_fields",
                "At least one update field is required",
                {"asset_id": asset_id},
                {},
            )
        raw = await self.client.update_asset(asset_id, name=name, remark=remark)
        data = {key: value for key, value in {"name": name, "remark": remark}.items() if value is not None}
        return _success_result("update_asset", "asset", ids={"asset_id": asset_id}, data=data, raw_result=raw)

    async def add_loan_principal(self, **kwargs: Any) -> dict[str, Any]:
        return await self._debtloan_record("add_loan_principal", self.client.add_loan_principal, **kwargs)

    async def update_loan_principal(self, *, bill_id: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._debtloan_record(
            "update_loan_principal", self.client.update_loan_principal, bill_id=bill_id, **kwargs
        )

    async def add_debt_principal(self, **kwargs: Any) -> dict[str, Any]:
        return await self._debtloan_record("add_debt_principal", self.client.add_debt_principal, **kwargs)

    async def update_debt_principal(self, *, bill_id: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._debtloan_record(
            "update_debt_principal", self.client.update_debt_principal, bill_id=bill_id, **kwargs
        )

    async def record_loan_collection(self, **kwargs: Any) -> dict[str, Any]:
        return await self._debtloan_record("record_loan_collection", self.client.record_loan_collection, **kwargs)

    async def update_loan_collection(self, *, bill_id: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._debtloan_record(
            "update_loan_collection", self.client.update_loan_collection, bill_id=bill_id, **kwargs
        )

    async def record_debt_repayment(self, **kwargs: Any) -> dict[str, Any]:
        return await self._debtloan_record("record_debt_repayment", self.client.record_debt_repayment, **kwargs)

    async def update_debt_repayment(self, *, bill_id: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._debtloan_record(
            "update_debt_repayment", self.client.update_debt_repayment, bill_id=bill_id, **kwargs
        )

    async def create_tag(self, *, name: str, group_id: str = "", color: str | None = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"name": name, "group_id": group_id}
        if color is not None:
            kwargs["color"] = color
        raw = await self.client.tag_submit(**kwargs)
        tag_id = _created_id(raw)
        return _success_result(
            "create_tag", "tag", ids={"tag_id": tag_id} if tag_id else {}, data=kwargs, raw_result=raw
        )

    async def update_tag(
        self, *, tag_id: str, name: str, group_id: str | None = None, color: str | None = None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"tag_id": tag_id, "name": name}
        if group_id is not None:
            kwargs["group_id"] = group_id
        if color is not None:
            kwargs["color"] = color
        raw = await self.client.tag_submit(**kwargs)
        return _success_result("update_tag", "tag", ids={"tag_id": tag_id}, data=kwargs, raw_result=raw)

    async def delete_tag(self, *, tag_id: str) -> dict[str, Any]:
        raw = await self.client.tag_delete(tag_id=tag_id)
        return _success_result("delete_tag", "tag", ids={"tag_id": tag_id}, data={"deleted": True}, raw_result=raw)

    async def archive_tag(self, *, tag_id: str) -> dict[str, Any]:
        raw = await self.client.tag_archive(tag_id=tag_id)
        return _success_result("archive_tag", "tag", ids={"tag_id": tag_id}, data={"archived": True}, raw_result=raw)

    async def create_tag_group(self, *, name: str) -> dict[str, Any]:
        raw = await self.client.tag_submit_group(name=name)
        group_id = _created_id(raw)
        return _success_result(
            "create_tag_group",
            "tag_group",
            ids={"group_id": group_id} if group_id else {},
            data={"name": name},
            raw_result=raw,
        )

    async def update_tag_group(self, *, group_id: str, name: str) -> dict[str, Any]:
        raw = await self.client.tag_submit_group(group_id=group_id, name=name)
        return _success_result(
            "update_tag_group", "tag_group", ids={"group_id": group_id}, data={"name": name}, raw_result=raw
        )

    async def delete_tag_group(self, *, group_id: str) -> dict[str, Any]:
        raw = await self.client.tag_delete_group(group_id=group_id)
        return _success_result(
            "delete_tag_group", "tag_group", ids={"group_id": group_id}, data={"deleted": True}, raw_result=raw
        )

    async def reorder_tags(self, *, tag_ids: list[str]) -> dict[str, Any]:
        raw = await self.client.tag_reorder_tag(tag_ids=tag_ids)
        return _success_result(
            "reorder_tags", "tag", ids={"tag_ids": tag_ids}, data={"tag_ids": tag_ids}, raw_result=raw
        )

    async def reorder_tag_groups(self, *, group_ids: list[str]) -> dict[str, Any]:
        raw = await self.client.tag_reorder_group(group_ids=group_ids)
        return _success_result(
            "reorder_tag_groups",
            "tag_group",
            ids={"group_ids": group_ids},
            data={"group_ids": group_ids},
            raw_result=raw,
        )

    async def upload_bill_image(self, *, filename: str, content: bytes, mime_type: str) -> dict[str, Any]:
        raw = await self.client.upload_bill_image(filename=filename, content=content, mime_type=mime_type)
        return _success_result(
            "upload_bill_image",
            "bill_image",
            ids={},
            data={"image_key": raw.get("image_key"), "filename": filename, "mime_type": mime_type},
            raw_result=raw,
        )

    async def set_tags_enabled(self, enabled: bool) -> dict[str, Any]:
        raw = await self.client.set_tags_enabled(enabled)
        return _success_result(
            "set_tags_enabled",
            "account_setting",
            ids={},
            data={"enabled": bool(enabled)},
            raw_result=raw,
        )

    async def set_bill_cache_ttl(self, seconds: int) -> dict[str, Any]:
        aid = self.client.current_account
        normalized = int(seconds)
        if normalized < 0:
            await self.client._session_mgr.delete_account_setting(aid, BILL_CACHE_TTL_KEY)
            source = "env_default"
        else:
            await self.client._session_mgr.set_account_setting(aid, BILL_CACHE_TTL_KEY, str(normalized))
            source = "account_override"
        ttl = await resolve_cache_ttl(self.client)
        return _success_result(
            "set_bill_cache_ttl",
            "account_setting",
            ids={"account_id": aid},
            data={"ttl_seconds": ttl, "source": source},
            raw_result={"ok": True, "account_id": aid, "ttl_seconds": ttl, "source": source},
        )

    async def _create_transaction(
        self,
        *,
        command: str,
        bill_type: int,
        amount: float,
        occurred_at: str,
        asset_id: int,
        category_id: int,
        remark: str,
        book_id: int,
        tags: list[str] | None,
        images: list[str] | None,
    ) -> dict[str, Any]:
        normalized_amount = _positive_amount(amount)
        bill_time = _parse_occurred_at(occurred_at)
        normalized_asset = _positive_int(asset_id, "asset_id")
        normalized_category = _positive_int(category_id, "category_id")
        await self._validate_asset_ids({"asset_id": normalized_asset})
        await self._validate_category_id(normalized_category, bill_type, int(book_id))
        tag_names, tag_ids = await _resolve_tags(self.client, tags)
        image_keys = validate_bill_image_keys(images)
        kwargs: dict[str, Any] = {
            "money": normalized_amount,
            "bill_type": bill_type,
            "cateid": normalized_category,
            "assetid": normalized_asset,
            "remark": remark,
            "book_id": int(book_id),
            "bill_time": bill_time,
            "target_asset_id": -1,
            "tags": tag_ids or None,
        }
        if image_keys is not None:
            kwargs["images"] = image_keys
        raw = await self.client.bill_create(**kwargs)
        return _created_bill_result(
            command,
            raw,
            {
                "bill_type": "expense" if bill_type == 0 else "income",
                "amount": normalized_amount,
                "occurred_at": occurred_at,
                "book_id": int(book_id),
                "asset_id": normalized_asset,
                "category_id": normalized_category,
                "remark": remark,
                "tags": tag_names,
                "tag_ids": tag_ids,
                **({"images": image_keys} if image_keys is not None else {}),
            },
        )

    async def _update_transaction(
        self,
        *,
        command: str,
        bill_id: Any,
        expected_type: int,
        amount: float | None = None,
        occurred_at: str | None = None,
        asset_id: int | None = None,
        category_id: int | None = None,
        from_asset_id: int | None = None,
        to_asset_id: int | None = None,
        remark: str | None = None,
        book_id: int = -1,
        tags: list[str] | None = None,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_id = _bill_id(bill_id)
        original = await _get_bill_by_id(self.client, normalized_id, book_id=book_id, refresh=False)
        if original is None:
            return _error_result(
                command,
                "bill",
                "bill_not_found",
                f"Bill not found: {normalized_id}",
                {"bill_id": str(normalized_id)},
                {},
            )
        raw_value = original.get("raw")
        raw_bill: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
        original_type = _int_value(raw_bill.get("type", original.get("type_code")), -1)
        if original_type != expected_type:
            raise ValueError(f"bill type mismatch: expected type={expected_type}, got type={original_type}")

        patch: dict[str, Any] = {}
        data: dict[str, Any] = {"bill_type": {0: "expense", 1: "income", 2: "transfer"}[expected_type]}
        if amount is not None:
            patch["money"] = _positive_amount(amount)
            data["amount"] = patch["money"]
        if occurred_at is not None:
            patch["bill_time"] = _parse_occurred_at(occurred_at)
            data["occurred_at"] = occurred_at
        if expected_type in (0, 1):
            if asset_id is not None:
                patch["assetid"] = _positive_int(asset_id, "asset_id")
                await self._validate_asset_ids({"asset_id": patch["assetid"]})
                data["asset_id"] = patch["assetid"]
            if category_id is not None:
                patch["cateid"] = _positive_int(category_id, "category_id")
                await self._validate_category_id(patch["cateid"], expected_type, int(book_id))
                data["category_id"] = patch["cateid"]
        else:
            current_from = _int_value(raw_bill.get("assetid", raw_bill.get("fromid")), -1)
            current_to = _int_value(raw_bill.get("targetid"), -1)
            changed_assets: dict[str, int] = {}
            if from_asset_id is not None:
                current_from = _positive_int(from_asset_id, "from_asset_id")
                patch["assetid"] = current_from
                changed_assets["from_asset_id"] = current_from
                data["from_asset_id"] = current_from
            if to_asset_id is not None:
                current_to = _positive_int(to_asset_id, "to_asset_id")
                patch["target_asset_id"] = current_to
                changed_assets["to_asset_id"] = current_to
                data["to_asset_id"] = current_to
            if changed_assets:
                await self._validate_asset_ids(changed_assets)
            if current_from == current_to:
                raise ValueError("from_asset_id and to_asset_id must be different")
        if remark is not None:
            patch["remark"] = remark
            data["remark"] = remark
        if tags is not None:
            tag_names, tag_ids = await _resolve_tags(self.client, tags)
            patch["tags"] = tag_ids
            data["tags"] = tag_names
            data["tag_ids"] = tag_ids
        if images is not None:
            patch["images"] = validate_bill_image_keys(images) or []
            data["images"] = patch["images"]
        if not patch:
            return _error_result(
                command,
                "bill",
                "no_update_fields",
                "At least one update field is required",
                {"bill_id": str(normalized_id)},
                {},
            )

        result = await update_bill_by_id(self.client, normalized_id, patch, book_id=book_id)
        if result is None:
            return _error_result(
                command,
                "bill",
                "bill_not_found",
                f"Bill not found: {normalized_id}",
                {"bill_id": str(normalized_id)},
                {},
            )
        ids: dict[str, Any] = {"bill_id": str(normalized_id)}
        sync = _bill_sync(result)
        update_ids = sync.get("update_ids") if isinstance(sync, dict) else None
        if isinstance(update_ids, list):
            ids["update_ids"] = [str(item) for item in update_ids]
            if str(normalized_id) not in {str(item) for item in update_ids}:
                return _error_result(
                    command,
                    "bill",
                    "upstream_not_confirmed",
                    f"Qianji did not confirm update for bill: {normalized_id}",
                    ids,
                    result,
                )
        if _sync_failed(sync):
            return _error_result(command, "bill", "upstream_not_confirmed", "Qianji failed to update bill", ids, result)
        return _success_result(command, "bill", ids=ids, data=data, raw_result=result)

    async def _submit_refund_like(
        self, command: str, source_bill_id: Any, refund_bill_id: Any, **kwargs: Any
    ) -> dict[str, Any]:
        normalized_source = _bill_id(source_bill_id, "source_bill_id")
        normalized_refund = _bill_id(refund_bill_id, "refund_bill_id")
        normalized_amount = _positive_amount(kwargs["amount"])
        tag_names, tag_ids = await _resolve_tags(self.client, kwargs.get("tags"))
        image_keys = validate_bill_image_keys(kwargs.get("images"))
        raw = await self.client.update_refund(
            source_bill_id=normalized_source,
            refund_bill_id=normalized_refund,
            amount=normalized_amount,
            occurred_at=_parse_occurred_at(kwargs["occurred_at"]),
            asset_id=kwargs.get("asset_id"),
            remark=kwargs.get("remark", ""),
            currency=kwargs.get("currency"),
            images=image_keys,
            tags=tag_ids or None,
        )
        return _success_result(
            command,
            "refund",
            ids={"source_bill_id": str(normalized_source), "refund_bill_id": str(normalized_refund)},
            data={"amount": normalized_amount, "tags": tag_names, "tag_ids": tag_ids},
            raw_result=raw,
        )

    async def _asset_command(self, command: str, resource: str, method: Any, **kwargs: Any) -> dict[str, Any]:
        if "amount" in kwargs:
            kwargs["amount"] = _positive_amount(kwargs["amount"])
        raw = await method(**_with_valid_images(kwargs))
        asset_id = raw.get("asset_id")
        return _success_result(
            command,
            resource,
            ids={"asset_id": asset_id} if asset_id is not None else {},
            data=_without_raw(kwargs),
            raw_result=raw,
        )

    async def _debtloan_record(self, command: str, method: Any, **kwargs: Any) -> dict[str, Any]:
        call_kwargs = dict(kwargs)
        data = dict(kwargs)
        if "bill_id" in call_kwargs:
            call_kwargs["bill_id"] = _bill_id(call_kwargs["bill_id"])
        if "amount" in call_kwargs:
            call_kwargs["amount"] = _positive_amount(call_kwargs["amount"])
            data["amount"] = call_kwargs["amount"]
        if "occurred_at" in call_kwargs:
            call_kwargs["occurred_at"] = _parse_occurred_at(call_kwargs["occurred_at"])
        if "images" in call_kwargs:
            call_kwargs["images"] = validate_bill_image_keys(call_kwargs["images"])
            data["images"] = call_kwargs["images"]
        raw = await method(**call_kwargs)
        ids: dict[str, Any] = {}
        if "bill_id" in call_kwargs:
            ids["bill_id"] = str(call_kwargs["bill_id"])
        elif raw.get("bill_id") is not None:
            ids["bill_id"] = str(raw["bill_id"])
        if raw.get("interest_bill_id") is not None:
            ids["interest_bill_id"] = str(raw["interest_bill_id"])
        return _success_result(command, "bill", ids=ids, data=_without_raw(data), raw_result=raw)

    async def _validate_asset_ids(self, field_values: dict[str, int]) -> None:
        result = await self.client.asset_list()
        asset_ids = {
            record_id
            for record_id in (
                _record_id(record, ("id", "assetid")) for record in _data_records(result, "list", "update")
            )
            if record_id is not None
        }
        missing = [f"{field}={value}" for field, value in field_values.items() if value not in asset_ids]
        if missing:
            raise ValueError(f"Unknown asset id: {', '.join(missing)}")

    async def _validate_category_id(self, category_id: int, bill_type: int, book_id: int) -> None:
        result = await self.client.category_list(book_id=book_id, cat_type=bill_type)
        category_ids = {
            record_id
            for record_id in (
                _record_id(record, ("id", "cateid")) for record in _data_records(result, "list", "update")
            )
            if record_id is not None
        }
        if category_id not in category_ids:
            raise ValueError(f"Unknown category_id: {category_id}")


def _positive_amount(amount: Any) -> float:
    try:
        normalized = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("amount must be a positive number") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError("amount must be a positive number")
    return normalized


def _parse_occurred_at(occurred_at: str) -> int:
    raw = (occurred_at or "").strip()
    if not raw:
        raise ValueError("occurred_at must be timezone-aware ISO8601")
    iso_value = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError as exc:
        raise ValueError("occurred_at must be timezone-aware ISO8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("occurred_at must include timezone offset")
    return int(parsed.timestamp())


async def _resolve_tags(client: Any, tags: list[str] | None) -> tuple[list[str], list[str]]:
    tag_names = normalize_tag_names(tags)
    tag_ids = await resolve_tag_names(client, tags)
    return tag_names, tag_ids


def _created_bill_result(command: str, raw: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    sync = _bill_sync(raw)
    if _sync_failed(sync):
        return _error_result(command, "bill", "upstream_not_confirmed", "Qianji failed to create bill", {}, raw)
    new_ids = sync.get("new_ids", []) if isinstance(sync, dict) else []
    if not isinstance(new_ids, list) or not new_ids:
        return _error_result(command, "bill", "upstream_not_confirmed", "Qianji did not confirm new bill id", {}, raw)
    return _success_result(
        command,
        "bill",
        ids={"bill_id": str(new_ids[0]), "new_ids": [str(item) for item in new_ids]},
        data=data,
        raw_result=raw,
    )


def _success_result(
    command: str,
    resource: str,
    *,
    ids: dict[str, Any],
    data: dict[str, Any],
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "resource": resource,
        "ids": _stringify_known_bill_ids(ids),
        "data": _stringify_known_bill_ids(data),
        "raw_result": raw_result,
    }


def _error_result(
    command: str,
    resource: str,
    error_code: str,
    message: str,
    ids: dict[str, Any],
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "resource": resource,
        "error_code": error_code,
        "message": message,
        "ids": _stringify_known_bill_ids(ids),
        "raw_result": raw_result,
    }


def _bill_sync(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result, dict) else None
    sync_result = data.get("sync_result") if isinstance(data, dict) else None
    bill = sync_result.get("bill") if isinstance(sync_result, dict) else None
    return bill if isinstance(bill, dict) else {}


def _sync_failed(sync: dict[str, Any]) -> bool:
    return bool(sync.get("has_failed")) if isinstance(sync, dict) else False


def _first_present(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if isinstance(value, dict) and value.get(key) is not None:
            return value[key]
    return None


def _created_id(raw: dict[str, Any]) -> str | None:
    candidates: list[Any] = []
    if isinstance(raw, dict):
        candidates.extend([raw.get("id"), raw.get("tag_id"), raw.get("group_id")])
        data = raw.get("data")
        if isinstance(data, dict):
            candidates.extend([data.get("id"), data.get("tag_id"), data.get("group_id")])
            for key in ("tag", "group"):
                nested = data.get(key)
                if isinstance(nested, dict):
                    candidates.extend(
                        [nested.get("id"), nested.get("tagid"), nested.get("tagId"), nested.get("group_id")]
                    )
    for candidate in candidates:
        if candidate is not None:
            return str(candidate)
    return None


def _with_valid_images(kwargs: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(kwargs)
    if "images" in normalized:
        normalized["images"] = validate_bill_image_keys(normalized["images"])
    return normalized


def _without_raw(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "raw_result"}


def _record_id(record: dict[str, Any], keys: tuple[str, ...] = ("id",)) -> int | None:
    for key in keys:
        if key in record:
            try:
                return int(record[key])
            except (TypeError, ValueError):
                return None
    return None


def _walk_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            records.extend(_walk_records(item))
    elif isinstance(value, dict):
        if any(key in value for key in ("id", "tagid", "tagId", "cateid", "assetid")):
            records.append(value)
        for key in ("list", "update", "tags", "sublist", "children"):
            records.extend(_walk_records(value.get(key)))
    return records


def _data_records(result: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    data = result.get("data", {}) if isinstance(result, dict) else {}
    if not keys:
        return _walk_records(data)
    records: list[dict[str, Any]] = []
    for key in keys:
        records.extend(_walk_records(data.get(key)))
    return records


def _int_value(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
