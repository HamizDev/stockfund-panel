from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.custom.jevos.routes as jevos_routes
import app.custom.jevos.service as jevos_service
from app.custom.jevos.client import JevosError, ModelInfo

_FINGERPRINT_A = "a" * 64
_FINGERPRINT_B = "b" * 64
_MODEL_A = ModelInfo(model="jevos-v5", fingerprint=_FINGERPRINT_A)
_MODEL_B = ModelInfo(model="jevos-v5", fingerprint=_FINGERPRINT_B)


class FakeClient:
    def __init__(self, *, health_error: JevosError | None = None, block_judge: bool = False):
        self.identity = _MODEL_A
        self.health_error = health_error
        self.block_judge = block_judge
        self.health_calls = 0
        self.judgements: list[dict] = []
        self.judge_started = threading.Event()
        self.release_judge = threading.Event()
        if not block_judge:
            self.release_judge.set()
        self._lock = threading.Lock()
        self._next_error: JevosError | None = None

    def health(self) -> ModelInfo:
        with self._lock:
            self.health_calls += 1
            error = self.health_error
            identity = self.identity
        if error is not None:
            raise error
        return identity

    def judge(self, state: dict, questions: dict[str, str], identity: ModelInfo) -> dict[str, float]:
        with self._lock:
            self.judgements.append({
                "state": copy.deepcopy(state),
                "questions": copy.deepcopy(questions),
                "identity": identity,
            })
            error = self._next_error
            self._next_error = None
            self.judge_started.set()
        if self.block_judge:
            self.release_judge.wait(timeout=5)
        if error is not None:
            raise error

        values = {
            "positive": 0.9,
            "risk": 0.1,
            "material": 0.9,
            "supports_signal": 0.9,
            "conflict": 0.1,
            "sufficient": 0.9,
        }
        return {name: values[name] for name in questions}

    def set_identity(self, identity: ModelInfo) -> None:
        with self._lock:
            self.identity = identity

    def fail_next_judge(self, error: JevosError) -> None:
        with self._lock:
            self._next_error = error


@pytest.fixture
def make_service():
    services: list[jevos_service.JevosService] = []

    def create(data_dir: Path, client: FakeClient) -> jevos_service.JevosService:
        service = jevos_service.JevosService(data_dir, client=client)
        services.append(service)
        return service

    yield create

    for service in services:
        service.stop()


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "timed out waiting for the Jevos service state"


def _start_ready(service: jevos_service.JevosService) -> None:
    service.start()
    _wait_until(lambda: service.snapshot()["status"] == "ready")


def _news(
    *,
    identifier: str = "article-1",
    title: str = "Policy support was announced",
    summary: str = "A public company reported an event.",
    symbol: str = "BTC-USDT-SWAP",
    name: str = "Bitcoin",
) -> dict:
    return {
        "id": identifier,
        "title": title,
        "summary": summary,
        "published_at": "2026-10-17T09:00:00+08:00",
        "associations": [{"symbol": symbol, "name": name}],
    }


def _wait_news(service: jevos_service.JevosService, identifier: str, predicate=lambda _row: True) -> dict:
    found: dict | None = None

    def ready() -> bool:
        nonlocal found
        row = service.snapshot()["news"].get(identifier)
        if row is not None and predicate(row):
            found = row
            return True
        return False

    _wait_until(ready)
    assert found is not None
    return found


