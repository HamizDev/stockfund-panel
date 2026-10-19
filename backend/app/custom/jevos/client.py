"""Bounded local HTTP client for the Jevos inference service."""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from typing import Literal

import httpx

_BASE_URL = "http://127.0.0.1:8017"
_HEALTH_PATH = "/health"
_SYSTEMONE_PATH = "/v1/systemone"
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_STATE_BYTES = 16 * 1024
_MAX_QUESTIONS_BYTES = 24 * 1024
_MAX_QUESTIONS = 8
_MAX_INSTRUCTIONS_CHARS = 1000
_MAX_CHOICES = 12
_MAX_CHOICE_DESCRIPTION_CHARS = 1000
_CHOICE_SUM_TOLERANCE = 1e-5
_CHOICE_ARGMAX_TOLERANCE = 1e-8
_MODEL_NAME = re.compile(r"jevos-[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
_FINGERPRINT = re.compile(r"[0-9a-fA-F]{64}\Z")
_QUESTION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_ERROR_MESSAGES = {
    "offline": "Jevos 本机服务不可用。",
    "not_ready": "Jevos 本机服务尚未就绪。",
    "invalid_response": "Jevos 本机服务返回数据无效。",
}


@dataclass(frozen=True, slots=True)
class ModelInfo:
    model: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ChoiceResult:
    choice: str
    probabilities: dict[str, float]
    confidence: float


class JevosError(ValueError):
    """A safe, fixed-message error from the local Jevos adapter."""

    category: Literal["offline", "not_ready", "invalid_response"]

    def __init__(self, category: Literal["offline", "not_ready", "invalid_response"]):
        safe_category = category if category in _ERROR_MESSAGES else "invalid_response"
        self.category = safe_category  # type: ignore[assignment]
        super().__init__(_ERROR_MESSAGES[safe_category])


class LocalJevosClient:
    """Call only the loopback Jevos service, with bounded inputs and responses."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._transport = transport

    def health(self) -> ModelInfo:
        payload = self._request("GET", _HEALTH_PATH)
        if payload.get("status") != "ready":
            raise JevosError("not_ready")

        model = payload.get("model")
        fingerprint = payload.get("fingerprint")
        if (
            not isinstance(model, str)
            or _MODEL_NAME.fullmatch(model) is None
            or not isinstance(fingerprint, str)
            or _FINGERPRINT.fullmatch(fingerprint) is None
        ):
            raise JevosError("invalid_response")
        return ModelInfo(model=model, fingerprint=fingerprint.lower())

    def judge(
        self,
        state: dict,
        questions: dict[str, str],
        identity: ModelInfo,
    ) -> dict[str, float]:
        _validate_identity(identity)
        state_json = _serialize_state(state)
        _validate_questions(questions)

        request_questions = {
            name: {"type": "noul", "instructions": instructions}
            for name, instructions in questions.items()
        }
        payload = {
            "model": "jev-latest",
            "state": json.loads(state_json),
            "questions": request_questions,
        }
        response = self._request("POST", _SYSTEMONE_PATH, payload)

        if response.get("model") != identity.model:
            raise JevosError("invalid_response")
        answers = response.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise JevosError("invalid_response")

        result: dict[str, float] = {}
        for name in questions:
            answer = answers.get(name)
            if not isinstance(answer, dict) or answer.get("type") != "noul":
                raise JevosError("invalid_response")
            value = answer.get("noul")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise JevosError("invalid_response")
            try:
                number = float(value)
            except (OverflowError, ValueError):
                raise JevosError("invalid_response") from None
            if not math.isfinite(number) or not 0.0 <= number <= 1.0:
                raise JevosError("invalid_response")
            result[name] = number
        return result

    def choose(
        self,
        state: dict,
        questions: dict[str, dict],
        identity: ModelInfo,
    ) -> dict[str, ChoiceResult]:
        _validate_identity(identity)
        state_json = _serialize_state(state)
        request_questions = json.loads(_serialize_choice_questions(questions))
        payload = {
            "model": "jev-latest",
            "state": json.loads(state_json),
            "questions": request_questions,
        }
        response = self._request("POST", _SYSTEMONE_PATH, payload)

        if response.get("model") != identity.model:
            raise JevosError("invalid_response")
        answers = response.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(request_questions):
            raise JevosError("invalid_response")

        result: dict[str, ChoiceResult] = {}
        for name, question in request_questions.items():
            answer = answers.get(name)
            criteria = question["criteria"]
            if (
                not isinstance(answer, dict)
                or set(answer) != {"type", "choice", "probabilities", "confidence"}
                or answer.get("type") != "choice"
            ):
                raise JevosError("invalid_response")

            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in criteria:
                raise JevosError("invalid_response")

            probabilities = answer.get("probabilities")
            if not isinstance(probabilities, dict) or set(probabilities) != set(criteria):
                raise JevosError("invalid_response")
            validated_probabilities: dict[str, float] = {}
            for option in criteria:
                value = probabilities.get(option)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise JevosError("invalid_response")
                try:
                    number = float(value)
                except (OverflowError, ValueError):
                    raise JevosError("invalid_response") from None
                if not math.isfinite(number) or not 0.0 <= number <= 1.0:
                    raise JevosError("invalid_response")
                validated_probabilities[option] = number

            if abs(sum(validated_probabilities.values()) - 1.0) > _CHOICE_SUM_TOLERANCE:
                raise JevosError("invalid_response")
            if (
                validated_probabilities[choice]
                + _CHOICE_ARGMAX_TOLERANCE
                < max(validated_probabilities.values())
            ):
                raise JevosError("invalid_response")

            confidence = answer.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise JevosError("invalid_response")
            try:
                confidence_number = float(confidence)
            except (OverflowError, ValueError):
                raise JevosError("invalid_response") from None
            if not math.isfinite(confidence_number) or not 0.0 <= confidence_number <= 1.0:
                raise JevosError("invalid_response")

            result[name] = ChoiceResult(
                choice=choice,
                probabilities=validated_probabilities,
                confidence=confidence_number,
            )
        return result

    def _request(self, method: Literal["GET", "POST"], path: str,
                 payload: dict | None = None) -> dict:
        deadline = time.monotonic() + 15
        try:
            timeout = httpx.Timeout(10.0, connect=1.0)
            with httpx.Client(
                transport=self._transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client, client.stream(method, _BASE_URL + path, json=payload) as response:
                if response.status_code >= 500:
                    raise JevosError("offline")
                if response.status_code != 200:
                    raise JevosError("invalid_response")
                raw = _read_limited(response, deadline)
        except JevosError:
            raise
        except (httpx.TimeoutException, httpx.RequestError):
            raise JevosError("offline") from None
        except Exception:
            raise JevosError("invalid_response") from None

        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            raise JevosError("invalid_response") from None
        if not isinstance(decoded, dict):
            raise JevosError("invalid_response")
        return decoded


def _read_limited(response: httpx.Response, deadline: float | None = None) -> bytes:
    deadline = time.monotonic() + 15 if deadline is None else deadline
    content_length = response.headers.get("content-length")
    declared_size: int | None = None
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            raise JevosError("invalid_response") from None
        if declared_size < 0 or declared_size > _MAX_RESPONSE_BYTES:
            raise JevosError("invalid_response")

    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_bytes():
            if time.monotonic() > deadline:
                raise JevosError("offline")
            if total + len(chunk) > _MAX_RESPONSE_BYTES:
                raise JevosError("invalid_response")
            chunks.append(chunk)
            total += len(chunk)
            # Without a declared length, reaching the cap would require reading
            # another chunk to distinguish an exact-size body from an oversized one.
            if total == _MAX_RESPONSE_BYTES and declared_size is None:
                raise JevosError("invalid_response")
    except JevosError:
        raise
    except httpx.DecodingError:
        raise JevosError("invalid_response") from None
    return b"".join(chunks)


def _validate_identity(identity: ModelInfo) -> None:
    if (
        not isinstance(identity, ModelInfo)
        or not isinstance(identity.model, str)
        or _MODEL_NAME.fullmatch(identity.model) is None
        or not isinstance(identity.fingerprint, str)
        or _FINGERPRINT.fullmatch(identity.fingerprint) is None
    ):
        raise JevosError("invalid_response")


def _serialize_state(state: dict) -> bytes:
    if not isinstance(state, dict):
        raise JevosError("invalid_response")
    try:
        serialized = json.dumps(
            state,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise JevosError("invalid_response") from None
    if len(serialized) > _MAX_STATE_BYTES:
        raise JevosError("invalid_response")
    return serialized


def _validate_questions(questions: dict[str, str]) -> None:
    if not isinstance(questions, dict) or not 1 <= len(questions) <= _MAX_QUESTIONS:
        raise JevosError("invalid_response")
    for name, instructions in questions.items():
        if (
            not isinstance(name, str)
            or _QUESTION_NAME.fullmatch(name) is None
            or not isinstance(instructions, str)
            or not instructions.strip()
            or len(instructions) > _MAX_INSTRUCTIONS_CHARS
        ):
            raise JevosError("invalid_response")


def _serialize_choice_questions(questions: dict[str, dict]) -> bytes:
    if not isinstance(questions, dict) or not 1 <= len(questions) <= _MAX_QUESTIONS:
        raise JevosError("invalid_response")

    request_questions: dict[str, dict] = {}
    for name, question in questions.items():
        if (
            not isinstance(name, str)
            or _QUESTION_NAME.fullmatch(name) is None
            or not isinstance(question, dict)
            or set(question) != {"type", "instructions", "criteria"}
            or question.get("type") != "choice"
        ):
            raise JevosError("invalid_response")
        instructions = question.get("instructions")
        criteria = question.get("criteria")
        if (
            not isinstance(instructions, str)
            or not instructions.strip()
            or len(instructions) > _MAX_INSTRUCTIONS_CHARS
            or not isinstance(criteria, dict)
            or not 2 <= len(criteria) <= _MAX_CHOICES
        ):
            raise JevosError("invalid_response")

        validated_criteria: dict[str, str] = {}
        for option, description in criteria.items():
            if (
                not isinstance(option, str)
                or _QUESTION_NAME.fullmatch(option) is None
                or not isinstance(description, str)
                or not description.strip()
                or len(description) > _MAX_CHOICE_DESCRIPTION_CHARS
            ):
                raise JevosError("invalid_response")
            validated_criteria[option] = description
        request_questions[name] = {
            "type": "choice",
            "instructions": instructions,
            "criteria": validated_criteria,
        }

    try:
        serialized = json.dumps(
            request_questions,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise JevosError("invalid_response") from None
    if len(serialized) > _MAX_QUESTIONS_BYTES:
        raise JevosError("invalid_response")
    return serialized
