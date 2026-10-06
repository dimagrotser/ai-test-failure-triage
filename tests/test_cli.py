import datetime
import io
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from typer.testing import CliRunner

from failtriage import cli
from failtriage.cli import app
from failtriage.evaluate import EvalReport
from failtriage.github.client import GitHubClient
from failtriage.models import Category, ClassifiedBy, SignalName
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
    assert result.output.strip() == "1.0.0"


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


def test_eval_without_a_key_runs_the_heuristics_only_and_says_so(tmp_path: Path) -> None:
    out = tmp_path / "result.json"

    result = runner.invoke(app, ["eval", str(EVALS), "--json", str(out)])

    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY not set, scoring the heuristics only" in result.stderr
    report = EvalReport.model_validate_json(out.read_text())
    assert report.date == datetime.datetime.now(datetime.UTC).date().isoformat()
    assert [run.model for run in report.runs] == [None]


def test_eval_with_a_key_scores_sonnet_and_haiku_on_the_same_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)
    out = tmp_path / "result.json"

    result = runner.invoke(app, ["eval", str(EVALS), "--json", str(out)])

    assert result.exit_code == 0
    report = EvalReport.model_validate_json(out.read_text())
    assert [run.model for run in report.runs] == [None, "claude-sonnet-5-5", "claude-haiku-4-5"]
    assert {len(run.groups) for run in report.runs} == {5}
    assert [(g.case_id, g.expected) for g in report.runs[1].groups] == [
        (g.case_id, g.expected) for g in report.runs[0].groups
    ]
    assert [r["model"] for r in requests] == ["claude-sonnet-5-5"] * 5 + ["claude-haiku-4-5"] * 5
    sonnet, haiku = report.runs[1].cost, report.runs[2].cost
    assert (sonnet.llm_calls, sonnet.input_tokens, sonnet.output_tokens) == (5, 5000, 1000)
    assert sonnet.usd == pytest.approx(0.02)
    assert haiku.usd == pytest.approx(0.01)
    assert "Total LLM cost: $0.0300" in result.output


def test_eval_scores_only_the_models_that_are_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_anthropic(monkeypatch, [])
    out = tmp_path / "result.json"

    result = runner.invoke(
        app, ["eval", str(EVALS), "--model", "claude-haiku-4-5", "--json", str(out)]
    )

    assert result.exit_code == 0
    report = EvalReport.model_validate_json(out.read_text())
    assert [run.model for run in report.runs] == [None, "claude-haiku-4-5"]


def test_eval_redacts_a_case_before_it_reaches_the_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = tmp_path / "cases" / "environment-refused"
    shutil.copytree(EVALS / "cases" / "environment-refused", case)
    junit = case / "junit.xml"
    junit.write_text(
        junit.read_text().replace("wallet/ledger.py:12", "https://ci:hunter2pass@ledger.internal")
    )
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)

    result = runner.invoke(app, ["eval", str(tmp_path)])

    assert result.exit_code == 0
    assert requests
    assert "hunter2pass" not in json.dumps(requests)


def test_eval_never_prints_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_anthropic(monkeypatch, [], status=401)

    result = runner.invoke(app, ["eval", str(EVALS)])

    assert result.exit_code == 0
    assert KEY not in result.output + result.stderr
    assert "Classified by heuristics instead of the LLM: 5" in result.output


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


