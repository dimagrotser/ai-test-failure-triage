import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from evals.lab.build import LabError, build_all, build_case
from evals.lab.environment import lab_environment
from failtriage.models import Status
from failtriage.parsers.junit import parse_junit

ROOT = Path(__file__).parent.parent
WALLET = ROOT / "evals" / "lab" / "wallet"
FIXTURES = ROOT / "tests" / "fixtures" / "lab"
SCENARIOS = ROOT / "evals" / "lab" / "scenarios"
SCENARIO = SCENARIOS / "product-bug-fee-rounding"


def test_wallet_tests_pass_without_any_scenario(tmp_path: Path) -> None:
    with lab_environment(tmp_path, None) as env:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(WALLET)],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )

    assert result.returncode == 0, result.stdout


def test_build_case_records_the_failing_run_and_the_patch(tmp_path: Path) -> None:
    case = build_case(SCENARIO, tmp_path)

    assert case == tmp_path / "product-bug-fee-rounding"
    failed = [r for r in parse_junit(case / "junit.xml") if r.status is Status.FAILED]
    assert [r.test_id for r in failed] == [
        "tests.test_fees::test_fee_is_one_and_a_half_percent_rounded_half_up"
    ]
    assert (case / "diff.patch").read_text() == (SCENARIO / "diff.patch").read_text()


def test_label_has_category_source_scenario_and_notes(tmp_path: Path) -> None:
    case = build_case(SCENARIO, tmp_path)

    label = yaml.safe_load((case / "label.yaml").read_text())
    assert label["category"] == "product_bug"
    assert label["source"] == "injected"
    assert label["scenario"] == "fee rounding truncates instead of rounding half up"
    assert "ROUND_DOWN" in label["notes"]


def test_history_records_a_passing_run_before_the_patch(tmp_path: Path) -> None:
    case = build_case(SCENARIO, tmp_path)

    history = json.loads((case / "history.json").read_text())
    assert history
    assert {tuple(sorted(entry)) for entry in history} == {
        ("attempts", "run_id", "sha", "status", "test_id")
    }
    assert {entry["status"] for entry in history} == {"passed"}
    assert "tests.test_fees::test_fee_is_one_and_a_half_percent_rounded_half_up" in {
        entry["test_id"] for entry in history
    }
    assert {entry["attempts"] for entry in history} == {1}
    assert len({entry["sha"] for entry in history}) == 1
    assert re.fullmatch(r"[0-9a-f]{40}", history[0]["sha"])