def _wait_persisted(service: jevos_service.JevosService, row: dict) -> None:
    def saved() -> bool:
        try:
            payload = json.loads(service.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return row["input_key"] in payload.get("records", {})

    _wait_until(saved)


def _protected_files(data_dir: Path) -> tuple[Path, bytes, Path, bytes]:
    news_path = data_dir / "news" / "snapshot.json"
    ledger_path = data_dir / "user_data" / "crypto_paper.json"
    news_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    news_bytes = b"existing-news-sentinel"
    ledger_bytes = b"existing-crypto-ledger-sentinel"
    news_path.write_bytes(news_bytes)
    ledger_path.write_bytes(ledger_bytes)
    return news_path, news_bytes, ledger_path, ledger_bytes


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"positive": 0.8, "risk": 0.25}, "positive_clue"),
        ({"positive": 0.8, "risk": 0.8}, "mixed"),
        ({"positive": 0.5, "risk": 0.8}, "risk_clue"),
        ({"positive": 0.5001, "risk": 0.8}, "unclear"),
        ({"positive": 0.8, "risk": 0.2501}, "unclear"),
        ({"positive": 0.7999, "risk": 0.1}, "unclear"),
    ],
)
def test_news_label_uses_conservative_inclusive_thresholds(values: dict, expected: str):
    assert jevos_service.news_label(values) == expected


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"supports_signal": 1, "conflict": 1, "sufficient": 0.2}, "insufficient"),
        ({"supports_signal": 0.1, "conflict": 0.8, "sufficient": 0.21}, "conflict"),
        ({"supports_signal": 0.8, "conflict": 0.2, "sufficient": 0.8}, "support"),
        ({"supports_signal": 0.8, "conflict": 0.2001, "sufficient": 0.8}, "wait"),
        ({"supports_signal": 0.7999, "conflict": 0.1, "sufficient": 0.9}, "wait"),
        ({"supports_signal": 1, "conflict": 0.1, "sufficient": 0.2001}, "wait"),
    ],
)
def test_strategy_label_requires_sufficient_and_unconflicted_evidence(values: dict, expected: str):
    assert jevos_service.strategy_label(values) == expected


def test_candle_summary_calculates_bounded_numeric_facts_from_closed_bars():
    bars = [{"close": float(100 + index)} for index in range(210)]

    summary = jevos_service.candle_summary(bars)

    assert summary["closed_candle_count"] == 200
    assert summary["last_close"] == 309.0
    assert summary["ema8"] == pytest.approx(305.5)
    assert summary["ema24"] == pytest.approx(297.50000072)
    assert summary["trend_alignment"] == "up"
    assert summary["last_return_pct"] == 0.3247
    assert summary["five_bar_return_pct"] == 1.6447
    assert summary["close_range20_pct"] == 6.5517


def test_candle_summary_rejects_short_or_invalid_history():
    with pytest.raises(ValueError, match="insufficient closed candles"):
        jevos_service.candle_summary([{"close": 1.0}] * 23)

    for invalid in (0.0, -1.0, float("nan"), float("inf")):
        bars = [{"close": 100.0}] * 23 + [{"close": invalid}]
        with pytest.raises(ValueError, match="invalid candle close"):
            jevos_service.candle_summary(bars)


def test_pending_queue_and_latest_index_stay_bounded_without_a_worker(tmp_path: Path, make_service):
    service = make_service(tmp_path, FakeClient())

    for index in range(jevos_service.MAX_QUEUE * 2):
        service.offer("news", f"article-{index}", {"text": f"headline {index}"})

    assert len(service.pending) == jevos_service.MAX_QUEUE
    assert len(service.queues["news"]) == jevos_service.MAX_QUEUE
    assert len(service.latest["news"]) <= jevos_service.MAX_QUEUE
    assert set(service.latest["news"].values()) <= service.pending


@pytest.mark.parametrize("change", ["content", "association"])
def test_cached_article_outside_work_cap_is_invalidated_when_current_input_changes(
    tmp_path: Path, make_service, change,
):
    client = FakeClient()
    service = make_service(tmp_path, client)
    _start_ready(service)
    original = _news()
    service.offer_news([original], ["BTC-USDT-SWAP"])
    _wait_news(service, original["id"])
    others = [_news(identifier=f"other-{index}") for index in range(jevos_service.MAX_NEWS)]
    edited = _news(title="A changed report") if change == "content" else _news(symbol="ETH-USDT-SWAP", name="Ethereum")
    service.offer_news([*others, edited], ["BTC-USDT-SWAP"])
    assert original["id"] not in service.snapshot()["news"]
    _wait_until(lambda: service.snapshot()["queued"] == 0)
    assert original["id"] not in service.snapshot()["news"]


def _cached_review(index: int) -> tuple[str, dict]:
    identifier = f"cached-{index:03d}"
    key = hashlib.sha256(f"seed-{index}".encode()).hexdigest()
    review = jevos_service.Review(
        kind="news",
        id=identifier,
        input_key=key,
        model="jevos-v5",
        fingerprint=_FINGERPRINT_A,
        generated_at_ms=index + 1,
        label="unclear",
        original_signal=None,
        probabilities={"positive": 0.4, "risk": 0.4, "material": 0.4},
    )
    return key, review.model_dump()