GITHUB_TOKEN = "ghs_DoNotPrintThisToken0123456789"
PR_FILES = [
    {
        "filename": "wallet/ledger.py",
        "status": "modified",
        "patch": "@@ -1,2 +1,2 @@\n-    url = 'http://ledger'\n+    url = 'http://ledger:9000'",
    },
    {"filename": "assets/logo.png", "status": "added"},
    {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new"},
]


def fake_github(
    monkeypatch: pytest.MonkeyPatch,
    requests: list[httpx.Request],
    status: int = 200,
    files: list[dict[str, Any]] = PR_FILES,
    artifacts: dict[int, bytes | int] | None = None,
) -> None:
    """Fake GitHub. `artifacts` maps an artifact id to its zip, or to an error status; the
    listing shows them newest first (highest id) as runs on main."""
    monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)
    served = artifacts or {}

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status, json={"message": f"bad {GITHUB_TOKEN}"})
        if request.url.path.endswith("/actions/artifacts"):
            listing = [
                {
                    "id": i,
                    "expired": False,
                    "workflow_run": {
                        "head_branch": "main",
                        "repository_id": 1,
                        "head_repository_id": 1,
                    },
                }
                for i in sorted(served, reverse=True)
            ]
            return httpx.Response(200, json={"total_count": len(listing), "artifacts": listing})
        if "/actions/artifacts/" in request.url.path:
            answer = served[int(request.url.path.split("/")[-2])]
            if isinstance(answer, int):
                return httpx.Response(answer, json={"message": "gone"})
            return httpx.Response(200, content=answer)
        return httpx.Response(200, json=files)

    monkeypatch.setattr(
        cli,
        "_github_client",
        lambda token: GitHubClient(token, transport=httpx.MockTransport(handle)),
    )


def analyze_pr(*extra: str) -> Any:
    args = ["analyze", "--junit", str(LEDGER_DOWN), "--json", "--repo", "acme/wallet", "--pr", "7"]
    return runner.invoke(app, [*args, *extra])


def test_the_pr_files_are_read_from_github_with_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    github: list[httpx.Request] = []
    fake_github(monkeypatch, github)

    result = analyze_pr()

    assert result.exit_code == 0
    assert "/repos/acme/wallet/pulls/7/files" in [r.url.path for r in github]
    assert all(r.headers["Authorization"] == f"Bearer {GITHUB_TOKEN}" for r in github)


def test_the_report_lists_changed_files_and_those_without_hunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_github(monkeypatch, [])

    report = AnalysisReport.model_validate_json(analyze_pr().stdout)

    assert report.diff is not None
    assert report.diff.available
    assert report.diff.changed_files == ["wallet/ledger.py", "assets/logo.png", "README.md"]
    assert report.diff.files_without_hunks == ["assets/logo.png"]
    assert SignalName.TOUCHES_CHANGED_FILE in {s.name for s in report.groups[0].signals}


def test_the_matching_hunks_are_sent_to_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)
    fake_github(monkeypatch, [])

    analyze_pr()

    sent = json.dumps(requests)
    assert "ledger:9000" in sent
    assert "README.md" not in sent
    assert "touches_changed_file" in sent


def test_a_secret_in_a_patch_is_not_sent_to_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)
    leaky = [{**PR_FILES[0], "patch": "@@ -1 +1 @@\n-x\n+DB_PASSWORD = 'hunter2hunter2'"}]
    fake_github(monkeypatch, [], files=leaky)

    analyze_pr()

    assert requests
    assert "hunter2" not in json.dumps(requests)


def test_the_markdown_report_of_a_pr_run_notes_the_missing_hunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_github(monkeypatch, [])
    args = [
        "analyze",
        "--junit",
        str(LEDGER_DOWN),
        "--markdown",
        "--repo",
        "acme/wallet",
        "--pr",
        "7",
    ]

    result = runner.invoke(app, args)

    assert "Hunks missing for `assets/logo.png`" in result.stdout


def test_a_github_failure_is_a_warning_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_github(monkeypatch, [], status=403)

    result = analyze_pr()

    assert result.exit_code == 0
    report = AnalysisReport.model_validate_json(result.stdout)
    assert report.diff is not None
    assert not report.diff.available
    assert "continuing without the diff" in result.stderr
    assert GITHUB_TOKEN not in result.stderr + result.stdout
    assert "bad" not in result.stderr


def test_a_run_without_repo_and_pr_has_no_diff_and_never_calls_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github: list[httpx.Request] = []
    fake_github(monkeypatch, github)

    report = analyze_json(LEDGER_DOWN)

    assert report.diff is None
    assert github == []


