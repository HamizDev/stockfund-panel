"""契约测试 for the bounded public indicator research library."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Settings normally load the project .env at import time. Point the one-time
# initialization at an empty temporary location so this suite never reads secrets.
_prior_env_file = os.environ.get("TICKFLOW_ENV_FILE")
with TemporaryDirectory(prefix="indicator-library-no-env-") as _empty_config_dir:
    os.environ["TICKFLOW_ENV_FILE"] = str(Path(_empty_config_dir) / "absent.env")
    try:
        from app.config import settings
        from app.custom.indicator_library import service
        from app.custom.indicator_library.routes import build_router
    finally:
        if _prior_env_file is None:
            os.environ.pop("TICKFLOW_ENV_FILE", None)
        else:
            os.environ["TICKFLOW_ENV_FILE"] = _prior_env_file


_HTTPX_CLIENT = httpx.Client
_HTTPX_MOCK_TRANSPORT = httpx.MockTransport


class _Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Network:
    """Each service client gets a MockTransport; unexpected requests fail locally."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = self._unexpected

    @staticmethod
    def _unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected outbound request: {request.url}")

    def dispatch(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def client_factory(*args, **kwargs):
            return _HTTPX_CLIENT(
                *args,
                transport=_HTTPX_MOCK_TRANSPORT(self.dispatch),
                **kwargs,
            )

        # Patch only the library's HTTP dependency. TestClient keeps the real httpx module.
        monkeypatch.setattr(
            service,
            "httpx",
            SimpleNamespace(Client=client_factory, Timeout=httpx.Timeout, HTTPError=httpx.HTTPError),
        )


class _Sandbox:
    def __init__(self, clock: _Clock, network: _Network) -> None:
        self.clock = clock
        self.network = network


@pytest.fixture
def sandbox(tmp_path, monkeypatch) -> _Sandbox:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    clock = _Clock()
    monkeypatch.setattr(service, "_clock", clock)
    network = _Network()
    network.install(monkeypatch)
    return _Sandbox(clock, network)


def _script(
    script_id: str,
    *,
    name: str | None = None,
    author: str = "researcher",
    kind: str = "indicator",
    access: int = 1,
    version: str = "3.2",
) -> dict:
    return {
        "script_access": access,
        "script_id_part": script_id,
        "name": name or script_id,
        "description": "公开研究脚本",
        "script_type": kind,
        "version": version,
        "user": {"username": author},
        "chart_url": f"https://www.tradingview.com/script/{script_id.removeprefix('PUB;')}/",
    }


def _listing(items: list[dict], next_page: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": {"ideas": {"data": {"items": items, "next": next_page}}}},
    )


def _source_payload(text: str, *, version: str = "3.2", access="open_no_auth") -> httpx.Response:
    return httpx.Response(
        200,
        json={"success": True, "scriptAccess": access, "source": text, "version": version},
    )


def _script_id_from_request(request: httpx.Request) -> str:
    return unquote(urlsplit(str(request.url)).path.rsplit("/", 2)[-2])


def _api_client() -> TestClient:
    app = FastAPI()
    app.include_router(build_router())
    return TestClient(app)


def test_chinese_topic_mapping_and_invalid_input(sandbox: _Sandbox) -> None:
    sandbox.network.handler = lambda _request: _listing([_script("PUB;TREND")])

    assert service.topic_slug(" 超级趋势 ") == "supertrend"
    assert service.topic_slug("布林带") == "bollingerbands"
    result = service.search("超级趋势")
    assert result["topic"] == "supertrend"
    assert [item["id"] for item in result["items"]] == ["PUB;TREND"]
    assert urlsplit(str(sandbox.network.requests[0].url)).path == "/scripts/supertrend/"
    offline = service.search("超级趋势", offline=True)
    assert [item["id"] for item in offline["items"]] == ["PUB;TREND"]
    assert offline["items"][0]["topic"] == "supertrend"
    assert offline["items"][0]["topic_name"] == "超级趋势"

    with pytest.raises(ValueError, match="中文主题尚未映射"):
        service.search("未收录的中文主题")
    with pytest.raises(ValueError, match="查询条件无效"):
        service.search("supertrend", kind="unsupported")
    assert len(sandbox.network.requests) == 1


def test_search_uses_fixed_public_endpoint_and_deduplicates_public_scripts(
    sandbox: _Sandbox,
) -> None:
    sandbox.network.handler = lambda _request: _listing([
        _script("PUB;DUP", name="旧名称"),
        _script("PUB;PRIVATE", name="受保护脚本", access=0),
        _script("bad id", name="无效 ID"),
        _script("PUB;DUP", name="更新名称"),
    ])

    result = service.search("supertrend")

    assert len(sandbox.network.requests) == 1
    request = sandbox.network.requests[0]
    parsed = urlsplit(str(request.url))
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "www.tradingview.com",
        "/scripts/supertrend/",
    )
    assert dict(request.url.params) == {
        "component-data-only": "1",
        "script_access": "open",
        "script_type": "all",
    }
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "PUB;DUP"
    assert result["items"][0]["name"] == "更新名称"
    assert result["items"][0]["url"] == "https://www.tradingview.com/script/DUP/"