def test_scenario_that_does_not_break_the_tests_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LabError, match="does not fail"):
        build_case(FIXTURES / "harmless", tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_scenario_that_edits_the_tests_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LabError, match="tests"):
        build_case(FIXTURES / "edits-tests", tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_two_builds_produce_byte_identical_files(tmp_path: Path) -> None:
    first = build_case(SCENARIO, tmp_path / "first")
    second = build_case(SCENARIO, tmp_path / "second")

    for name in ["junit.xml", "diff.patch", "history.json", "label.yaml"]:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def _snapshot(root: Path) -> dict[str, bytes]:
    files = (p for p in sorted(root.rglob("*")) if p.is_file())
    return {str(p.relative_to(root)): p.read_bytes() for p in files}


def _seed_cases(cases: Path) -> dict[str, bytes]:
    (cases / "old-case").mkdir(parents=True)
    (cases / "old-case" / "label.yaml").write_text("category: unknown\n")
    return _snapshot(cases)


def test_build_all_rebuilds_the_whole_dataset_byte_for_byte(tmp_path: Path) -> None:
    cases = tmp_path / "cases"

    build_all(SCENARIOS, cases)
    first = _snapshot(cases)
    build_all(SCENARIOS, cases)

    assert {name.split("/")[0] for name in first} == {p.name for p in SCENARIOS.iterdir()}
    assert _snapshot(cases) == first


def test_case_does_not_depend_on_the_host_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = SCENARIOS / "environment-missing-ledger-url"
    first = build_case(scenario, tmp_path / "first")

    for name in set(os.environ) - {"PATH", "HOME"}:
        monkeypatch.delenv(name)
    monkeypatch.setenv("LAB_HOST_PROBE", "different")
    second = build_case(scenario, tmp_path / "second")

    assert (first / "junit.xml").read_bytes() == (second / "junit.xml").read_bytes()


def test_rebuild_drops_cases_whose_scenario_is_gone(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    _seed_cases(cases)
    scenarios = tmp_path / "scenarios"
    shutil.copytree(SCENARIO, scenarios / SCENARIO.name)

    build_all(scenarios, cases)

    assert [p.name for p in cases.iterdir()] == ["product-bug-fee-rounding"]


def test_files_next_to_the_scenarios_are_ignored(tmp_path: Path) -> None:
    scenarios = tmp_path / "scenarios"
    shutil.copytree(SCENARIO, scenarios / SCENARIO.name)
    (scenarios / ".DS_Store").write_text("")

    build_all(scenarios, tmp_path / "cases")

    assert [p.name for p in (tmp_path / "cases").iterdir()] == ["product-bug-fee-rounding"]


def test_failing_scenario_stops_the_build_and_leaves_the_cases_untouched(tmp_path: Path) -> None:
    cases = tmp_path / "cases"
    before = _seed_cases(cases)
    scenarios = tmp_path / "scenarios"
    shutil.copytree(SCENARIO, scenarios / "a-fine")
    shutil.copytree(FIXTURES / "environment-patch-breaks", scenarios / "b-broken")

    with pytest.raises(LabError, match="restoring the environment of b-broken"):
        build_all(scenarios, cases)

    assert _snapshot(cases) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cases", "scenarios"]


def test_test_bug_case_is_built_from_a_patch_that_only_edits_tests(tmp_path: Path) -> None:
    case = build_case(SCENARIOS / "test-bug-expected-value", tmp_path)

    failed = [r for r in parse_junit(case / "junit.xml") if r.status is Status.FAILED]
    assert [r.test_id for r in failed] == [
        "tests.test_transfers::test_transfer_moves_the_amount_and_charges_the_fee_to_the_sender"
    ]
    label = yaml.safe_load((case / "label.yaml").read_text())
    assert label["category"] == "test_bug"
    assert "98.50" in label["notes"]


def test_test_bug_scenario_that_edits_app_code_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LabError, match="may only change tests/"):
        build_case(FIXTURES / "test-bug-edits-app", tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("fixture", "message"),
    [("unknown-category", "unsupported category"), ("no-notes", "no notes")],
)
def test_scenario_with_an_incomplete_or_unknown_label_is_rejected(
    tmp_path: Path, fixture: str, message: str
) -> None:
    with pytest.raises(LabError, match=message):
        build_case(FIXTURES / fixture, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_stale_selector_case_fails_on_the_missing_testid(tmp_path: Path) -> None:
    case = build_case(SCENARIOS / "test-bug-stale-selector", tmp_path)

    [failed] = [r for r in parse_junit(case / "junit.xml") if r.status is Status.FAILED]
    assert failed.test_id == "tests.test_receipt::test_receipt_shows_the_amount"
    assert failed.attempts[0].message is not None
    assert "no element with data-testid='amount'" in failed.attempts[0].message


def test_fixture_leak_case_fails_tests_that_run_after_the_leaking_one(tmp_path: Path) -> None:
    case = build_case(SCENARIOS / "test-bug-fixture-leak", tmp_path)

    results = parse_junit(case / "junit.xml")
    failed_files = {r.test_id.split("::")[0] for r in results if r.status is Status.FAILED}
    assert failed_files == {"tests.test_receipt", "tests.test_transfers"}
    assert all(
        r.status is Status.PASSED for r in results if r.test_id.startswith("tests.test_fees::")
    )


def test_deposit_case_fails_every_cent_deposit_the_same_way(tmp_path: Path) -> None:
    case = build_case(SCENARIOS / "product-bug-deposit-float", tmp_path)

    failed = [r for r in parse_junit(case / "junit.xml") if r.status is Status.FAILED]
    assert len(failed) == 41
    assert {r.test_id for r in failed} == {
        "tests.test_balance::test_deposit_increases_the_balance",
        "tests.test_balance::test_deposit_keeps_every_cent",
    }
    assert all("unsupported operand" in (r.attempts[0].message or "") for r in failed)


def test_junit_with_object_reprs_is_still_byte_identical_across_builds(tmp_path: Path) -> None:
    scenario = SCENARIOS / "test-bug-expected-value"

    first = build_case(scenario, tmp_path / "first")
    second = build_case(scenario, tmp_path / "second")

    assert (first / "junit.xml").read_bytes() == (second / "junit.xml").read_bytes()


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("environment-no-condition", "no condition"),
        ("environment-unknown-condition", "unsupported condition"),
        ("environment-edits-tests", "may only change wallet/"),
        ("environment-patch-breaks", "restoring the environment"),
    ],
)
def test_environment_scenario_that_cannot_be_trusted_is_rejected(
    tmp_path: Path, fixture: str, message: str
) -> None:
    with pytest.raises(LabError, match=message):
        build_case(FIXTURES / fixture, tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("scenario", "condition", "failed_test", "marker"),
    [
        ("environment-ledger-down", "service_down", "test_ledger", "Connection refused"),
        ("environment-ledger-dns", "dns_failure", "test_ledger", "urlopen error"),
        ("environment-ledger-timeout", "timeout", "test_ledger", "timed out"),
        ("environment-missing-ledger-url", "missing_env_var", "test_ledger", "WALLET_LEDGER_URL"),
        (
            "environment-read-only-statements",
            "read_only_dir",
            "test_statements",
            "Permission denied",
        ),
    ],
)
def test_environment_case_fails_only_where_the_environment_is_broken(
    tmp_path: Path, scenario: str, condition: str, failed_test: str, marker: str
) -> None:
    case = build_case(SCENARIOS / scenario, tmp_path)

    [failed] = [r for r in parse_junit(case / "junit.xml") if r.status is Status.FAILED]
    assert failed.test_id.startswith(f"tests.{failed_test}::")
    assert failed.attempts[0].message is not None
    assert marker in failed.attempts[0].message
    label = yaml.safe_load((case / "label.yaml").read_text())
    assert label["category"] == "environment"
    assert label["condition"] == condition
    assert (case / "diff.patch").read_text() == (SCENARIOS / scenario / "diff.patch").read_text()