@pytest.mark.parametrize(
    "args",
    [
        ["--repo", "acme/wallet"],
        ["--pr", "7"],
        ["--repo", "wallet", "--pr", "7"],
    ],
)
def test_repo_and_pr_go_together_and_repo_has_an_owner(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    github: list[httpx.Request] = []
    fake_github(monkeypatch, github)

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--json", *args])

    assert result.exit_code == 2
    assert github == []


def test_a_pr_without_a_token_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = analyze_pr()

    assert result.exit_code == 2
    assert "GITHUB_TOKEN" in result.stderr


def test_a_pr_with_text_output_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_github(monkeypatch, [])
    args = ["analyze", "--junit", str(LEDGER_DOWN), "--repo", "acme/wallet", "--pr", "7"]

    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert "--json or --markdown" in result.stderr


FLAKY_CASE = Path(__file__).parent.parent / "evals" / "cases" / "flaky-random-transfer-id"


def analyze_with_history(history: Path, *extra: str) -> Any:
    args = ["analyze", "--junit", str(FLAKY_CASE / "junit.xml"), "--history", str(history)]
    return runner.invoke(app, [*args, *(extra or ["--json"])])


def test_history_adds_signals_and_a_summary_to_the_json() -> None:
    result = analyze_with_history(FLAKY_CASE / "history.json")

    assert result.exit_code == 0
    report = AnalysisReport.model_validate_json(result.stdout)
    assert report.history is not None
    assert report.history.runs == 2
    names = {s.name for g in report.groups for s in g.signals}
    assert {SignalName.FAILED_ON_MAIN, SignalName.FLAKY_IN_HISTORY} <= names


def test_history_shows_in_the_markdown() -> None:
    result = analyze_with_history(FLAKY_CASE / "history.json", "--markdown")

    assert result.exit_code == 0
    assert "History: 2 runs on main" in result.stdout


def test_without_history_the_report_says_none() -> None:
    result = runner.invoke(app, ["analyze", "--junit", str(FLAKY_CASE / "junit.xml"), "--markdown"])

    assert "History: none." in result.stdout
    assert analyze_json(FLAKY_CASE / "junit.xml").history is None


def test_an_empty_history_file_is_reported_as_none(tmp_path: Path) -> None:
    empty = tmp_path / "history.json"
    empty.write_text("[]")

    result = analyze_with_history(empty)

    assert result.exit_code == 0
    assert AnalysisReport.model_validate_json(result.stdout).history is None


def test_an_invalid_history_file_is_a_usage_error_that_quotes_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "history.json"
    bad.write_text('[{"test_id": "secret-test", "status": "melted"}]')

    result = analyze_with_history(bad)

    assert result.exit_code == 2
    assert f"{bad} is not a valid history file" in result.stderr
    assert "secret-test" not in result.stderr + result.stdout


def test_history_with_text_output_is_a_usage_error() -> None:
    args = ["analyze", "--junit", str(FLAKY_CASE / "junit.xml"), "--history"]

    result = runner.invoke(app, [*args, str(FLAKY_CASE / "history.json")])

    assert result.exit_code == 2
    assert "--json or --markdown" in result.stderr


def history_zip(run_id: int) -> bytes:
    entries = [
        e for e in json.loads((FLAKY_CASE / "history.json").read_text()) if e["run_id"] == run_id
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("history.json", json.dumps(entries))
    return buffer.getvalue()


def analyze_flaky_pr(*extra: str) -> Any:
    args = [
        "analyze",
        "--junit",
        str(FLAKY_CASE / "junit.xml"),
        "--repo",
        "acme/wallet",
        "--pr",
        "7",
    ]
    return runner.invoke(app, [*args, *(extra or ["--json"])])


def downloaded(github: list[httpx.Request]) -> list[int]:
    return [int(r.url.path.split("/")[-2]) for r in github if r.url.path.endswith("/zip")]


def test_a_pr_run_reads_history_from_the_artifacts_on_main(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_github(monkeypatch, [], artifacts={2: history_zip(2), 1: history_zip(1)})

    result = analyze_flaky_pr()

    assert result.exit_code == 0
    report = AnalysisReport.model_validate_json(result.stdout)
    assert report.history is not None
    assert report.history.runs == 2
    names = {s.name for g in report.groups for s in g.signals}
    assert SignalName.FLAKY_IN_HISTORY in names


def test_history_runs_limits_how_many_artifacts_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    github: list[httpx.Request] = []
    fake_github(monkeypatch, github, artifacts={2: history_zip(2), 1: history_zip(1)})

    result = analyze_flaky_pr("--json", "--history-runs", "1")

    assert result.exit_code == 0
    assert downloaded(github) == [2]


def test_ten_runs_are_read_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    github: list[httpx.Request] = []
    fake_github(monkeypatch, github, artifacts={i: history_zip(1) for i in range(1, 13)})

    analyze_flaky_pr()

    assert downloaded(github) == list(range(12, 2, -1))


def test_a_cold_start_says_history_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_github(monkeypatch, [], artifacts={})

    markdown = analyze_flaky_pr("--markdown")
    as_json = analyze_flaky_pr()

    assert markdown.exit_code == 0
    assert "History: none." in markdown.stdout
    assert AnalysisReport.model_validate_json(as_json.stdout).history is None


def test_an_expired_or_missing_artifact_is_skipped_without_failing_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_github(monkeypatch, [], artifacts={3: 410, 2: history_zip(2), 1: 404})

    result = analyze_flaky_pr()

    assert result.exit_code == 0
    report = AnalysisReport.model_validate_json(result.stdout)
    assert report.history is not None
    assert report.history.runs == 1
    assert "history artifact 3 skipped" in result.stderr
    assert "history artifact 1 skipped" in result.stderr


def test_a_failing_artifact_listing_is_a_warning_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_github(monkeypatch, [], status=403)

    result = analyze_flaky_pr()

    assert result.exit_code == 0
    assert AnalysisReport.model_validate_json(result.stdout).history is None
    assert "continuing without history" in result.stderr
    assert GITHUB_TOKEN not in result.stderr + result.stdout


def test_a_history_file_wins_over_the_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    github: list[httpx.Request] = []
    fake_github(monkeypatch, github, artifacts={2: history_zip(2)})

    result = analyze_flaky_pr("--json", "--history", str(FLAKY_CASE / "history.json"))

    assert result.exit_code == 0
    assert not [r for r in github if "/actions/" in r.url.path]


def test_a_run_without_a_pr_never_reads_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    github: list[httpx.Request] = []
    fake_github(monkeypatch, github, artifacts={2: history_zip(2)})

    runner.invoke(app, ["analyze", "--junit", str(FLAKY_CASE / "junit.xml"), "--json"])

    assert github == []


def test_history_runs_must_be_positive() -> None:
    result = analyze_flaky_pr("--json", "--history-runs", "0")

    assert result.exit_code == 2


class FakeComments:
    """Comment API of one pull request, backed by a list the test can look at."""

    def __init__(self, comments: list[dict[str, Any]] | None = None, status: int = 200) -> None:
        self.comments = comments or []
        self.status = status
        self.writes: list[httpx.Request] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", GITHUB_TOKEN)
        monkeypatch.setattr(
            cli,
            "_github_client",
            lambda token: GitHubClient(token, transport=httpx.MockTransport(self._handle)),
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls/7/files"):
            return httpx.Response(200, json=PR_FILES)
        if request.method != "GET":
            self.writes.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": f"bad {GITHUB_TOKEN}"})
        if request.method == "GET":
            return httpx.Response(200, json=self.comments)
        return httpx.Response(201, json={"id": 1, "body": json.loads(request.content)["body"]})


def analyze_comment(junit: Path, *extra: str) -> Any:
    args = ["analyze", "--junit", str(junit), "--repo", "acme/wallet", "--pr", "7", "--comment"]
    return runner.invoke(app, [*args, *extra])


def test_comment_posts_the_markdown_report_with_the_default_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github = FakeComments()
    github.install(monkeypatch)

    result = analyze_comment(LEDGER_DOWN)

    assert result.exit_code == 0
    assert [w.method for w in github.writes] == ["POST"]
    body = json.loads(github.writes[0].content)["body"]
    assert body.startswith("<!-- failtriage:default -->\n")
    assert "failure group" in body
    assert "comment created" in result.stderr


def test_comment_key_names_the_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    github = FakeComments()
    github.install(monkeypatch)

    analyze_comment(LEDGER_DOWN, "--comment-key", "e2e")

    assert json.loads(github.writes[0].content)["body"].startswith("<!-- failtriage:e2e -->\n")


def test_a_second_run_updates_the_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = {
        "id": 31,
        "body": "<!-- failtriage:default -->\nold",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    github = FakeComments([existing])
    github.install(monkeypatch)

    result = analyze_comment(LEDGER_DOWN)

    assert [w.method for w in github.writes] == ["PATCH"]
    assert github.writes[0].url.path.endswith("/issues/comments/31")
    assert "comment updated" in result.stderr


def test_a_green_run_without_a_comment_posts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    github = FakeComments()
    github.install(monkeypatch)

    result = analyze_comment(FIXTURES / "all_green.xml")

    assert result.exit_code == 0
    assert github.writes == []
    assert "no failures" in result.stderr


def test_comment_does_not_change_what_is_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeComments().install(monkeypatch)

    result = analyze_comment(LEDGER_DOWN, "--json")

    assert AnalysisReport.model_validate_json(result.stdout).groups


def test_a_long_report_is_cut_and_goes_whole_to_the_job_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    github = FakeComments()
    github.install(monkeypatch)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(cli, "render_markdown", lambda report: "line\n" * 30000)

    result = analyze_comment(LEDGER_DOWN)

    assert result.exit_code == 0
    body = json.loads(github.writes[0].content)["body"]
    assert len(body) <= 65536
    assert "job summary" in body
    assert summary.read_text(encoding="utf-8") == "line\n" * 30000


def test_a_short_report_leaves_the_job_summary_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeComments().install(monkeypatch)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    analyze_comment(LEDGER_DOWN)

    assert not summary.exists()


def test_a_github_failure_while_commenting_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeComments(status=403).install(monkeypatch)

    result = analyze_comment(LEDGER_DOWN)

    assert result.exit_code == 0
    assert "comment not posted" in result.stderr
    assert GITHUB_TOKEN not in result.stderr + result.stdout
    assert "bad" not in result.stderr


def test_comment_needs_repo_and_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeComments().install(monkeypatch)

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--comment"])

    assert result.exit_code == 2
    assert "--comment" in result.stderr


@pytest.mark.parametrize("key", ["", "a b", "x-->y", "a\nb"])
def test_a_comment_key_cannot_break_the_marker(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    github = FakeComments()
    github.install(monkeypatch)

    result = analyze_comment(LEDGER_DOWN, "--comment-key", key)

    assert result.exit_code == 2
    assert github.writes == []


def test_a_comment_key_without_comment_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeComments().install(monkeypatch)

    result = analyze_pr("--comment-key", "e2e")

    assert result.exit_code == 2
    assert "--comment-key" in result.stderr


def test_a_green_run_updates_an_existing_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = {
        "id": 31,
        "body": "<!-- failtriage:default -->\n1 failure group",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    github = FakeComments([existing])
    github.install(monkeypatch)

    analyze_comment(FIXTURES / "all_green.xml")

    assert [w.method for w in github.writes] == ["PATCH"]
    assert "tests passed" in json.loads(github.writes[0].content)["body"]


def test_a_secret_in_the_report_does_not_reach_the_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    github = FakeComments()
    github.install(monkeypatch)

    analyze_comment(FIXTURES / "secrets.xml")

    sent = github.writes[0].content.decode()
    assert "hunter2" not in sent
    assert "ghp_a1B2c3D4" not in sent
    assert "PRIVATE KEY" not in sent


def test_a_cut_without_a_job_summary_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeComments().install(monkeypatch)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(cli, "render_markdown", lambda report: "line\n" * 30000)

    result = analyze_comment(LEDGER_DOWN)

    assert "GITHUB_STEP_SUMMARY" in result.stderr


def test_summary_appends_the_report_to_the_job_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--markdown", "--summary"])

    assert result.exit_code == 0
    written = summary.read_text(encoding="utf-8")
    assert written.startswith("earlier step\n")
    assert "failure group" in written
    assert written.removeprefix("earlier step\n") == result.stdout


def test_summary_alone_prints_nothing_and_does_not_need_github(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--summary"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "failure group" in summary.read_text(encoding="utf-8")


def test_summary_needs_the_job_summary_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--summary"])

    assert result.exit_code == 2
    assert "GITHUB_STEP_SUMMARY" in result.stderr


def test_summary_with_a_cut_comment_writes_the_report_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeComments().install(monkeypatch)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(cli, "render_markdown", lambda report: "line\n" * 30000)

    result = analyze_comment(LEDGER_DOWN, "--summary")

    assert result.exit_code == 0
    assert summary.read_text(encoding="utf-8") == "line\n" * 30000


def test_summary_with_a_short_comment_still_writes_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeComments().install(monkeypatch)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    analyze_comment(LEDGER_DOWN, "--summary")

    assert "failure group" in summary.read_text(encoding="utf-8")


def test_a_secret_in_the_report_does_not_reach_the_job_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "secrets.xml"), "--summary"])

    written = summary.read_text(encoding="utf-8")
    assert "hunter2" not in written
    assert "ghp_a1B2c3D4" not in written
    assert "PRIVATE KEY" not in written


def test_an_empty_api_key_means_heuristics_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    result = runner.invoke(app, ["analyze", "--junit", str(LEDGER_DOWN), "--json"])

    assert result.exit_code == 0
    assert "heuristics only" in result.stderr


PLAYWRIGHT = Path(__file__).parent / "fixtures" / "playwright"


def test_analyze_reads_a_playwright_report() -> None:
    result = runner.invoke(app, ["analyze", "--playwright", str(PLAYWRIGHT / "retries.json")])

    assert result.exit_code == 0
    assert "pay.spec.ts::chromium › always broken" in result.output
    assert "1 passed on retry" in result.output


def test_analyze_json_lists_every_input_file() -> None:
    first, second = FIXTURES / "mixed.xml", FIXTURES / "retries_surefire.xml"

    result = runner.invoke(
        app, ["analyze", "--junit", str(first), "--junit", str(second), "--json"]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["run"]["inputs"] == [str(first), str(second)]


def test_several_files_of_one_format_are_analyzed_together() -> None:
    one = runner.invoke(app, ["analyze", "--junit", str(FIXTURES / "mixed.xml")])
    both = runner.invoke(
        app,
        [
            "analyze",
            "--junit",
            str(FIXTURES / "mixed.xml"),
            "--junit",
            str(FIXTURES / "retries_surefire.xml"),
        ],
    )

    assert "6 tests total" in one.output
    assert "9 tests total" in both.output


def test_junit_and_playwright_cannot_be_combined() -> None:
    result = runner.invoke(
        app,
        [
            "analyze",
            "--junit",
            str(FIXTURES / "mixed.xml"),
            "--playwright",
            str(PLAYWRIGHT / "mixed.json"),
        ],
    )

    assert result.exit_code == 2
    assert "only one of" in result.output


def test_analyze_needs_a_report() -> None:
    result = runner.invoke(app, ["analyze"])

    assert result.exit_code == 2
    assert "--junit, --playwright or --allure" in result.output


@pytest.mark.parametrize("name", ["broken.json", "zero.json", "missing.json", "wrong_shape.json"])
def test_analyze_unreadable_playwright_report_exits_two(name: str) -> None:
    result = runner.invoke(app, ["analyze", "--playwright", str(PLAYWRIGHT / name)])

    assert result.exit_code == 2
    assert "cannot read" in result.output


def test_analyze_empty_playwright_report_warns_and_exits_zero() -> None:
    result = runner.invoke(app, ["analyze", "--playwright", str(PLAYWRIGHT / "empty.json")])

    assert result.exit_code == 0
    assert "no tests found" in result.output


def test_what_a_playwright_run_sends_to_the_api_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)

    result = runner.invoke(
        app, ["analyze", "--playwright", str(PLAYWRIGHT / "secrets.json"), "--json"]
    )

    sent = json.dumps(requests) + result.output
    assert requests
    assert "<CARD>" in sent
    for secret in ["4111", "jane.doe", "hunter2", "abc.def.ghi", "ghp_", "MIIEvQ"]:
        assert secret not in sent


def test_history_reads_a_playwright_report() -> None:
    result = runner.invoke(
        app,
        [
            "history",
            "--playwright",
            str(PLAYWRIGHT / "retries.json"),
            "--sha",
            "a" * 40,
            "--run-id",
            "1",
        ],
    )

    entries = {e["test_id"]: e for e in json.loads(result.stdout)}
    assert entries["pay.spec.ts::chromium › flaky pay"]["status"] == "passed_on_retry"
    assert entries["pay.spec.ts::chromium › flaky pay"]["attempts"] == 3


ALLURE = Path(__file__).parent / "fixtures" / "allure"


def test_analyze_reads_an_allure_directory() -> None:
    result = runner.invoke(app, ["analyze", "--allure", str(ALLURE / "retries")])

    assert result.exit_code == 0
    assert "tests.pay.PayTest::always_broken" in result.output
    assert "1 passed on retry" in result.output


def test_allure_cannot_be_combined_with_another_format() -> None:
    result = runner.invoke(
        app,
        [
            "analyze",
            "--allure",
            str(ALLURE / "mixed"),
            "--playwright",
            str(PLAYWRIGHT / "mixed.json"),
        ],
    )

    assert result.exit_code == 2
    assert "only one of" in result.output


def test_the_report_names_the_allure_directory_as_its_input() -> None:
    result = runner.invoke(app, ["analyze", "--allure", str(ALLURE / "mixed"), "--json"])

    assert json.loads(result.stdout)["run"]["inputs"] == [str(ALLURE / "mixed")]


@pytest.mark.parametrize("name", ["broken", "wrong_shape", "missing"])
def test_analyze_unreadable_allure_directory_exits_two(name: str) -> None:
    result = runner.invoke(app, ["analyze", "--allure", str(ALLURE / name)])

    assert result.exit_code == 2
    assert "cannot read" in result.output


def test_analyze_empty_allure_directory_warns_and_exits_zero() -> None:
    result = runner.invoke(app, ["analyze", "--allure", str(ALLURE / "empty")])

    assert result.exit_code == 0
    assert "no tests found" in result.output


def test_what_an_allure_run_sends_to_the_api_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    fake_anthropic(monkeypatch, requests)

    result = runner.invoke(app, ["analyze", "--allure", str(ALLURE / "secrets"), "--json"])

    sent = json.dumps(requests) + result.output
    assert requests
    assert "<CARD>" in sent
    for secret in ["4111", "jane.doe", "hunter2", "abc.def.ghi", "ghp_", "MIIEvQ"]:
        assert secret not in sent


def test_history_reads_an_allure_directory() -> None:
    result = runner.invoke(
        app,
        ["history", "--allure", str(ALLURE / "retries"), "--sha", "a" * 40, "--run-id", "1"],
    )

    entries = {e["test_id"]: e for e in json.loads(result.stdout)}
    assert entries["tests.pay.PayTest::flaky_pay"]["status"] == "passed_on_retry"
    assert entries["tests.pay.PayTest::flaky_pay"]["attempts"] == 3
