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


def test_analyze_shows_attempt_count_per_test() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "retries_surefire.xml")])

    assert result.exit_code == 0
    assert "com.acme.shop.OrderServiceTest::shouldChargeCard (3 attempts)" in result.output
    mixed = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "mixed.xml")])
    assert "tests.test_cart::test_total_with_discount (1 attempt)" in mixed.output


def test_analyze_lists_passed_on_retry_with_last_failure_and_counts_it() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "retries_surefire.xml")])

    assert "PASSED_ON_RETRY com.acme.shop.OrderServiceTest::shouldReserveStock (3 attempts)" in (
        result.output
    )
    assert "Expected: 3 but was: 1" in result.output
    assert "shouldListOrders" not in result.output
    assert "1 failed, 1 passed on retry, 3 tests total" in result.output


def test_analyze_prints_redacted_text_only() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "secrets.xml")])

    assert result.exit_code == 0
    assert "<CARD>" in result.output
    assert "<EMAIL>" in result.output
    for secret in ["4111", "jane.doe", "hunter2", "abc.def.ghi", "ghp_"]:
        assert secret not in result.output


CASES = Path(__file__).parent.parent / "evals" / "cases"


def test_analyze_prints_forty_failures_with_one_cause_as_one_group() -> None:
    junit = CASES / "product-bug-deposit-float" / "junit.xml"

    result = runner.invoke(app, ["analyze", "--junit", str(junit)])

    assert result.exit_code == 0
    assert result.output.count("Group ") == 1
    assert "Group 1: 41 tests" in result.output
    assert "Signature: TypeError | " in result.output
    assert "| wallet/accounts.py:deposit" in result.output
    assert result.output.count("FAILED tests.test_balance::test_deposit_keeps_every_cent") == 40
    assert "41 failed, 0 passed on retry, 59 tests total, 1 group" in result.output


def test_analyze_prints_one_group_per_cause_with_a_sample_failure() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "mixed.xml")])

    assert result.output.count("Group ") == 3
    assert "Group 1: 1 test\n" in result.output
    assert result.output.index("Group 1") < result.output.index("test_total_with_discount")
    assert result.output.index("test_total_with_discount") < result.output.index("Group 2")
    assert "3 failed, 0 passed on retry, 6 tests total, 3 groups" in result.output


def test_analyze_prints_the_signature_on_redacted_text() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "secrets.xml")])

    [signature] = [line for line in result.output.splitlines() if "Signature:" in line]
    assert "card <CARD> declined for <EMAIL>" in signature
    assert "jane.doe" not in signature
