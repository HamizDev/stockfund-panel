from __future__ import annotations

import json
import math

import httpx
import pytest

from app.custom.jevos.client import ChoiceResult, JevosError, LocalJevosClient, ModelInfo

_FINGERPRINT = "a" * 64
_IDENTITY = ModelInfo(model="jevos-v5", fingerprint=_FINGERPRINT)
_QUESTIONS = {"positive": "Does the evidence support a positive classification?"}
_CHOICE_QUESTIONS = {
    "direction": {
        "type": "choice",
        "instructions": "Classify the stated effect, without guessing beyond the text.",
        "criteria": {
            "positive": "The text describes a clearly favorable effect.",
            "negative": "The text describes a clearly harmful effect.",
        },
    },
}


def _health(**overrides):
    return {
        "status": "ready",
        "model": "jevos-v5",
        "fingerprint": _FINGERPRINT,
        "engine": "must-not-be-returned",
        "model_dir": "must-not-be-returned",
        **overrides,
    }


def _answer(**overrides):
    return {
        "model": "jevos-v5",
        "answers": {"positive": {"type": "noul", "noul": 0.75}},
        "usage": {"input_tokens": 10, "output_tokens": 0},
        **overrides,
    }


def _choice_answer(**overrides):
    return {
        "model": "jevos-v5",
        "answers": {
            "direction": {
                "type": "choice",
                "choice": "positive",
                "probabilities": {"positive": 0.8, "negative": 0.2},
                "confidence": 0.75,
            },
        },
        **overrides,
    }


def _client(handler):
    requests = []

    def capture(request):
        requests.append(request)
        return handler(request)

    return LocalJevosClient(httpx.MockTransport(capture)), requests


def _raise_for(category: str, client: LocalJevosClient, method: str = "health"):
    with pytest.raises(JevosError) as caught:
        if method == "health":
            client.health()
        elif method == "choose":
            client.choose({"untrusted_news_text": "Example"}, _CHOICE_QUESTIONS, _IDENTITY)
        else:
            client.judge({"price": 1.0}, _QUESTIONS, _IDENTITY)
    assert caught.value.category == category
    assert str(caught.value) in {
        "Jevos 本机服务不可用。",
        "Jevos 本机服务尚未就绪。",
        "Jevos 本机服务返回数据无效。",
    }
    return caught.value


def test_health_returns_only_validated_model_identity():
    client, requests = _client(lambda _request: httpx.Response(200, json=_health()))

    identity = client.health()

    assert identity == _IDENTITY
    assert [(request.method, str(request.url)) for request in requests] == [
        ("GET", "http://127.0.0.1:8017/health"),
    ]
    assert not hasattr(identity, "engine")
    assert not hasattr(identity, "model_dir")


def test_response_deadline_stops_a_slow_drip_without_waiting_for_a_full_chunk(monkeypatch):
    from app.custom.jevos import client as module

    clock = [0.0]
    emitted = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                clock[0] += 8
                emitted.append(True)
                yield b" "

    client, _ = _client(lambda _request: httpx.Response(200, stream=SlowStream()))
    _raise_for("offline", client)
    assert len(emitted) == 2


@pytest.mark.parametrize("kind", ["deep", "integer"])
def test_pathological_json_is_a_safe_error_instead_of_killing_the_worker(kind):
    # Python 3.13's C decoder limit differs from sys.getrecursionlimit().
    depth = 40_000
    raw_value = b'[' * depth + b'0' + b']' * depth if kind == "deep" else b'9' * 5000
    raw = json.dumps(_health()).encode()[:-1] + b',"extra":' + raw_value + b'}'
    client, _ = _client(lambda _request: httpx.Response(200, content=raw))
    _raise_for("invalid_response", client)


def test_judge_posts_bounded_noul_schema_to_fixed_loopback_endpoint():
    client, requests = _client(lambda _request: httpx.Response(200, json=_answer()))

    result = client.judge(
        {"untrusted_news_text": "The company reported a new product."},
        {"positive": "Does the evidence support a positive classification?"},
        _IDENTITY,
    )

    assert result == {"positive": 0.75}
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8017/v1/systemone"
    assert not any(key.lower() in {"authorization", "x-api-key"} for key in request.headers)
    assert json.loads(request.content) == {
        "model": "jev-latest",
        "state": {"untrusted_news_text": "The company reported a new product."},
        "questions": {
            "positive": {
                "type": "noul",
                "instructions": "Does the evidence support a positive classification?",
            },
        },
    }