def test_sqlite_listing_hit_and_offline_source_text_search(sandbox: _Sandbox) -> None:
    sandbox.network.handler = lambda request: (
        _listing([_script("PUB;CACHE")])
        if "/scripts/" in urlsplit(str(request.url)).path
        else _source_payload("// @version=5\nplot(close) // UNIQUE_OFFLINE_MARKER", version="5.1")
    )

    first = service.search("supertrend")
    second = service.search("supertrend")
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["items"] == first["items"]
    assert len(sandbox.network.requests) == 1

    sandbox.clock.advance(2.1)
    source = service.source("PUB;CACHE")
    assert source["source"].endswith("UNIQUE_OFFLINE_MARKER")
    assert source["version"] == "5.1"
    assert source["pine_version"] == 5
    assert len(sandbox.network.requests) == 2

    offline = service.search("unique_offline_marker", offline=True)
    assert offline["offline"] is True
    assert [item["id"] for item in offline["items"]] == ["PUB;CACHE"]
    assert offline["items"][0]["source_cached"] is True
    assert offline["items"][0]["cached_version"] == "5.1"
    assert len(sandbox.network.requests) == 2


def test_source_version_mismatch_and_protected_source_are_refused(sandbox: _Sandbox) -> None:
    original_source = "// @version=5\nplot(close) // ORIGINAL_PUBLIC_SOURCE"

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        if path.startswith("/scripts/"):
            return _listing([_script("PUB;VERSIONED")])
        requested_version = unquote(path.rsplit("/", 1)[-1])
        if requested_version == "7.2":
            return _source_payload(original_source, version="7.2")
        if requested_version == "7.1":
            return _source_payload("// wrong exact revision", version="7.2")
        return _source_payload("PROTECTED_BODY_MUST_NOT_BE_SAVED", version="9.0", access="protected")

    sandbox.network.handler = handler
    service.search("supertrend")
    sandbox.clock.advance(2.1)

    fetched = service.source("PUB;VERSIONED", version="7.2")
    assert fetched["version"] == "7.2"
    assert fetched["source"] == original_source
    assert urlsplit(str(sandbox.network.requests[1].url)).netloc == "pine-facade.tradingview.com"

    sandbox.clock.advance(2.1)
    with pytest.raises(service.LibraryError, match="版本与所选版本不符"):
        service.source("PUB;VERSIONED", version="7.1")
    sandbox.clock.advance(2.1)
    with pytest.raises(service.LibraryError, match="不会尝试读取受保护内容"):
        service.source("PUB;VERSIONED", version="9")

    cached = service.source("PUB;VERSIONED", version="7")
    assert cached["cached"] is True
    assert cached["source"] == original_source
    assert "PROTECTED_BODY_MUST_NOT_BE_SAVED" not in cached["source"]
    assert len(sandbox.network.requests) == 4


