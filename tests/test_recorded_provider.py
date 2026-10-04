import json
from pathlib import Path

import pytest

from failtriage.classify.provider import (
    MissingRecordingError,
    RecordedProvider,
    recording_path,
)
from failtriage.prompts import Prompt

MODEL = "claude-sonnet-5-5"
PROMPT = Prompt(version="classify-v1", text="prompt")


def record(directory: Path, prompt: Prompt, model: str, payload: str, response: str) -> None:
    path = recording_path(directory, prompt.version, model, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "prompt_version": prompt.version,
                "model": model,
                "payload": payload,
                "response": response,
            }
        )
    )


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