def test_completed_cache_stays_bounded_when_a_new_review_is_written(tmp_path: Path, make_service):
    cache_path = tmp_path / "jevos" / "advisories.json"
    cache_path.parent.mkdir(parents=True)
    seeded = dict(_cached_review(index) for index in range(jevos_service.MAX_RECORDS))
    oldest_key = next(iter(seeded))
    cache_path.write_text(json.dumps({
        "version": jevos_service.VERSION,
        "records": seeded,
        "latest": {
            "news": {row["id"]: key for key, row in seeded.items()},
            "strategy": {},
        },
    }), encoding="utf-8")

    client = FakeClient()
    service = make_service(tmp_path, client)
    assert len(service.records) == jevos_service.MAX_RECORDS
    _start_ready(service)
    service.offer_news([_news(identifier="new-article")], ["BTC-USDT-SWAP"])
    row = _wait_news(service, "new-article")
    _wait_persisted(service, row)

    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert row["input_key"] in service.records
    assert len(service.records) == jevos_service.MAX_RECORDS
    assert len(saved["records"]) == jevos_service.MAX_RECORDS
    assert oldest_key not in service.records


def test_persisted_cache_hit_after_restart_does_not_repeat_inference(tmp_path: Path, make_service):
    item = _news()
    client = FakeClient()
    first = make_service(tmp_path, client)
    _start_ready(first)
    first.offer_news([item], ["BTC-USDT-SWAP"])
    row = _wait_news(first, "article-1")
    _wait_persisted(first, row)
    first.stop()
    assert len(client.judgements) == 1

    second = make_service(tmp_path, client)
    _start_ready(second)
    second.offer_news([item], ["BTC-USDT-SWAP"])

    assert second.snapshot()["queued"] == 0
    assert second.snapshot()["news"]["article-1"]["fingerprint"] == _FINGERPRINT_A
    assert len(client.judgements) == 1


def test_news_content_association_and_model_changes_recompute(tmp_path: Path, make_service):
    client = FakeClient()
    service = make_service(tmp_path, client)
    _start_ready(service)

    item = _news()
    service.offer_news([item], ["BTC-USDT-SWAP"])
    first = _wait_news(service, "article-1")
    service.offer_news([item], ["BTC-USDT-SWAP"])
    _wait_until(lambda: service.snapshot()["queued"] == 0)
    assert service.snapshot()["queued"] == 0
    assert len(client.judgements) == 1

    changed_content = _news(title="A different policy report")
    service.offer_news([changed_content], ["BTC-USDT-SWAP"])
    second = _wait_news(service, "article-1", lambda row: row["input_key"] != first["input_key"])
    assert len(client.judgements) == 2

    changed_association = _news(title="A different policy report", symbol="ETH-USDT-SWAP", name="Ethereum")
    service.offer_news([changed_association], ["ETH-USDT-SWAP"])
    third = _wait_news(service, "article-1", lambda row: row["input_key"] != second["input_key"])
    assert third["related_names"] == ["Ethereum"]
    assert len(client.judgements) == 3

    client.set_identity(_MODEL_B)
    with service.condition:
        service.next_health = 0.0
        service.condition.notify_all()
    _wait_until(lambda: service.snapshot()["fingerprint"] == _FINGERPRINT_B)
    service.offer_news([changed_association], ["ETH-USDT-SWAP"])
    fourth = _wait_news(
        service,
        "article-1",
        lambda row: row["input_key"] == third["input_key"] and row["fingerprint"] == _FINGERPRINT_B,
    )

    assert fourth["model"] == "jevos-v5"
    assert len(client.judgements) == 4


def test_offline_service_keeps_news_and_ledger_untouched(tmp_path: Path, make_service):
    protected = _protected_files(tmp_path)
    client = FakeClient(health_error=JevosError("offline"))
    service = make_service(tmp_path, client)
    service.start()
    _wait_until(lambda: service.snapshot()["status"] == "offline")
    service.offer_news([_news()], ["BTC-USDT-SWAP"])

    assert service.snapshot()["queued"] == 1
    assert client.judgements == []
    assert not service.path.exists()
    assert tuple(path.read_bytes() for path in (protected[0], protected[2])) == (protected[1], protected[3])