def test_choose_posts_bounded_choice_schema_and_returns_validated_probabilities():
    client, requests = _client(lambda _request: httpx.Response(200, json=_choice_answer()))

    result = client.choose(
        {"untrusted_news_text": "公司净利润增长120%。"},
        _CHOICE_QUESTIONS,
        _IDENTITY,
    )

    assert result == {
        "direction": ChoiceResult(
            choice="positive",
            probabilities={"positive": 0.8, "negative": 0.2},
            confidence=0.75,
        ),
    }
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8017/v1/systemone"
    assert not any(key.lower() in {"authorization", "x-api-key"} for key in request.headers)
    assert json.loads(request.content) == {
        "model": "jev-latest",
        "state": {"untrusted_news_text": "公司净利润增长120%。"},
        "questions": _CHOICE_QUESTIONS,
    }


def test_choose_accepts_a_tied_most_likely_choice():
    client, _ = _client(lambda _request: httpx.Response(200, json=_choice_answer(answers={
        "direction": {
            "type": "choice",
            "choice": "negative",
            "probabilities": {"positive": 0.5, "negative": 0.5},
            "confidence": 0.5,
        },
    })))

    result = client.choose({}, _CHOICE_QUESTIONS, _IDENTITY)

    assert result["direction"].choice == "negative"


@pytest.mark.parametrize(
    "payload",
    [
        _choice_answer(answers={}),
        _choice_answer(answers={
            "direction": _choice_answer()["answers"]["direction"],
            "unexpected": _choice_answer()["answers"]["direction"],
        }),
        _choice_answer(model="jevos-v4"),
        _choice_answer(answers={"direction": {**_choice_answer()["answers"]["direction"], "type": "noul"}}),
        _choice_answer(answers={"direction": {
            **_choice_answer()["answers"]["direction"], "choice": "unknown",
        }}),
        _choice_answer(answers={"direction": {
            **_choice_answer()["answers"]["direction"],
            "probabilities": {"positive": 0.8},
        }}),
        _choice_answer(answers={"direction": {
            **_choice_answer()["answers"]["direction"],
            "probabilities": {"positive": 0.7, "negative": 0.3, "neutral": 0.0},
        }}),
        _choice_answer(answers={"direction": {
            **_choice_answer()["answers"]["direction"], "surprise": "not allowed",
        }}),
        _choice_answer(answers={"direction": {
            **_choice_answer()["answers"]["direction"],
            "choice": "negative",
            "probabilities": {"positive": 0.8, "negative": 0.2},
        }}),
        _choice_answer(answers={"direction": {
            **_choice_answer()["answers"]["direction"],
            "probabilities": {"positive": 0.7, "negative": 0.2},
        }}),
    ],
)
def test_choose_rejects_wrong_model_question_answer_or_choice_contract(payload):
    client, _ = _client(lambda _request: httpx.Response(200, json=payload))
    _raise_for("invalid_response", client, "choose")


@pytest.mark.parametrize(
    "value",
    [True, False, float("nan"), float("inf"), float("-inf"), -0.01, 1.01, "0.5", None],
)
def test_choose_rejects_invalid_probability_and_confidence_values(value):
    base = _choice_answer()["answers"]["direction"]
    for answer in (
        {**base, "probabilities": {"positive": value, "negative": 0.2}},
        {**base, "confidence": value},
    ):
        client, _ = _client(lambda _request, answer=answer: httpx.Response(
            200, json=_choice_answer(answers={"direction": answer}),
        ))
        _raise_for("invalid_response", client, "choose")


