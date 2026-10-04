import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from failtriage.cli import app
from failtriage.models import Category, ClassifiedBy
from failtriage.report.json_output import AnalysisReport

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


def analyze_json(junit: Path) -> AnalysisReport:
    result = runner.invoke(app, ["analyze", "--junit", str(junit), "--json"])
    assert result.exit_code == 0
    return AnalysisReport.model_validate_json(result.stdout)


def test_analyze_json_has_version_run_groups_cost_and_no_history() -> None:
    report = analyze_json(FIXTURES / "mixed.xml")
    raw = json.loads(
        runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "mixed.xml"), "--json"]).stdout
    )

    assert report.schema_version == 1
    assert set(raw) == {"schema_version", "run", "groups", "cost", "history"}
    assert raw["history"] is None
    assert report.run.tests == 6
    assert report.run.failed == 3
    assert len(report.groups) == 3
    assert report.cost.usd == 0
    first = report.groups[0]
    assert first.tests[0].test_id == "tests.test_cart::test_total_with_discount"
    assert first.classification.classified_by is ClassifiedBy.HEURISTICS


def test_analyze_json_contains_redacted_text_only() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "secrets.xml"), "--json"])

    assert result.exit_code == 0
    assert "<CARD>" in result.stdout
    for secret in ["4111", "jane.doe", "hunter2", "abc.def.ghi", "ghp_"]:
        assert secret not in result.stdout


def test_analyze_json_works_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    report = analyze_json(CASES / "environment-ledger-down" / "junit.xml")

    assert [g.classification.category for g in report.groups] == [Category.ENVIRONMENT]
    assert report.cost.llm_calls == 0


def test_analyze_json_of_an_empty_report_has_no_groups() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "empty.xml"), "--json"])

    assert result.exit_code == 0
    assert AnalysisReport.model_validate_json(result.stdout).groups == []


def test_analyze_json_prints_nothing_for_unreadable_input() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "broken.xml"), "--json"])

    assert result.exit_code == 2
    assert result.stdout == ""


@pytest.mark.parametrize("flags", [[], ["--json"]])
def test_analyze_internal_error_exits_one_without_printing_details(
    monkeypatch: pytest.MonkeyPatch, flags: list[str]
) -> None:
    def explode(results: object) -> None:
        raise RuntimeError("token ghp_unredactedsecret leaked")

    monkeypatch.setattr("failtriage.cli.group_failures", explode)

    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "mixed.xml"), *flags])

    assert result.exit_code == 1
    assert "internal error: RuntimeError" in result.output
    assert "ghp_" not in result.output
    assert result.stdout == ""


def test_analyze_markdown_prints_the_report() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "mixed.xml"), "--markdown"])

    assert result.exit_code == 0
    assert result.stdout.startswith("**3 failure groups**: 3 failed tests")
    assert "History: none." in result.stdout


def test_analyze_markdown_of_a_green_run_is_one_line() -> None:
    result = runner.invoke(
        app, ["analyze", "--junit", str(FIXTURES / "all_green.xml"), "--markdown"]
    )

    assert result.exit_code == 0
    assert result.stdout == "All 4 tests passed or were skipped.\n"


def test_analyze_markdown_contains_redacted_text_only() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "secrets.xml"), "--markdown"])

    assert result.exit_code == 0
    assert "test_pay_with_saved_card" in result.stdout
    for secret in ["4111", "jane.doe", "hunter2", "abc.def.ghi", "ghp_"]:
        assert secret not in result.stdout


def test_analyze_rejects_json_and_markdown_together() -> None:
    result = runner.invoke(
        app, ["analyze", "--junit", str(FIXTURES / "mixed.xml"), "--json", "--markdown"]
    )

    assert result.exit_code == 2
    assert "either --json or --markdown" in result.output
    assert result.stdout == ""