def test_source_enforces_utf8_256_kibibyte_limit(sandbox: _Sandbox) -> None:
    exact_id, oversized_id = "PUB;EXACT", "PUB;TOOLARGE"
    prefix = "// @version=5\n"
    exact_text = prefix + "x" * (service.MAX_SOURCE_BYTES - len(prefix.encode("utf-8")))
    oversized_text = exact_text + "x"

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        if path.startswith("/scripts/"):
            return _listing([_script(exact_id), _script(oversized_id)])
        script_id = _script_id_from_request(request)
        return _source_payload(exact_text if script_id == exact_id else oversized_text, version="5.1")

    sandbox.network.handler = handler
    service.search("supertrend")

    sandbox.clock.advance(2.1)
    accepted = service.source(exact_id)
    assert accepted["bytes"] == 256 * 1024
    assert accepted["source"] == exact_text

    sandbox.clock.advance(2.1)
    with pytest.raises(service.LibraryError, match="超过 256 KiB"):
        service.source(oversized_id)
    with pytest.raises(service.LibraryError, match="此版本尚未缓存"):
        service.source(oversized_id, offline=True)
    assert len(sandbox.network.requests) == 3


def test_expired_last_source_is_refetched(sandbox: _Sandbox) -> None:
    fetch_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_count
        path = urlsplit(str(request.url)).path
        if path.startswith("/scripts/"):
            return _listing([_script("PUB;TTL")])
        fetch_count += 1
        version = f"6.{fetch_count}"
        source_text = f"// @version={fetch_count}\nplot(close) // FETCH_{fetch_count}"
        return _source_payload(source_text, version=version)

    sandbox.network.handler = handler
    service.search("supertrend")
    sandbox.clock.advance(2.1)
    first = service.source("PUB;TTL", version="last")
    assert first["source"].endswith("FETCH_1")

    sandbox.clock.advance(service.SOURCE_TTL + 1)
    second = service.source("PUB;TTL", version="last")
    assert second["source"].endswith("FETCH_2")
    assert second["version"] == "6.2"
    assert second["cached"] is False
    assert fetch_count == 2
    assert len(sandbox.network.requests) == 3


def test_api_catalog_pagination_and_parameter_errors(sandbox: _Sandbox) -> None:
    sandbox.network.handler = lambda _request: _listing([_script("PUB;PAGE")], next_page="next")
    client = _api_client()

    catalog = client.get("/api/custom/indicator-library/catalog")
    assert catalog.status_code == 200
    assert any(topic["id"] == "supertrend" and topic["name"] == "超级趋势" for topic in catalog.json()["topics"])
    assert any(recipe["id"] == "dmi_confirm" for recipe in catalog.json()["recipes"])

    first = client.get("/api/custom/indicator-library/search", params={"q": "超级趋势"})
    assert first.status_code == 200
    assert first.json()["topic"] == "supertrend"
    assert first.json()["page"] == 1
    assert first.json()["has_more"] is True
    assert [item["id"] for item in first.json()["items"]] == ["PUB;PAGE"]

    sandbox.clock.advance(2.1)
    second = client.get(
        "/api/custom/indicator-library/search",
        params={"q": "超级趋势", "page": 2},
    )
    assert second.status_code == 200
    assert second.json()["page"] == 2
    assert second.json()["has_more"] is True
    assert urlsplit(str(sandbox.network.requests[1].url)).path == "/scripts/supertrend/page-2/"

    invalid_requests = [
        {"q": "未知中文主题"},
        {"q": "supertrend", "kind": "unknown"},
        {"q": "supertrend", "page": 0},
        {"q": "supertrend", "page": 21},
        {"q": "x" * 81},
        {"q": "", "author": "bad!"},
    ]
    for params in invalid_requests:
        response = client.get("/api/custom/indicator-library/search", params=params)
        assert response.status_code == 422, params

    invalid_source_id = client.get(
        "/api/custom/indicator-library/source",
        params={"id": "not-a-public-id"},
    )
    assert invalid_source_id.status_code == 422
    invalid_source_version = client.get(
        "/api/custom/indicator-library/source",
        params={"id": "PUB;PAGE", "version": "latest"},
    )
    assert invalid_source_version.status_code == 422
    assert len(sandbox.network.requests) == 2


