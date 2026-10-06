import json
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest

from failtriage.classify.anthropic_provider import AnthropicProvider
from failtriage.classify.provider import ProviderError, RecordedProvider, Usage
from failtriage.prompts import Prompt

MODEL = "claude-sonnet-5-5"
PROMPT = Prompt(version="classify-v1", text="You classify failures.")
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"category": {"type": "string"}}}


def message(text: str = '{"category": "unknown"}', stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def client(
    requests: list[dict[str, Any]], response: httpx2.Response | None = None
) -> anthropic.Anthropic:
    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return response or httpx2.Response(200, json=message())

    return anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle)),
    )


def test_the_payload_is_sent_under_the_prompt_with_a_json_schema() -> None:
    requests: list[dict[str, Any]] = []
    provider = AnthropicProvider(client(requests), MODEL, SCHEMA)

    provider.complete(PROMPT, '{"tests": []}')

    sent = requests[0]
    assert sent["model"] == MODEL
    assert sent["system"] == PROMPT.text
    assert sent["messages"] == [{"role": "user", "content": '{"tests": []}'}]
    assert sent["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}


def test_low_effort_is_asked_for_on_a_model_that_takes_it() -> None:
    requests: list[dict[str, Any]] = []

    AnthropicProvider(client(requests), MODEL, SCHEMA).complete(PROMPT, "payload")

    assert requests[0]["output_config"]["effort"] == "low"


@pytest.mark.parametrize("haiku", ["claude-haiku-4-5", "claude-haiku-4-5-20251001"])
def test_haiku_is_not_sent_the_effort_parameter_it_rejects(haiku: str) -> None:
    requests: list[dict[str, Any]] = []

    AnthropicProvider(client(requests), haiku, SCHEMA).complete(PROMPT, "payload")

    assert requests[0]["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


def test_the_answer_text_is_returned() -> None:
    provider = AnthropicProvider(client([]), MODEL, SCHEMA)

    assert provider.complete(PROMPT, "payload") == '{"category": "unknown"}'


def test_usage_is_summed_over_calls() -> None:
    provider = AnthropicProvider(client([]), MODEL, SCHEMA)

    provider.complete(PROMPT, "a")
    provider.complete(PROMPT, "b")

    assert provider.usage == Usage(calls=2, input_tokens=240, output_tokens=60)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_an_incomplete_answer_is_an_error(stop_reason: str) -> None:
    response = httpx2.Response(200, json=message(stop_reason=stop_reason))
    provider = AnthropicProvider(client([], response), MODEL, SCHEMA)

    with pytest.raises(ProviderError, match=stop_reason):
        provider.complete(PROMPT, "payload")


def test_a_recording_is_written_only_when_asked(tmp_path: Path) -> None:
    AnthropicProvider(client([]), MODEL, SCHEMA).complete(PROMPT, "payload")

    assert list(tmp_path.iterdir()) == []


def test_a_recording_replays_the_answer_and_its_usage(tmp_path: Path) -> None:
    live = AnthropicProvider(client([]), MODEL, SCHEMA, record_dir=tmp_path)
    answer = live.complete(PROMPT, "payload")

    replay = RecordedProvider(tmp_path, MODEL)

    assert replay.complete(PROMPT, "payload") == answer
    assert replay.usage == Usage(calls=1, input_tokens=120, output_tokens=30)


@pytest.mark.parametrize(
    ("status", "error_type", "name"),
    [
        (401, "authentication_error", "AuthenticationError"),
        (429, "rate_limit_error", "RateLimitError"),
    ],
)
def test_an_api_error_names_its_type_and_status_and_does_not_quote_the_key(
    status: int, error_type: str, name: str
) -> None:
    error_body = {"type": "error", "error": {"type": error_type, "message": "bad key"}}
    provider = AnthropicProvider(
        client([], httpx2.Response(status, json=error_body)), MODEL, SCHEMA
    )

    with pytest.raises(ProviderError) as error:
        provider.complete(PROMPT, "payload")

    assert str(error.value) == f"the request failed: {name}, HTTP {status}"
    assert "test-key" not in str(error.value)
    assert "bad key" not in str(error.value)


def test_a_connection_error_has_no_status() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    unreachable = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(refuse)),
    )

    with pytest.raises(ProviderError) as error:
        AnthropicProvider(unreachable, MODEL, SCHEMA).complete(PROMPT, "payload")

    assert str(error.value) == "the request failed: APIConnectionError"