def test_choose_rejects_invalid_identity_and_state_before_http():
    client, requests = _client(lambda _request: pytest.fail("Invalid input must fail before HTTP"))
    invalid_identity = ModelInfo(model="wrong-model", fingerprint=_FINGERPRINT)
    with pytest.raises(JevosError) as caught:
        client.choose({}, _CHOICE_QUESTIONS, invalid_identity)
    assert caught.value.category == "invalid_response"
    with pytest.raises(JevosError):
        client.choose({"text": "x" * (16 * 1024)}, _CHOICE_QUESTIONS, _IDENTITY)
    with pytest.raises(JevosError):
        client.choose({"value": math.nan}, _CHOICE_QUESTIONS, _IDENTITY)
    assert requests == []


def test_choose_rejects_invalid_question_shapes_and_limits_before_http():
    oversized_questions = {
        f"question_{question_index}": {
            "type": "choice",
            "instructions": "Choose one.",
            "criteria": {f"option_{option_index}": "x" * 1000 for option_index in range(12)},
        }
        for question_index in range(3)
    }
    invalid_questions = [
        {},
        {str(index): _CHOICE_QUESTIONS["direction"] for index in range(9)},
        {"bad name": _CHOICE_QUESTIONS["direction"]},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "extra": "no"}},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "type": "noul"}},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "instructions": " "}},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "instructions": "x" * 1001}},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "criteria": {"only": "one"}}},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "criteria": {
            f"option_{index}": "valid" for index in range(13)
        }}},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "criteria": {
            "bad option": "valid", "other": "also valid",
        }}},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "criteria": {
            "positive": " ", "negative": "valid",
        }}},
        {"direction": {**_CHOICE_QUESTIONS["direction"], "criteria": {
            "positive": "x" * 1001, "negative": "valid",
        }}},
        oversized_questions,
    ]
    client, requests = _client(lambda _request: pytest.fail("Invalid questions must fail before HTTP"))

    for questions in invalid_questions:
        with pytest.raises(JevosError) as caught:
            client.choose({}, questions, _IDENTITY)
        assert caught.value.category == "invalid_response"

    assert requests == []


@pytest.mark.parametrize("method", ["health", "judge", "choose"])
def test_network_and_timeout_errors_are_fixed_and_do_not_leak(method):
    def offline(request):
        raise httpx.ConnectError("FAKE_NETWORK_SECRET", request=request)

    client, _ = _client(offline)
    caught = _raise_for("offline", client, method)
    assert "FAKE_NETWORK_SECRET" not in str(caught)

    def timeout(request):
        raise httpx.ReadTimeout("FAKE_TIMEOUT_SECRET", request=request)

    client, _ = _client(timeout)
    caught = _raise_for("offline", client, method)
    assert "FAKE_TIMEOUT_SECRET" not in str(caught)


def test_non_ready_and_server_error_have_distinct_safe_categories():
    client, _ = _client(lambda _request: httpx.Response(200, json=_health(status="loading")))
    _raise_for("not_ready", client)

    client, _ = _client(lambda _request: httpx.Response(503, text="FAKE_SERVER_SECRET"))
    caught = _raise_for("offline", client)
    assert "FAKE_SERVER_SECRET" not in str(caught)


@pytest.mark.parametrize(
    "payload",
    [
        _health(status="ready", model="engine-secret"),
        _health(status="ready", model="jevos-" + "a" * 80),
        _health(status="ready", fingerprint="not-a-fingerprint"),
        _health(status="ready", fingerprint="a" * 63),
    ],
)
def test_health_rejects_unsafe_or_malformed_identity(payload):
    client, _ = _client(lambda _request: httpx.Response(200, json=payload))
    _raise_for("invalid_response", client)


def test_health_rejects_redirect_without_following_it():
    client, requests = _client(lambda _request: httpx.Response(
        302, headers={"Location": "https://external.invalid/steal"},
    ))

    _raise_for("invalid_response", client)

    assert len(requests) == 1
    assert str(requests[0].url) == "http://127.0.0.1:8017/health"