def test_invalid_response_preserves_prior_cache_and_never_touches_source_files(tmp_path: Path, make_service):
    protected = _protected_files(tmp_path)
    client = FakeClient()
    service = make_service(tmp_path, client)
    _start_ready(service)
    service.offer_news([_news(identifier="good-article")], ["BTC-USDT-SWAP"])
    row = _wait_news(service, "good-article")
    _wait_persisted(service, row)
    cache_before = service.path.read_bytes()

    client.fail_next_judge(JevosError("invalid_response"))
    service.offer_news([_news(identifier="bad-article")], ["BTC-USDT-SWAP"])
    _wait_until(lambda: service.snapshot()["status"] == "invalid_response" and service.snapshot()["queued"] == 0)

    assert len(service.records) == 1
    assert service.path.read_bytes() == cache_before
    assert tuple(path.read_bytes() for path in (protected[0], protected[2])) == (protected[1], protected[3])


def test_stop_during_inference_prevents_cache_write(tmp_path: Path, make_service):
    client = FakeClient(block_judge=True)
    service = make_service(tmp_path, client)
    _start_ready(service)
    service.offer_news([_news()], ["BTC-USDT-SWAP"])
    assert client.judge_started.wait(timeout=3)

    stopper = threading.Thread(target=service.stop)
    stopper.start()
    _wait_until(service.stop_event.is_set)
    client.release_judge.set()
    stopper.join(timeout=3)

    assert not stopper.is_alive()
    assert service.records == {}
    assert not service.path.exists()


def test_offer_strategy_copies_only_public_fields_and_never_waits_for_inference(
    tmp_path: Path, make_service, monkeypatch,
):
    client = FakeClient(block_judge=True)
    service = make_service(tmp_path, client)
    key = str(tmp_path.resolve())
    monkeypatch.setitem(jevos_service._SERVICES, key, service)
    _start_ready(service)

    bars = [{"close": float(index)} for index in range(1, 25)]
    bot = {
        "id": "bot-1",
        "symbol": "BTC-USDT-SWAP",
        "strategy_id": "ma-cross",
        "interval": "5m",
        "last_signal": "long",
        "last_bar_ms": 1_792_000_000_000,
        "credentials": {"api_key": "credential-sentinel"},
        "fingerprint": "account-fingerprint-sentinel",
        "orders": [{"clOrdId": "order-sentinel"}],
        "position": {"quantity": "position-sentinel"},
    }
    offered = threading.Event()

    def offer() -> None:
        jevos_service.offer_strategy(tmp_path, bot, bars)
        offered.set()

    offer_thread = threading.Thread(target=offer)
    offer_thread.start()
    try:
        assert client.judge_started.wait(timeout=3)
        assert offered.wait(timeout=1)
        assert not client.release_judge.is_set()
    finally:
        client.release_judge.set()
        offer_thread.join(timeout=3)

    assert not offer_thread.is_alive()
    state = client.judgements[0]["state"]
    assert set(state) == {
        "symbol", "strategy_id", "interval", "last_signal", "last_bar_ms", "closed_candles",
    }
    assert state["symbol"] == "BTC-USDT-SWAP"
    assert state["last_signal"] == "long"
    assert state["closed_candles"]["closed_candle_count"] == 24
    serialized = json.dumps(state)
    assert "credential-sentinel" not in serialized
    assert "account-fingerprint-sentinel" not in serialized
    assert "order-sentinel" not in serialized
    assert "position-sentinel" not in serialized


def test_status_route_is_read_only_and_uses_only_the_requested_data_dir(tmp_path: Path, monkeypatch):
    status = {
        "mode": "shadow",
        "status": "ready",
        "model": "jevos-v5",
        "fingerprint": _FINGERPRINT_A,
        "last_check_ms": 1_792_000_000_000,
        "queued": 0,
        "completed": 0,
        "news": {},
        "strategies": {},
        "strategy_history": [],
    }
    calls: list[Path] = []

    class StubService:
        def snapshot(self) -> dict:
            return copy.deepcopy(status)

    def fake_get_service(data_dir: Path) -> StubService:
        calls.append(data_dir)
        return StubService()

    monkeypatch.setattr(jevos_routes, "get_service", fake_get_service)
    app = FastAPI()
    app.include_router(jevos_routes.build_router())
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))

    response = TestClient(app).get("/api/custom/jevos/status")

    assert response.status_code == 200
    assert response.json() == status
    assert calls == [tmp_path]
    assert not (tmp_path / "jevos").exists()