def test_api_maps_sqlite_failures_to_generic_cache_error(sandbox: _Sandbox, monkeypatch) -> None:
    def fail_with_database_error(*_args, **_kwargs):
        raise sqlite3.OperationalError("private database detail")

    monkeypatch.setattr(service, "search", fail_with_database_error)
    monkeypatch.setattr(service, "source", fail_with_database_error)
    client = _api_client()

    search_error = client.get("/api/custom/indicator-library/search")
    assert search_error.status_code == 400
    assert search_error.json()["detail"] == "本机研究缓存暂不可用"
    assert "private database detail" not in search_error.text

    source_error = client.get(
        "/api/custom/indicator-library/source",
        params={"id": "PUB;ANY"},
    )
    assert source_error.status_code == 400
    assert source_error.json()["detail"] == "本机研究缓存暂不可用"
    assert "private database detail" not in source_error.text
    assert sandbox.network.requests == []


def test_concurrent_different_searches_share_two_second_sqlite_gate(sandbox: _Sandbox) -> None:
    sandbox.network.handler = lambda _request: _listing([_script("PUB;RACE")])
    start = threading.Barrier(3)

    def search_after_barrier(query: str):
        start.wait(timeout=5)
        try:
            return service.search(query)
        except service.LibraryError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(search_after_barrier, query) for query in ("supertrend", "rsi")]
        start.wait(timeout=5)
        results = [future.result(timeout=10) for future in futures]

    assert sum(isinstance(result, service.LibraryError) for result in results) == 1
    successful = [result for result in results if isinstance(result, dict)]
    assert len(successful) == 1
    assert successful[0]["items"][0]["id"] == "PUB;RACE"
    assert len(sandbox.network.requests) == 1
    assert urlsplit(str(sandbox.network.requests[0].url)).path in {
        "/scripts/supertrend/",
        "/scripts/rsi/",
    }


def test_daily_request_budget_stops_before_network(sandbox: _Sandbox) -> None:
    for _ in range(100):
        service._claim_request()
        sandbox.clock.advance(2.1)

    with pytest.raises(service.LibraryError, match="100 次上限"):
        service.search("supertrend")

    with service._database() as db:
        gate = db.execute("SELECT calls FROM gate WHERE id=1").fetchone()
    assert gate["calls"] == 100
    assert sandbox.network.requests == []


@pytest.mark.parametrize("status_code", [401, 403, 429])
def test_restricted_status_cools_down_before_the_next_network_call(
    sandbox: _Sandbox,
    status_code: int,
) -> None:
    sandbox.network.handler = lambda _request: httpx.Response(status_code)

    with pytest.raises(service.LibraryError, match="暂停在线请求 15 分钟"):
        service.search("supertrend")
    assert len(sandbox.network.requests) == 1

    with pytest.raises(service.LibraryError, match="暂时冷却"):
        service.search("supertrend")
    assert len(sandbox.network.requests) == 1


