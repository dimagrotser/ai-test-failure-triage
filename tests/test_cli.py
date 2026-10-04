import json
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from typer.testing import CliRunner

from failtriage import cli
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
    assert set(raw) == {"schema_version", "run", "groups", "cost", "diff", "history"}
    assert raw["diff"] is None
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


EVALS = Path(__file__).parent / "fixtures" / "evals"


def test_eval_prints_the_baseline_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["eval", str(EVALS)])

    assert result.exit_code == 0
    assert "Accuracy: 3/5 (60%)" in result.output
    assert "Confusion matrix" in result.output


def test_eval_reports_a_dataset_that_cannot_be_scored(tmp_path: Path) -> None:
    result = runner.invoke(app, ["eval", str(tmp_path)])

    assert result.exit_code == 2
    assert "no cases" in result.output


KEY = "sk-ant-api03-DoNotPrintThisKey0123456789"
LEDGER_DOWN = CASES / "environment-ledger-down" / "junit.xml"
QUOTE = "urllib.error.URLError: <urlopen error [Errno 61] Connection refused>"
LLM_ANSWER = {
    "category": "environment",
    "confidence": "high",
    "summary": "The ledger service refused the connection.",
    "evidence": [QUOTE],
    "next_step": "Check that the ledger service is up",
    "disagreement_reason": None,
}


def fake_anthropic(
    monkeypatch: pytest.MonkeyPatch, requests: list[dict[str, Any]], status: int = 200
) -> None:
    """The key is set and the API is a local handler, so no test reaches the network."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        if status != 200:
            error = {"type": "authentication_error", "message": f"bad {KEY}"}
            return httpx2.Response(status, json={"type": "error", "error": error})
        body = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": requests[-1]["model"],
            "content": [{"type": "text", "text": json.dumps(LLM_ANSWER)}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 200},
        }
        return httpx2.Response(200, json=body)

    def make_client() -> anthropic.Anthropic:
        return anthropic.Anthropic(
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle)),
        )

    monkeypatch.setattr(cli, "_client", make_client)


def test_without_a_key_the_run_uses_heuristics_and_says_so() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--json"])

    assert result.exit_code == 0
    report = AnalysisReport.model_validate_json(result.stdout)
    assert report.groups[0].classification.classified_by is ClassifiedBy.HEURISTICS
    assert report.cost.model is None
    assert report.cost.llm_calls == 0
    assert "ANTHROPIC_API_KEY not set" in result.stderr


def test_with_a_key_groups_are_classified_by_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_anthropic(monkeypatch, [])

    report = analyze_json(LEDGER_DOWN)

    classification = report.groups[0].classification
    assert classification.classified_by is ClassifiedBy.LLM
    assert classification.evidence == [QUOTE]


def test_tokens_and_cost_are_in_the_json_and_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_anthropic(monkeypatch, [])

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--json"])

    cost = AnalysisReport.model_validate_json(result.stdout).cost
    assert cost.model == "claude-sonnet-5-5"
    assert (cost.llm_calls, cost.input_tokens, cost.output_tokens) == (1, 1000, 200)
    assert cost.usd == pytest.approx(0.004)
    assert "1 call, 1000 input tokens, 200 output tokens, $0.0040" in result.stderr


def test_the_default_model_is_sonnet_and_it_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)

    runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--json"])
    runner.invoke(
        app, ["analyze", "--junit", str(LEDGER_DOWN), "--json", "--model", "claude-haiku-4-5"]
    )

    assert [r["model"] for r in requests] == ["claude-sonnet-5-5", "claude-haiku-4-5"]


def test_an_unpriced_model_is_logged_without_a_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_anthropic(monkeypatch, [])

    result = runner.invoke(
        app, ["analyze", "--junit", str(LEDGER_DOWN), "--json", "--model", "claude-mystery"]
    )

    assert AnalysisReport.model_validate_json(result.stdout).cost.usd is None
    assert "cost unknown" in result.stderr


def test_the_markdown_report_is_classified_by_the_llm_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic(monkeypatch, [])

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--markdown"])

    assert result.exit_code == 0
    assert "The ledger service refused the connection." in result.stdout


def test_record_writes_recordings_that_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_anthropic(monkeypatch, [])

    result = runner.invoke(
        app, ["analyze", "--junit", str(LEDGER_DOWN), "--json", "--record", str(tmp_path)]
    )

    assert result.exit_code == 0
    [path] = (tmp_path / "classify-v1" / "claude-sonnet-5-5").glob("*.json")
    saved = json.loads(path.read_text())
    assert json.loads(saved["response"]) == LLM_ANSWER
    assert saved["usage"] == {"input_tokens": 1000, "output_tokens": 200}


def test_record_without_a_key_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["analyze", "--junit", str(LEDGER_DOWN), "--json", "--record", str(tmp_path)]
    )

    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_a_failing_call_falls_back_to_heuristics_and_the_run_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_anthropic(monkeypatch, [], status=401)

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--json"])

    assert result.exit_code == 0
    report = AnalysisReport.model_validate_json(result.stdout)
    assert report.groups[0].classification.classified_by is ClassifiedBy.HEURISTICS
    assert "group 1: ProviderError" in result.stderr


@pytest.mark.parametrize("status", [200, 401])
def test_the_key_is_never_printed_or_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    fake_anthropic(monkeypatch, [], status=status)

    result = runner.invoke(
        app, ["analyze", "--junit", str(LEDGER_DOWN), "--json", "--record", str(tmp_path)]
    )

    written = "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    assert KEY not in result.stdout + result.stderr + written


def test_the_text_output_does_not_call_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN)])

    assert result.exit_code == 0
    assert requests == []


def test_what_analyze_sends_to_the_api_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)

    runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "secrets.xml"), "--json"])

    sent = json.dumps(requests)
    assert requests
    assert "<CARD>" in sent
    for secret in ["4111", "jane.doe", "hunter2", "abc.def.ghi", "ghp_"]:
        assert secret not in sent


def test_record_with_text_output_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_anthropic(monkeypatch, [])

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--record", str(tmp_path)])

    assert result.exit_code == 2
    assert "--json or --markdown" in result.stderr
