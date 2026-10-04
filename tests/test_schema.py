import json
from pathlib import Path

from typer.testing import CliRunner

from failtriage.cli import app
from failtriage.report.json_output import AnalysisReport

SCHEMA = Path(__file__).parent.parent / "schema.json"


def test_committed_schema_matches_the_report_model() -> None:
    committed = json.loads(SCHEMA.read_text())

    assert committed == AnalysisReport.model_json_schema(), (
        "schema.json is stale, run: uv run failtriage schema > schema.json"
    )


def test_schema_command_prints_the_committed_schema() -> None:
    result = CliRunner().invoke(app, ["schema"])

    assert result.exit_code == 0
    assert result.stdout == SCHEMA.read_text()