@pytest.mark.parametrize(
    ("max_sources", "max_total_bytes", "script_ids", "retained_ids"),
    [
        (2, 10_000, ("PUB;A", "PUB;B", "PUB;C"), {"PUB;B", "PUB;C"}),
        (10, 100, ("PUB;A", "PUB;B"), {"PUB;B"}),
    ],
)
def test_source_cache_enforces_count_and_byte_budgets_with_eviction(
    sandbox: _Sandbox,
    monkeypatch,
    max_sources: int,
    max_total_bytes: int,
    script_ids: tuple[str, ...],
    retained_ids: set[str],
) -> None:
    assert service.MAX_SOURCES == 40
    assert service.MAX_TOTAL_SOURCE_BYTES == 6 * 1024 * 1024
    monkeypatch.setattr(service, "MAX_SOURCES", max_sources)
    monkeypatch.setattr(service, "MAX_TOTAL_SOURCE_BYTES", max_total_bytes)
    source_text = "// @version=5\n" + "x" * 46

    def handler(request: httpx.Request) -> httpx.Response:
        if urlsplit(str(request.url)).path.startswith("/scripts/"):
            return _listing([_script(script_id) for script_id in script_ids])
        return _source_payload(source_text, version="5.1")

    sandbox.network.handler = handler
    service.search("supertrend")
    for script_id in script_ids:
        sandbox.clock.advance(2.1)
        fetched = service.source(script_id)
        assert fetched["id"] == script_id
        assert fetched["bytes"] == 60

    for script_id in script_ids:
        if script_id in retained_ids:
            cached = service.source(script_id, offline=True)
            assert cached["cached"] is True
            assert cached["source"] == source_text
        else:
            with pytest.raises(service.LibraryError, match="此版本尚未缓存"):
                service.source(script_id, offline=True)

    with service._database() as db:
        cached_rows = db.execute(
            "SELECT source_bytes FROM scripts WHERE source IS NOT NULL"
        ).fetchall()
    assert len(cached_rows) <= max_sources
    assert sum(row["source_bytes"] for row in cached_rows) <= max_total_bytes
    assert len(sandbox.network.requests) == 1 + len(script_ids)


