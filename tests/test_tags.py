from __future__ import annotations

import asyncio

import pytest

from qianji.api.user import UserAPI
from qianji.errors import NotLoggedInError, QianjiError, TagFeatureDisabledError
from qianji.session import SessionData
from qianji.tags import flatten_tag_items, get_tags_enabled, normalize_tag_names, resolve_tag_names, set_tags_enabled


class FakeTagClient:
    def __init__(self, *, enabled: bool):
        self.current_account = "a"
        self.remote_enabled: bool | None = None
        self.refresh_calls = 0
        self._session = SessionData(
            account_id="a",
            email="a@example.com",
            pwd_hash="pwd",
            utoken="token",
            user_id="user-a",
            devid="device-a",
            devname="Device A",
            user_configs={"tagon": 1 if enabled else 0},
        )
        self.updated_configs: list[dict] = []
        self.created_names: list[str] = []
        self.tags = [
            {"id": "g1", "name": "默认组", "tags": [{"id": "10", "name": "工作", "groupid": "g1"}]},
        ]

    @property
    def session(self):
        return self._session

    async def refresh_user_configs(self):
        self.refresh_calls += 1
        if self.remote_enabled is not None:
            self.session.user_configs["tagon"] = 1 if self.remote_enabled else 0
        return self.session.user_configs

    async def user_update_config(self, config: dict):
        self.updated_configs.append(config)
        return {"ec": 200, "em": "ok"}

    async def tag_list(self):
        return {"data": {"list": self.tags}}

    async def tag_submit(self, name: str, groupid: str = "", tagid: str = "", color: str | None = None):
        self.created_names.append(name)
        new_id = str(10 + len(self.created_names))
        self.tags[0]["tags"].append({"id": new_id, "name": name, "groupid": "g1"})
        return {"ec": 200, "data": {"tag": {"id": new_id, "name": name}}}


class FakeTagPayloadClient(FakeTagClient):
    def __init__(self, payload: dict):
        super().__init__(enabled=True)
        self.payload = payload

    async def tag_list(self):
        return self.payload


class FakeUserAPIClient(FakeTagClient, UserAPI):
    pass


class FakeCreateTagIdClient(FakeTagClient):
    def __init__(self):
        super().__init__(enabled=True)
        self.tag_list_calls = 0
        self.tags = []

    async def tag_list(self):
        self.tag_list_calls += 1
        return {"data": {"list": self.tags}}

    async def tag_submit(self, name: str, groupid: str = "", tagid: str = "", color: str | None = None):
        self.created_names.append(name)
        return {"ec": 200, "data": {"tag": {"tagId": "41", "name": name}}}


class FakeCreateNeedsRefreshClient(FakeTagClient):
    def __init__(self, *, refresh_resolves: bool):
        super().__init__(enabled=True)
        self.refresh_resolves = refresh_resolves
        self.tag_list_calls = 0
        self.tags = []

    async def tag_list(self):
        self.tag_list_calls += 1
        if self.refresh_resolves and self.tag_list_calls > 1:
            self.tags = [{"id": "50", "name": "刷新"}]
        return {"data": {"list": self.tags}}

    async def tag_submit(self, name: str, groupid: str = "", tagid: str = "", color: str | None = None):
        self.created_names.append(name)
        return {"ec": 200, "data": {"tag": {"name": name}}}


class FakeLoggedOutClient:
    def __init__(self):
        self.updated_configs: list[dict] = []

    @property
    def session(self):
        return None

    async def user_update_config(self, config: dict):
        self.updated_configs.append(config)
        return {"ec": 200, "em": "ok"}


class FakeRefreshFailureClient(FakeTagClient):
    async def refresh_user_configs(self):
        self.refresh_calls += 1
        raise QianjiError(502, "refresh failed")


class FakeSessionManager:
    def __init__(self):
        self.saved_sessions: list[SessionData] = []

    async def save(self, session: SessionData):
        self.saved_sessions.append(session)


def test_normalize_tag_names_strips_deduplicates_and_drops_blanks():
    assert normalize_tag_names([" 工作 ", "", "差旅", "工作", "  "]) == ["工作", "差旅"]


def test_tag_feature_disabled_error_is_reexported():
    from qianji import TagFeatureDisabledError as ReexportedTagFeatureDisabledError

    assert ReexportedTagFeatureDisabledError is TagFeatureDisabledError


def test_normalize_tag_names_accepts_none_as_empty_list():
    assert normalize_tag_names(None) == []


def test_normalize_tag_names_rejects_non_list():
    with pytest.raises(ValueError, match="tags must be a list of tag names"):
        normalize_tag_names("工作")


def test_normalize_tag_names_rejects_non_string_items():
    with pytest.raises(ValueError, match="tags must contain only strings"):
        normalize_tag_names(["工作", 1])


