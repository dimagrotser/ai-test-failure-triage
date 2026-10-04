from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from failtriage.models import Status


class HistoryEntry(BaseModel):
    """One test in one run on main, as the action uploads it (ADR 0002)."""

    test_id: str
    status: Status
    attempts: int = Field(ge=1)
    sha: str
    run_id: int


class HistoryError(Exception):
    pass


_ENTRIES = TypeAdapter(list[HistoryEntry])


def load_history(path: Path) -> list[HistoryEntry]:
    try:
        return _ENTRIES.validate_json(path.read_bytes())
    except (OSError, ValidationError):
        # No cause in the message: a validation error quotes the offending value.
        raise HistoryError(f"{path} is not a valid history file") from None


def history_schema() -> dict[str, Any]:
    return _ENTRIES.json_schema()