@pytest.mark.parametrize(
    "scenario", ["environment-ledger-down", "environment-read-only-statements"]
)
def test_environment_junit_with_ports_and_temp_paths_is_byte_identical(
    tmp_path: Path, scenario: str
) -> None:
    first = build_case(SCENARIOS / scenario, tmp_path / "first")
    second = build_case(SCENARIOS / scenario, tmp_path / "second")

    junit = (first / "junit.xml").read_text()
    assert junit == (second / "junit.xml").read_text()
    assert "/var/folders" not in junit
    assert "/private" not in junit
    assert not re.search(r"127\.0\.0\.1(:|', )(?!0\b)\d+", junit)


FLAKY_CASES = [
    (
        "flaky-random-transfer-id",
        "randomness",
        "tests.test_ids::test_three_transfers_get_different_ids",
    ),
    (
        "flaky-cold-settlement-clock",
        "timing",
        "tests.test_settlement::test_settlement_is_confirmed_before_the_deadline",
    ),
    (
        "flaky-rates-cache-order",
        "order_dependence",
        "tests.test_rates::test_convert_to_euros",
    ),
]


@pytest.mark.parametrize(("scenario", "kind", "test_id"), FLAKY_CASES)
def test_flaky_case_fails_the_first_attempt_and_passes_on_retry(
    tmp_path: Path, scenario: str, kind: str, test_id: str
) -> None:
    case = build_case(SCENARIOS / scenario, tmp_path)

    results = {r.test_id: r for r in parse_junit(case / "junit.xml")}
    assert results[test_id].status is Status.PASSED_ON_RETRY
    assert [a.status for a in results[test_id].attempts] == [Status.FAILED, Status.PASSED]
    assert {r.status for r in results.values()} <= {Status.PASSED, Status.PASSED_ON_RETRY}
    label = yaml.safe_load((case / "label.yaml").read_text())
    assert label["category"] == "flaky"
    assert label["kind"] == kind


@pytest.mark.parametrize(("scenario", "kind", "test_id"), FLAKY_CASES)
def test_flaky_history_has_a_failed_and_a_passed_run_on_the_same_sha(
    tmp_path: Path, scenario: str, kind: str, test_id: str
) -> None:
    case = build_case(SCENARIOS / scenario, tmp_path)

    history = json.loads((case / "history.json").read_text())
    entries = [e for e in history if e["test_id"] == test_id]
    assert sorted((e["run_id"], e["status"]) for e in entries) == [
        (1, "failed"),
        (2, "passed_on_retry"),
    ]
    assert len({e["sha"] for e in entries}) == 1