def test_get_and_set_tags_enabled_use_tagon():
    async def run():
        client = FakeTagClient(enabled=False)
        before = await get_tags_enabled(client)
        result = await set_tags_enabled(client, True)
        after = await get_tags_enabled(client)
        return client, before, result, after

    client, before, result, after = asyncio.run(run())

    assert before is False
    assert result == {"ok": True, "enabled": True}
    assert after is True
    assert client.updated_configs == [{"tagon": 1}]
    assert client.session.user_configs["tagon"] == 1
    assert client.refresh_calls == 0


def test_get_tags_enabled_reads_cached_tagon_without_refreshing():
    async def run():
        client = FakeTagClient(enabled=False)
        client.remote_enabled = True
        return client, await get_tags_enabled(client)

    client, enabled = asyncio.run(run())

    assert enabled is False
    assert client.refresh_calls == 0
    assert client.session.user_configs["tagon"] == 0


def test_get_tags_enabled_does_not_refresh_or_raise_when_refresh_fails():
    async def run():
        client = FakeRefreshFailureClient(enabled=False)
        return client, await get_tags_enabled(client)

    client, enabled = asyncio.run(run())

    assert enabled is False
    assert client.refresh_calls == 0


def test_user_api_tag_switch_methods_delegate_to_tag_helpers():
    async def run():
        client = FakeUserAPIClient(enabled=False)
        before = await client.get_tags_enabled()
        result = await client.set_tags_enabled(True)
        after = await client.get_tags_enabled()
        return client, before, result, after

    client, before, result, after = asyncio.run(run())

    assert before is False
    assert result == {"ok": True, "enabled": True}
    assert after is True
    assert client.updated_configs == [{"tagon": 1}]


def test_set_tags_enabled_persists_when_session_manager_exists_without_current_account():
    async def run():
        client = FakeTagClient(enabled=False)
        client.current_account = None
        client._session_mgr = FakeSessionManager()
        result = await set_tags_enabled(client, True)
        return client, result

    client, result = asyncio.run(run())

    assert result == {"ok": True, "enabled": True}
    assert client._session_mgr.saved_sessions == [client.session]


def test_get_tags_enabled_requires_session():
    async def run():
        client = FakeLoggedOutClient()
        with pytest.raises(NotLoggedInError):
            await get_tags_enabled(client)

    asyncio.run(run())


def test_set_tags_enabled_requires_session():
    async def run():
        client = FakeLoggedOutClient()
        with pytest.raises(NotLoggedInError):
            await set_tags_enabled(client, True)
        return client

    client = asyncio.run(run())

    assert client.updated_configs == []


def test_resolve_tag_names_raises_when_tags_disabled():
    async def run():
        client = FakeTagClient(enabled=False)
        with pytest.raises(TagFeatureDisabledError):
            await resolve_tag_names(client, ["工作", "差旅"])
        return client

    client = asyncio.run(run())

    assert client.created_names == []
    assert client.refresh_calls == 0


def test_resolve_tag_names_refreshes_once_when_switch_unknown_and_still_disabled():
    async def run():
        client = FakeTagClient(enabled=True)
        del client.session.user_configs["tagon"]
        client.remote_enabled = False
        with pytest.raises(TagFeatureDisabledError):
            await resolve_tag_names(client, ["工作"])
        return client

    client = asyncio.run(run())

    assert client.refresh_calls == 1
    assert client.created_names == []


def test_resolve_tag_names_resolves_after_refresh_caches_enabled_switch():
    async def run():
        client = FakeTagClient(enabled=True)
        del client.session.user_configs["tagon"]
        client.remote_enabled = True
        ids = await resolve_tag_names(client, ["工作"])
        again = await resolve_tag_names(client, ["工作"])
        return client, ids, again

    client, ids, again = asyncio.run(run())

    assert ids == ["10"]
    assert again == ["10"]
    assert client.refresh_calls == 1


def test_tag_feature_disabled_error_message_mentions_python_true():
    assert TagFeatureDisabledError().em == (
        "Qianji tag feature is disabled. Call set_tags_enabled(True) before passing tags."
    )


def test_resolve_tag_names_accepts_empty_tags_when_disabled():
    async def run():
        client = FakeTagClient(enabled=False)
        return await resolve_tag_names(client, [])

    assert asyncio.run(run()) == []