class _CountedChunks(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.bytes_yielded = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.bytes_yielded += len(chunk)
            yield chunk

    def close(self):
        pass


def test_oversized_stream_is_rejected_without_buffering_the_full_body():
    stream = _CountedChunks([b"x" * 8192 for _ in range(40)])
    client, _ = _client(lambda _request: httpx.Response(200, stream=stream))

    _raise_for("invalid_response", client)

    assert stream.bytes_yielded <= 256 * 1024
    assert stream.bytes_yielded < 40 * 8192


def test_body_at_the_limit_is_accepted_when_its_length_is_declared():
    body = json.dumps(_health()).encode("utf-8")
    body += b" " * (256 * 1024 - len(body))
    stream = _CountedChunks([body[index:index + 8192] for index in range(0, len(body), 8192)])
    client, _ = _client(lambda _request: httpx.Response(
        200,
        headers={"Content-Length": str(256 * 1024)},
        stream=stream,
    ))

    assert client.health() == _IDENTITY
    assert stream.bytes_yielded == 256 * 1024


def test_declared_oversized_body_is_rejected_before_reading_stream():
    stream = _CountedChunks([b"x" * 8192])
    client, _ = _client(lambda _request: httpx.Response(
        200,
        headers={"Content-Length": str(256 * 1024 + 1)},
        stream=stream,
    ))

    _raise_for("invalid_response", client)

    assert stream.bytes_yielded == 0


@pytest.mark.parametrize("content", [b"not-json-secret", b"\xff\xfe"])
def test_malformed_json_is_rejected_without_echoing_body(content):
    client, _ = _client(lambda _request: httpx.Response(200, content=content))
    caught = _raise_for("invalid_response", client)
    assert "not-json-secret" not in str(caught)


def test_judge_rejects_missing_or_extra_answers_and_wrong_model():
    responses = [
        _answer(answers={}),
        _answer(answers={
            "positive": {"type": "noul", "noul": 0.5},
            "unexpected": {"type": "noul", "noul": 0.2},
        }),
        _answer(model="jevos-v4"),
    ]
    for payload in responses:
        client, _ = _client(lambda _request, payload=payload: httpx.Response(200, json=payload))
        _raise_for("invalid_response", client, "judge")


@pytest.mark.parametrize(
    "value",
    [True, False, float("nan"), float("inf"), float("-inf"), -0.01, 1.01, "0.5", None],
)
def test_judge_rejects_boolean_nonfinite_non_numeric_and_out_of_range_noul(value):
    client, _ = _client(lambda _request: httpx.Response(200, json=_answer(answers={
        "positive": {"type": "noul", "noul": value},
    })))

    _raise_for("invalid_response", client, "judge")


def test_judge_rejects_wrong_answer_type_and_identity():
    client, _ = _client(lambda _request: httpx.Response(200, json=_answer(answers={
        "positive": {"type": "score", "noul": 0.5},
    })))
    _raise_for("invalid_response", client, "judge")

    client, requests = _client(lambda _request: pytest.fail("Invalid identity must fail before HTTP"))
    invalid_identity = ModelInfo(model="wrong-model", fingerprint=_FINGERPRINT)
    with pytest.raises(JevosError) as caught:
        client.judge({"price": 1}, _QUESTIONS, invalid_identity)
    assert caught.value.category == "invalid_response"
    assert requests == []


def test_judge_rejects_oversized_state_and_invalid_question_limits_before_http():
    client, requests = _client(lambda _request: pytest.fail("Invalid input must fail before HTTP"))
    with pytest.raises(JevosError):
        client.judge({"text": "x" * (16 * 1024)}, _QUESTIONS, _IDENTITY)
    with pytest.raises(JevosError):
        client.judge({}, {}, _IDENTITY)
    with pytest.raises(JevosError):
        client.judge({}, {str(index): "valid" for index in range(9)}, _IDENTITY)
    with pytest.raises(JevosError):
        client.judge({}, {"positive": "x" * 1001}, _IDENTITY)
    with pytest.raises(JevosError):
        client.judge({"value": math.nan}, _QUESTIONS, _IDENTITY)
    assert requests == []


def test_proxy_environment_cannot_override_loopback_transport(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("NO_PROXY", "")
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=_health()))
    actual_client = httpx.Client
    options = {}

    def client_with_recorded_options(**kwargs):
        options.update(kwargs)
        return actual_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", client_with_recorded_options)
    identity = LocalJevosClient(transport=transport).health()

    assert identity == _IDENTITY
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False
    assert options["transport"] is transport
    assert options["timeout"].connect == 1.0
    assert options["timeout"].read == 10.0