def test_source_survives_concurrent_catalog_eviction_and_keeps_catalog_bounded(
    sandbox: _Sandbox,
) -> None:
    target_id = "PUB;TARGET"
    source_text = "// @version=5\nplot(close) // CONCURRENT_SOURCE"
    source_started = threading.Event()
    release_source = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        if path == "/scripts/supertrend/":
            return _listing([_script(target_id, name="并发源码目标")])
        if path.startswith("/scripts/rsi/"):
            last_segment = path.rstrip("/").rsplit("/", 1)[-1]
            page = 1 if last_segment == "rsi" else int(last_segment.removeprefix("page-"))
            start = (page - 1) * 24
            items = [_script(f"PUB;B{index:03d}") for index in range(start, start + 24)]
            return _listing(items, next_page="more" if page < 9 else None)
        source_started.set()
        if not release_source.wait(timeout=15):
            raise AssertionError("test did not release the paused source request")
        return _source_payload(source_text, version="5.1")

    sandbox.network.handler = handler
    service.search("supertrend")
    sandbox.clock.advance(2.1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        source_future = pool.submit(service.source, target_id)
        try:
            assert source_started.wait(timeout=5)
            for page in range(1, 10):
                sandbox.clock.advance(2.1)
                result = service.search("rsi", page=page)
                assert result["page"] == page
                assert len(result["items"]) == 24

            with service._database() as db:
                catalog_count = db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
                evicted_target = db.execute(
                    "SELECT 1 FROM scripts WHERE id=?", (target_id,)
                ).fetchone()
            assert catalog_count == 200
            assert evicted_target is None
        finally:
            release_source.set()

        result = source_future.result(timeout=10)

    assert result["id"] == target_id
    assert result["name"] == "并发源码目标"
    assert result["source"] == source_text
    with service._database() as db:
        catalog_count = db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
        restored = db.execute(
            "SELECT source,source_version FROM scripts WHERE id=?", (target_id,)
        ).fetchone()
    assert catalog_count <= 200
    assert restored["source"] == source_text
    assert restored["source_version"] == "5.1"
    assert len(sandbox.network.requests) == 11


def test_listing_cache_hit_restores_evicted_ids_without_replacing_newer_metadata(
    sandbox: _Sandbox,
) -> None:
    returned_id = "PUB;RETURNED"
    newer_id = "PUB;NEWER"
    source_text = "// @version=5\nplot(close) // RESTORED_FROM_CACHED_LISTING"

    def handler(request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        if path == "/scripts/supertrend/":
            return _listing([
                _script(returned_id, name="缓存可恢复脚本"),
                _script(newer_id, name="旧缓存名称", version="3.1"),
            ])
        if path.startswith("/scripts/rsi/"):
            last_segment = path.rstrip("/").rsplit("/", 1)[-1]
            page = 1 if last_segment == "rsi" else int(last_segment.removeprefix("page-"))
            first = (page - 1) * 24
            return _listing(
                [_script(f"PUB;F{index:03d}") for index in range(first, first + 24)],
                next_page="more" if page < 9 else None,
            )
        if path == "/scripts/macd/":
            return _listing([_script(newer_id, name="较新目录名称", version="4.0")])
        return _source_payload(source_text, version="5.1")

    sandbox.network.handler = handler
    first = service.search("supertrend")
    assert first["cached"] is False
    assert [item["id"] for item in first["items"]] == [returned_id, newer_id]
    original_fetched_at = first["fetched_at_ms"]
    listing_key = json.dumps(["supertrend", "", "", "all", 1])
    with service._database() as db:
        original_listing_fetched = db.execute(
            "SELECT fetched FROM listings WHERE key=?", (listing_key,)
        ).fetchone()["fetched"]

    for page in range(1, 10):
        sandbox.clock.advance(2.1)
        result = service.search("rsi", page=page)
        assert result["page"] == page
        assert len(result["items"]) == 24

    with service._database() as db:
        assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 200
        assert db.execute(
            "SELECT 1 FROM scripts WHERE id=?", (returned_id,)
        ).fetchone() is None
        assert db.execute(
            "SELECT 1 FROM scripts WHERE id=?", (newer_id,)
        ).fetchone() is None

    sandbox.clock.advance(2.1)
    refreshed_metadata = service.search("macd")
    assert refreshed_metadata["items"][0]["name"] == "较新目录名称"
    with service._database() as db:
        newer_payload = json.loads(
            db.execute("SELECT payload FROM scripts WHERE id=?", (newer_id,)).fetchone()["payload"]
        )
    assert newer_payload["name"] == "较新目录名称"
    assert newer_payload["version"] == "4.0"
    upstream_count = len(sandbox.network.requests)

    cached = service.search("supertrend")
    assert cached["cached"] is True
    assert cached["fetched_at_ms"] == original_fetched_at
    assert len(sandbox.network.requests) == upstream_count
    assert [item["id"] for item in cached["items"]] == [returned_id, newer_id]
    with service._database() as db:
        fetched_after_hit = db.execute(
            "SELECT fetched FROM listings WHERE key=?", (listing_key,)
        ).fetchone()["fetched"]
        catalog_count = db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
        restored_payload = db.execute(
            "SELECT payload FROM scripts WHERE id=?", (returned_id,)
        ).fetchone()["payload"]
        still_newer_payload = db.execute(
            "SELECT payload FROM scripts WHERE id=?", (newer_id,)
        ).fetchone()["payload"]
    assert fetched_after_hit == original_listing_fetched
    assert catalog_count <= 200
    assert json.loads(restored_payload)["name"] == "缓存可恢复脚本"
    assert json.loads(still_newer_payload)["name"] == "较新目录名称"
    assert json.loads(still_newer_payload)["version"] == "4.0"

    sandbox.clock.advance(2.1)
    restored_source = service.source(returned_id)
    assert restored_source["id"] == returned_id
    assert restored_source["name"] == "缓存可恢复脚本"
    assert restored_source["source"] == source_text
    assert restored_source["version"] == "5.1"
    assert len(sandbox.network.requests) == upstream_count + 1
    with service._database() as db:
        assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] <= 200