def test_resolve_tag_names_reuses_existing_and_creates_missing():
    async def run():
        client = FakeTagClient(enabled=True)
        ids = await resolve_tag_names(client, ["工作", "差旅"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["10", "11"]
    assert client.created_names == ["差旅"]


def test_resolve_tag_names_does_not_treat_group_record_as_tag():
    async def run():
        client = FakeTagClient(enabled=True)
        client.tags = [{"id": "g1", "name": "默认组", "tags": []}]
        ids = await resolve_tag_names(client, ["默认组"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["11"]
    assert client.created_names == ["默认组"]


def test_resolve_tag_names_does_not_treat_group_record_with_groupid_as_tag():
    async def run():
        client = FakeTagClient(enabled=True)
        client.tags = [{"id": "g1", "groupid": "g1", "name": "默认组", "tags": []}]
        ids = await resolve_tag_names(client, ["默认组"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["11"]
    assert client.created_names == ["默认组"]


def test_resolve_tag_names_does_not_treat_sparse_groups_root_record_as_tag():
    async def run():
        client = FakeTagPayloadClient({"data": {"groups": [{"id": "g1", "name": "默认组"}]}})
        ids = await resolve_tag_names(client, ["默认组"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["11"]
    assert client.created_names == ["默认组"]


def test_resolve_tag_names_preserves_group_context_through_children():
    async def run():
        client = FakeTagPayloadClient(
            {"data": {"groups": [{"name": "root", "children": [{"id": "g2", "name": "nested-group"}]}]}}
        )
        ids = await resolve_tag_names(client, ["nested-group"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["11"]
    assert client.created_names == ["nested-group"]


def test_resolve_tag_names_preserves_group_context_through_nested_list():
    async def run():
        client = FakeTagPayloadClient({"data": {"groups": {"list": [{"id": "g3", "name": "list-group"}]}}})
        ids = await resolve_tag_names(client, ["list-group"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["11"]
    assert client.created_names == ["list-group"]


def test_resolve_tag_names_reuses_existing_tagid_camel_case():
    async def run():
        client = FakeTagPayloadClient({"data": {"list": [{"tagId": "40", "name": "驼峰"}]}})
        ids = await resolve_tag_names(client, ["驼峰"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["40"]
    assert client.created_names == []


def test_resolve_tag_names_ignores_unrelated_nested_metadata():
    async def run():
        client = FakeTagPayloadClient(
            {"data": {"list": [{"id": "10", "name": "工作", "meta": {"id": "x", "name": "not-a-tag"}}]}}
        )
        ids = await resolve_tag_names(client, ["not-a-tag"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["11"]
    assert client.created_names == ["not-a-tag"]


def test_resolve_tag_names_uses_created_tagid_camel_case_without_refresh():
    async def run():
        client = FakeCreateTagIdClient()
        ids = await resolve_tag_names(client, ["新标签"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["41"]
    assert client.created_names == ["新标签"]
    assert client.tag_list_calls == 1


def test_resolve_tag_names_refreshes_when_create_response_has_no_id():
    async def run():
        client = FakeCreateNeedsRefreshClient(refresh_resolves=True)
        ids = await resolve_tag_names(client, ["刷新"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["50"]
    assert client.created_names == ["刷新"]
    assert client.tag_list_calls == 2


def test_resolve_tag_names_raises_when_created_tag_still_unresolved():
    async def run():
        client = FakeCreateNeedsRefreshClient(refresh_resolves=False)
        with pytest.raises(QianjiError) as exc_info:
            await resolve_tag_names(client, ["丢失"])
        return client, exc_info.value

    client, exc = asyncio.run(run())

    assert exc.ec == 502
    assert exc.em == "Failed to resolve tag names: ['丢失']"
    assert client.created_names == ["丢失"]
    assert client.tag_list_calls == 2


def test_resolve_tag_names_flattens_nested_groups():
    async def run():
        client = FakeTagClient(enabled=True)
        client.tags = [
            {
                "name": "outer",
                "tags": [
                    {
                        "name": "inner",
                        "children": [
                            {"id": "12", "name": "嵌套"},
                        ],
                    }
                ],
            }
        ]
        ids = await resolve_tag_names(client, ["嵌套"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["12"]
    assert client.created_names == []


def test_resolve_tag_names_flattens_known_root_containers():
    async def run():
        client = FakeTagPayloadClient(
            {
                "data": {
                    "groups": [{"tags": [{"id": "21", "name": "分组根"}]}],
                    "update": [{"tags": [{"id": "22", "name": "更新根"}]}],
                }
            }
        )
        ids = await resolve_tag_names(client, ["分组根", "更新根"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["21", "22"]
    assert client.created_names == []


def test_resolve_tag_names_flattens_known_nested_containers():
    async def run():
        client = FakeTagPayloadClient(
            {
                "data": {
                    "list": [
                        {
                            "name": "outer",
                            "sublist": [
                                {
                                    "name": "middle",
                                    "tagList": [
                                        {"id": "31", "name": "子列表"},
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }
        )
        ids = await resolve_tag_names(client, ["子列表"])
        return client, ids

    client, ids = asyncio.run(run())

    assert ids == ["31"]
    assert client.created_names == []


def test_flatten_tag_items_flattens_groups_and_skips_empty_group_records():
    payload = {
        "data": {
            "list": [
                {"id": "", "name": None, "tags": [{"id": "2606166a30efaea0beb4525", "name": "codex"}]},
                {"id": "group-1", "name": "默认组", "tags": [{"tagid": "work-id", "name": "工作"}]},
            ]
        }
    }

    assert flatten_tag_items(payload) == [
        {"id": "2606166a30efaea0beb4525", "name": "codex"},
        {"id": "work-id", "name": "工作"},
    ]