@pytest.mark.parametrize("scenario", [case[0] for case in FLAKY_CASES])
def test_flaky_case_is_byte_identical_across_builds(tmp_path: Path, scenario: str) -> None:
    first = build_case(SCENARIOS / scenario, tmp_path / "first")
    second = build_case(SCENARIOS / scenario, tmp_path / "second")

    for name in ["junit.xml", "diff.patch", "history.json", "label.yaml"]:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("flaky-unknown-kind", "unsupported kind"),
        ("flaky-never-fails", "does not fail"),
        ("flaky-fails-after-retry", "does not pass on retry"),
    ],
)
def test_flaky_scenario_that_cannot_be_trusted_is_rejected(
    tmp_path: Path, fixture: str, message: str
) -> None:
    with pytest.raises(LabError, match=message):
        build_case(FIXTURES / fixture, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_order_dependent_test_passes_without_retries_only_after_the_failing_one(
    tmp_path: Path,
) -> None:
    case = build_case(SCENARIOS / "flaky-rates-cache-order", tmp_path)

    history = json.loads((case / "history.json").read_text())
    first_run = {e["test_id"]: e["status"] for e in history if e["run_id"] == 1}
    assert first_run["tests.test_rates::test_convert_to_euros"] == "failed"
    assert first_run["tests.test_rates::test_convert_again"] == "passed"


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("unknown-passes-on-retry", "passes on retry"),
        ("unknown-never-fails", "does not fail"),
        ("unknown-bad-history", "unsupported history"),
    ],
)
def test_unknown_scenario_that_hides_a_provable_cause_is_rejected(
    tmp_path: Path, fixture: str, message: str
) -> None:
    with pytest.raises(LabError, match=message):
        build_case(FIXTURES / fixture, tmp_path)

    assert list(tmp_path.iterdir()) == []


UNKNOWN_CASES = [
    (
        "unknown-dormant-new-account",
        "tests.test_dormant::test_a_new_account_is_not_dormant",
        "assert not True",
    ),
    (
        "unknown-amount-with-comma",
        "tests.test_parse_amount::test_amount_with_a_thousands_separator",
        "InvalidOperation",
    ),
    (
        "unknown-interest-rate-mismatch",
        "tests.test_interest::test_monthly_interest_on_a_thousand",
        "Decimal('2.50')",
    ),
]


@pytest.mark.parametrize(("scenario", "test_id", "marker"), UNKNOWN_CASES)
def test_unknown_case_fails_without_passing_on_retry(
    tmp_path: Path, scenario: str, test_id: str, marker: str
) -> None:
    case = build_case(SCENARIOS / scenario, tmp_path)

    results = parse_junit(case / "junit.xml")
    [failed] = [r for r in results if r.status is Status.FAILED]
    assert failed.test_id == test_id
    assert failed.attempts[0].message is not None
    assert marker in failed.attempts[0].message
    assert {r.status for r in results} == {Status.PASSED, Status.FAILED}
    label = yaml.safe_load((case / "label.yaml").read_text())
    assert label["category"] == "unknown"
    assert len(label["notes"]) > 100


@pytest.mark.parametrize(
    ("scenario", "has_history"),
    [
        ("unknown-dormant-new-account", True),
        ("unknown-amount-with-comma", True),
        ("unknown-interest-rate-mismatch", False),
    ],
)
def test_history_is_empty_only_when_the_scenario_says_none(
    tmp_path: Path, scenario: str, has_history: bool
) -> None:
    case = build_case(SCENARIOS / scenario, tmp_path)

    history = json.loads((case / "history.json").read_text())
    assert bool(history) is has_history


@pytest.mark.parametrize("scenario", [case[0] for case in UNKNOWN_CASES])
def test_unknown_case_is_byte_identical_across_builds(tmp_path: Path, scenario: str) -> None:
    first = build_case(SCENARIOS / scenario, tmp_path / "first")
    second = build_case(SCENARIOS / scenario, tmp_path / "second")

    for name in ["junit.xml", "diff.patch", "history.json", "label.yaml"]:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
