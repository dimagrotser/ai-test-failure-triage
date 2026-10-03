from pathlib import Path

import pytest
from typer.testing import CliRunner

from failtriage.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Commands" in result.output
    assert "version" in result.output


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


FIXTURES = Path(__file__).parent / "fixtures" / "junit"


def test_analyze_prints_each_failure_once() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "mixed.xml")])

    assert result.exit_code == 0
    for test_id in [
        "tests.test_cart::test_total_with_discount",
        "tests/test_api.py::test_fetch_profile",
        "tests.test_db::test_migrations_apply",
    ]:
        assert result.output.count(test_id) == 1
    assert "assert 90 == 95" in result.output
    assert "tests/test_cart.py:21: AssertionError" in result.output
    assert "test_empty_cart" not in result.output
    assert "test_legacy_import" not in result.output


def test_analyze_empty_report_warns_and_exits_zero() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "empty.xml")])

    assert result.exit_code == 0
    assert "warning" in result.output
    assert "no tests found" in result.output


@pytest.mark.parametrize("name", ["broken.xml", "zero.xml", "missing.xml"])
def test_analyze_unreadable_report_exits_two(name: str) -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / name)])

    assert result.exit_code == 2
    assert "cannot read" in result.output
