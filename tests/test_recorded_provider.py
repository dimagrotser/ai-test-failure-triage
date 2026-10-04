import json
from pathlib import Path

import pytest

from failtriage.classify.provider import (
    MissingRecordingError,
    RecordedProvider,
    Usage,
    recording_path,
    write_recording,
)
from failtriage.prompts import Prompt

MODEL = "claude-sonnet-5-5"
PROMPT = Prompt(version="classify-v1", text="prompt")


def record(directory: Path, prompt: Prompt, model: str, payload: str, response: str) -> None:
    write_recording(directory, prompt, model, payload, response, Usage(input_tokens=0))


def test_a_recorded_response_is_replayed(tmp_path: Path) -> None:
    record(tmp_path, PROMPT, MODEL, '{"a": 1}', '{"category": "unknown"}')

    provider = RecordedProvider(tmp_path, MODEL)

    assert provider.complete(PROMPT, '{"a": 1}') == '{"category": "unknown"}'


def test_the_provider_reports_its_model(tmp_path: Path) -> None:
    assert RecordedProvider(tmp_path, MODEL).model == MODEL


def test_a_missing_recording_fails_and_names_the_expected_file(tmp_path: Path) -> None:
    provider = RecordedProvider(tmp_path, MODEL)

    with pytest.raises(MissingRecordingError) as error:
        provider.complete(PROMPT, "payload")

    assert str(recording_path(tmp_path, PROMPT.version, MODEL, "payload")) in str(error.value)


def test_a_changed_payload_is_a_miss(tmp_path: Path) -> None:
    record(tmp_path, PROMPT, MODEL, "payload", "{}")

    with pytest.raises(MissingRecordingError):
        RecordedProvider(tmp_path, MODEL).complete(PROMPT, "payload ")


def test_another_model_is_a_miss(tmp_path: Path) -> None:
    record(tmp_path, PROMPT, MODEL, "payload", "{}")

    with pytest.raises(MissingRecordingError):
        RecordedProvider(tmp_path, "claude-haiku-4-5-20251001").complete(PROMPT, "payload")


def test_another_prompt_version_is_a_miss(tmp_path: Path) -> None:
    record(tmp_path, PROMPT, MODEL, "payload", "{}")

    with pytest.raises(MissingRecordingError):
        RecordedProvider(tmp_path, MODEL).complete(
            Prompt(version="classify-v2", text="prompt"), "payload"
        )


def test_recorded_usage_is_replayed_and_accumulated(tmp_path: Path) -> None:
    write_recording(tmp_path, PROMPT, MODEL, "a", "{}", Usage(input_tokens=100, output_tokens=20))
    write_recording(tmp_path, PROMPT, MODEL, "b", "{}", Usage(input_tokens=50, output_tokens=5))
    provider = RecordedProvider(tmp_path, MODEL)

    provider.complete(PROMPT, "a")
    provider.complete(PROMPT, "b")

    assert provider.usage == Usage(calls=2, input_tokens=150, output_tokens=25)


def test_a_recording_without_usage_counts_as_free(tmp_path: Path) -> None:
    path = recording_path(tmp_path, PROMPT.version, MODEL, "old")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"response": "{}"}))
    provider = RecordedProvider(tmp_path, MODEL)

    provider.complete(PROMPT, "old")

    assert provider.usage == Usage(calls=1)


def test_a_written_recording_keeps_the_prompt_model_and_payload(tmp_path: Path) -> None:
    write_recording(tmp_path, PROMPT, MODEL, "payload", "{}", Usage(input_tokens=1))

    saved = json.loads(recording_path(tmp_path, PROMPT.version, MODEL, "payload").read_text())

    assert saved["prompt_version"] == PROMPT.version
    assert saved["model"] == MODEL
    assert saved["payload"] == "payload"
    assert saved["usage"] == {"input_tokens": 1, "output_tokens": 0}
