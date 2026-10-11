from __future__ import annotations

from typing import Any

from qianji.errors import NotLoggedInError, QianjiError, TagFeatureDisabledError

TAG_ENABLED_CONFIG_KEY = "tagon"
TAG_ROOT_KEYS = ("list", "update", "groups", "tags")
TAG_CONTAINER_KEYS = (*TAG_ROOT_KEYS, "children", "sublist", "tagList")


def tags_flag_enabled(value: Any) -> bool:
    """Interpret the `tagon` user-config flag; True, 1 and "1" all mean enabled."""
    return value is True or value == 1 or value == "1"


def normalize_tag_names(tags: list[str] | None) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        raise ValueError("tags must be a list of tag names")

    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise ValueError("tags must contain only strings")
        name = tag.strip()
        if not name or name in seen:
            continue
        normalized.append(name)
        seen.add(name)
    return normalized


async def get_tags_enabled(client: Any) -> bool:
    session = client.session
    if session is None:
        raise NotLoggedInError()
    value = session.user_configs.get(TAG_ENABLED_CONFIG_KEY)
    return tags_flag_enabled(value)


async def set_tags_enabled(client: Any, enabled: bool) -> dict:
    session = client.session
    if session is None:
        raise NotLoggedInError()

    value = 1 if enabled else 0
    await client.user_update_config({TAG_ENABLED_CONFIG_KEY: value})

    session.user_configs[TAG_ENABLED_CONFIG_KEY] = value

    session_mgr = getattr(client, "_session_mgr", None)
    if session_mgr is not None and session is not None:
        await session_mgr.save(session)

    return {"ok": True, "enabled": bool(enabled)}


async def _tags_feature_enabled(client: Any) -> bool:
    """Read the cached tagon switch; refresh upstream once and cache when unknown."""
    session = client.session
    if session is None:
        raise NotLoggedInError()
    if TAG_ENABLED_CONFIG_KEY not in session.user_configs:
        await client.refresh_user_configs()
        session = client.session
        if session is None:
            raise NotLoggedInError()
    return tags_flag_enabled(session.user_configs.get(TAG_ENABLED_CONFIG_KEY))


async def resolve_tag_names(client: Any, tags: list[str] | None) -> list[str]:
    names = normalize_tag_names(tags)
    if not names:
        return []

    if not await _tags_feature_enabled(client):
        raise TagFeatureDisabledError()

    tags_by_name = _tags_by_name(await client.tag_list())
    resolved: list[str] = []
    unresolved: list[str] = []

    for name in names:
        tag_id = tags_by_name.get(name)
        if tag_id is None:
            create_result = await client.tag_submit(name=name)
            tag_id = _created_tag_id(create_result)
            if tag_id is None:
                tags_by_name = _tags_by_name(await client.tag_list())
                tag_id = tags_by_name.get(name)
        if tag_id is None:
            unresolved.append(name)
        else:
            resolved.append(tag_id)
            tags_by_name[name] = tag_id

    if unresolved:
        raise QianjiError(502, f"Failed to resolve tag names: {unresolved}")

    return resolved


def _tags_by_name(response: dict) -> dict[str, str]:
    return {item["name"]: item["id"] for item in flatten_tag_items(response)}


def flatten_tag_items(response: dict) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for tag in _iter_tag_records(_tag_roots(response)):
        name = tag.get("name")
        tag_id = _tag_id(tag)
        if isinstance(name, str) and tag_id is not None:
            items.append({"id": str(tag_id), "name": name})
    return items


def _tag_roots(response: dict) -> Any:
    data = response.get("data")
    if isinstance(data, dict):
        roots = {key: data[key] for key in TAG_ROOT_KEYS if key in data}
        if roots:
            return roots
    return response


def _iter_tag_records(record: Any, container_key: str | None = None):
    if isinstance(record, list):
        for item in record:
            yield from _iter_tag_records(item, container_key)
        return

    if not isinstance(record, dict):
        return

    if _is_tag_record(record, container_key):
        yield record

    for key, value in record.items():
        if key in TAG_CONTAINER_KEYS:
            yield from _iter_tag_records(value, _next_container_context(container_key, key))


def _created_tag_id(response: dict) -> str | None:
    data = response.get("data")
    candidates = []
    if isinstance(data, dict):
        candidates.append(_tag_id(data))
        tag = data.get("tag")
        if isinstance(tag, dict):
            candidates.append(_tag_id(tag))
    candidates.append(_tag_id(response))

    for candidate in candidates:
        if candidate is not None:
            return str(candidate)
    return None


def _tag_id(record: dict) -> Any:
    return record.get("tagid") or record.get("tagId") or record.get("id")


def _next_container_context(current: str | None, key: str) -> str:
    if key in {"tags", "tagList"}:
        return key
    if current == "groups":
        return current
    return key


def _is_tag_record(record: dict, container_key: str | None = None) -> bool:
    tag_id = _tag_id(record)
    if not isinstance(record.get("name"), str) or tag_id is None:
        return False
    if container_key == "groups" and "tagid" not in record and "tagId" not in record:
        return False
    has_container = any(key in record for key in TAG_CONTAINER_KEYS)
    return not has_container or "tagid" in record or "tagId" in record
