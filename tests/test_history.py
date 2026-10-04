import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from failtriage.cli import app
from failtriage.history import HistoryError, history_schema, load_history

CASES = Path(__file__).parent.parent / "evals" / "cases"
SCHEMA = Path(__file__).parent.parent / "history.schema.json"


@pytest.mark.parametrize("path", sorted(CASES.glob("*/history.json")), ids=lambda p: p.parent.name)
def test_every_lab_history_file_loads_unchanged(path: Path) -> None:
    entries = load_history(path)

    assert len(entries) == len(json.loads(path.read_text()))


def test_an_entry_keeps_the_five_fields_of_the_format() -> None:
    entry = load_history(CASES / "flaky-random-transfer-id" / "history.json")[0]

    assert set(entry.model_dump()) == {"test_id", "status", "attempts", "sha", "run_id"}


def test_the_empty_lab_history_is_an_empty_list() -> None:
    assert load_history(CASES / "unknown-interest-rate-mismatch" / "history.json") == []


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"test_id": "a"}',
        '[{"test_id": "a", "status": "passed", "attempts": 0, "sha": "s", "run_id": 1}]',
        '[{"test_id": "a", "status": "melted", "attempts": 1, "sha": "s", "run_id": 1}]',
        '[{"test_id": "a", "status": "passed", "attempts": 1, "sha": "s"}]',
    ],
)
def test_an_invalid_file_is_an_error_that_quotes_none_of_it(tmp_path: Path, content: str) -> None:
    path = tmp_path / "history.json"
    path.write_text(content)

    with pytest.raises(HistoryError) as error:
        load_history(path)

    assert str(error.value) == f"{path} is not a valid history file"


def test_a_missing_file_is_a_history_error(tmp_path: Path) -> None:
    with pytest.raises(HistoryError):
        load_history(tmp_path / "nope.json")


def test_committed_history_schema_matches_the_model() -> None:
    assert json.loads(SCHEMA.read_text()) == history_schema(), (
        "history.schema.json is stale, run: failtriage schema --history > history.schema.json"
    )


def test_schema_history_command_prints_the_committed_schema() -> None:
    result = CliRunner().invoke(app, ["schema", "--history"])

    assert result.exit_code == 0
    assert result.stdout == SCHEMA.read_text()
