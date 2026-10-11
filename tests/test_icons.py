from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qianji import icon_cache
from qianji.server.routes import public_router

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


def _icon_app() -> FastAPI:
    app = FastAPI()
    app.include_router(public_router)
    return app


def _use_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("QIANJI_ICON_CACHE_DIR", str(tmp_path))


# --- name validation ---


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("cateic_gongzi.png", True),
        ("ic_cate2_jiangjin.png", True),
        ("a.png", True),
        ("../etc/passwd.png", False),
        ("..%2Fx.png", False),
        ("nested/icon.png", False),
        ("icon.svg", False),
        ("icon.png.exe", False),
        ("icon", False),
        (".png", False),
        ("", False),
    ],
)
def test_icon_name_validation(name, expected):
    assert icon_cache.is_valid_icon_name(name) is expected


def test_basename_from_url_rejects_non_png_and_missing():
    assert icon_cache.basename_from_url("http://res3.qianjiapp.com/a_b.png") == "a_b.png"
    assert icon_cache.basename_from_url("https://res.qianjiapp.com/x.png") == "x.png"
    assert icon_cache.basename_from_url("http://res3.qianjiapp.com/x.svg") is None
    assert icon_cache.basename_from_url("") is None


def test_icon_names_from_payload_skips_group_headers_and_dedupes():
    payload = {
        "ec": 200,
        "data": {
            "list": [
                {"group": "1", "groupname": "收入"},
                {"name": "工资", "icon": "http://res3.qianjiapp.com/cateic_gongzi.png"},
                {"name": "工资别名", "icon": "http://res.qianjiapp.com/cateic_gongzi.png"},
                {"name": "餐饮", "icon": "http://res3.qianjiapp.com/cateic_canyin.png"},
                {"name": "无图标", "icon": ""},
                {"name": "非法", "icon": "http://res3.qianjiapp.com/bad.svg"},
            ]
        },
    }

    assert icon_cache.icon_names_from_payload(payload) == ["cateic_gongzi.png", "cateic_canyin.png"]
    assert icon_cache.icon_names_from_payload({}) == []
    assert icon_cache.icon_names_from_payload({"data": {"list": "nope"}}) == []


# --- upstream fetch ---


def _transport(handler):
    return httpx.MockTransport(handler)


def test_fetch_icon_falls_back_to_second_host():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == icon_cache.ICON_HOSTS[0]:
            return httpx.Response(500)
        return httpx.Response(200, content=PNG_BYTES)

    content = asyncio.run(icon_cache.fetch_icon("a.png", transport=_transport(handler)))

    assert content == PNG_BYTES
    assert seen == list(icon_cache.ICON_HOSTS)


def test_fetch_icon_rejects_non_png_and_oversized():
    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>nope</html>")

    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"x" * icon_cache.MAX_ICON_BYTES)

    assert asyncio.run(icon_cache.fetch_icon("a.png", transport=_transport(html))) is None
    assert asyncio.run(icon_cache.fetch_icon("a.png", transport=_transport(oversized))) is None


def test_fetch_icon_rejects_invalid_name_without_requesting():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("must not request for an invalid name")

    assert asyncio.run(icon_cache.fetch_icon("../x.png", transport=_transport(handler))) is None


def test_ensure_icon_dedupes_concurrent_misses(monkeypatch, tmp_path):
    _use_cache_dir(monkeypatch, tmp_path)
    calls = 0

    async def fake_fetch(name, *, transport=None):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return PNG_BYTES

    monkeypatch.setattr(icon_cache, "fetch_icon", fake_fetch)

    async def main():
        return await asyncio.gather(*(icon_cache.ensure_icon("a.png") for _ in range(5)))

    results = asyncio.run(main())

    assert results == [PNG_BYTES] * 5
    assert calls == 1
    assert (tmp_path / "a.png").read_bytes() == PNG_BYTES


def test_ensure_icon_serves_from_cache_without_fetching(monkeypatch, tmp_path):
    _use_cache_dir(monkeypatch, tmp_path)
    (tmp_path / "a.png").write_bytes(PNG_BYTES)

    async def fake_fetch(name, *, transport=None):  # pragma: no cover - must not run
        raise AssertionError("cache hit must not reach upstream")

    monkeypatch.setattr(icon_cache, "fetch_icon", fake_fetch)

    assert asyncio.run(icon_cache.ensure_icon("a.png")) == PNG_BYTES


def test_store_icon_tolerates_read_only_directory(monkeypatch, tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("this is a file, not a directory", encoding="utf-8")
    _use_cache_dir(monkeypatch, blocker)

    assert icon_cache.store_icon("a.png", PNG_BYTES) is False


# --- route ---


def test_category_icon_route_serves_cached_file(monkeypatch, tmp_path):
    _use_cache_dir(monkeypatch, tmp_path)
    (tmp_path / "probe_icon.png").write_bytes(PNG_BYTES)

    with TestClient(_icon_app()) as client:
        response = client.get("/category-icons/probe_icon.png")

    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("name", ["..%2Fx.png", "nested%2Ficon.png", "icon.svg", "icon"])
def test_category_icon_route_rejects_invalid_names(monkeypatch, tmp_path, name):
    _use_cache_dir(monkeypatch, tmp_path)

    with TestClient(_icon_app()) as client:
        response = client.get(f"/category-icons/{name}")

    assert response.status_code == 404


def test_category_icon_route_404s_when_upstream_unavailable(monkeypatch, tmp_path):
    _use_cache_dir(monkeypatch, tmp_path)

    async def failing_fetch(name, *, transport=None):
        return None

    monkeypatch.setattr(icon_cache, "fetch_icon", failing_fetch)

    with TestClient(_icon_app()) as client:
        response = client.get("/category-icons/probe_missing.png")

    assert response.status_code == 404
    assert not (tmp_path / "probe_missing.png").exists()


# --- prefetch script ---


def test_prefetch_downloads_then_skips_existing(tmp_path):
    import scripts.prefetch_icons as prefetch_icons

    names = ["a.png", "b.png", "c.png"]
    fetched: list[str] = []

    async def fake_fetch(name, *, transport=None):
        fetched.append(name)
        return None if name == "c.png" else PNG_BYTES

    written, skipped, failures = asyncio.run(
        prefetch_icons.prefetch(names, out_dir=tmp_path, concurrency=2, fetch=fake_fetch)
    )

    assert (written, skipped, failures) == (2, 0, ["c.png"])
    assert fetched == names
    assert (tmp_path / "a.png").read_bytes() == PNG_BYTES

    written, skipped, failures = asyncio.run(
        prefetch_icons.prefetch(names, out_dir=tmp_path, concurrency=2, fetch=fake_fetch)
    )

    assert (written, skipped, failures) == (0, 2, ["c.png"])


def test_prefetch_force_redownloads(tmp_path):
    import scripts.prefetch_icons as prefetch_icons

    (tmp_path / "a.png").write_bytes(b"stale")
    calls: list[str] = []

    async def fake_fetch(name, *, transport=None):
        calls.append(name)
        return PNG_BYTES

    written, skipped, failures = asyncio.run(
        prefetch_icons.prefetch(["a.png"], out_dir=tmp_path, force=True, fetch=fake_fetch)
    )

    assert (written, skipped, failures) == (1, 0, [])
    assert calls == ["a.png"]
    assert (tmp_path / "a.png").read_bytes() == PNG_BYTES
