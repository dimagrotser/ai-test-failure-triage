import json
from pathlib import Path

from typer.testing import CliRunner, Result

from failtriage.cli import app
from failtriage.history import parse_history
from failtriage.models import Status

FIXTURES = Path(__file__).parent / "fixtures" / "junit"
SHA = "a" * 40

runner = CliRunner()


def run_history(report: str) -> Result:
    return runner.invoke(
        app, ["history", "--junit", str(FIXTURES / report), "--sha", SHA, "--run-id", "42"]
    )


def test_every_test_becomes_an_entry_with_only_the_history_fields() -> None:
    result = run_history("mixed.xml")

    assert result.exit_code == 0
    entries = json.loads(result.stdout)
    assert entries
    for entry in entries:
        assert set(entry) == {"test_id", "status", "attempts", "sha", "run_id"}
        assert entry["sha"] == SHA
        assert entry["run_id"] == 42
    assert "tests.test_cart::test_empty_cart" in {e["test_id"] for e in entries}


def test_the_output_reads_back_as_history() -> None:
    result = run_history("retries_surefire.xml")

    entries = parse_history(result.stdout.encode())
    retried = [e for e in entries if e.status is Status.PASSED_ON_RETRY]
    assert retried
    assert all(e.attempts > 1 for e in retried)


def test_nothing_but_the_history_fields_leaves_the_report() -> None:
    result = run_history("secrets.xml")

    assert result.exit_code == 0
    for leak in ["hunter2", "ghp_", "jane.doe", "PRIVATE KEY", "4111"]:
        assert leak not in result.stdout


def test_a_broken_report_exits_with_2() -> None:
    result = run_history("broken.xml")

    assert result.exit_code == 2
    assert result.stdout == ""


def test_an_empty_report_gives_an_empty_history() -> None:
    result = run_history("empty.xml")

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
