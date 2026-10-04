import hashlib
import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from failtriage.prompts import Prompt


class Usage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class Provider(Protocol):
    model: str
    usage: Usage

    def complete(self, prompt: Prompt, payload: str) -> str:
        """Send the payload under the prompt and return the model's raw JSON answer."""
        ...


class MissingRecordingError(Exception):
    pass


def recording_path(directory: Path, prompt_version: str, model: str, payload: str) -> Path:
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return directory / prompt_version / model / f"{digest}.json"


def write_recording(
    directory: Path, prompt: Prompt, model: str, payload: str, response: str, usage: Usage
) -> None:
    path = recording_path(directory, prompt.version, model, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    recording = {
        "prompt_version": prompt.version,
        "model": model,
        "payload": payload,
        "response": response,
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
    }
    path.write_text(json.dumps(recording, indent=2) + "\n", encoding="utf-8")


class RecordedProvider:
    """Replays answers recorded for a prompt version, a model and an exact payload."""

    def __init__(self, directory: Path, model: str) -> None:
        self._directory = directory
        self.model = model
        self.usage = Usage()

    def complete(self, prompt: Prompt, payload: str) -> str:
        path = recording_path(self._directory, prompt.version, self.model, payload)
        if not path.is_file():
            raise MissingRecordingError(f"no recording for this payload, expected {path}")
        recording = json.loads(path.read_text(encoding="utf-8"))
        used = recording.get("usage", {})
        self.usage.calls += 1
        self.usage.input_tokens += used.get("input_tokens", 0)
        self.usage.output_tokens += used.get("output_tokens", 0)
        response: str = recording["response"]
        return response
