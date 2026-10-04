import hashlib
import json
from pathlib import Path
from typing import Protocol

from failtriage.prompts import Prompt


class Provider(Protocol):
    model: str

    def complete(self, prompt: Prompt, payload: str) -> str:
        """Send the payload under the prompt and return the model's raw JSON answer."""
        ...


class MissingRecordingError(Exception):
    pass


def recording_path(directory: Path, prompt_version: str, model: str, payload: str) -> Path:
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return directory / prompt_version / model / f"{digest}.json"


class RecordedProvider:
    """Replays answers recorded for a prompt version, a model and an exact payload."""

    def __init__(self, directory: Path, model: str) -> None:
        self._directory = directory
        self.model = model

    def complete(self, prompt: Prompt, payload: str) -> str:
        path = recording_path(self._directory, prompt.version, self.model, payload)
        if not path.is_file():
            raise MissingRecordingError(f"no recording for this payload, expected {path}")
        response: str = json.loads(path.read_text(encoding="utf-8"))["response"]
        return response
