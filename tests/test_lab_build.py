import json
import os
import re
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


def test_build_all_rebuilds_into_an_existing_cases_dir(tmp_path: Path) -> None:
    build_all(tmp_path)
    build_all(tmp_path)

    assert (tmp_path / "product-bug-fee-rounding" / "label.yaml").is_file()


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
