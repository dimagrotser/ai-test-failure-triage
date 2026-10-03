import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from evals.lab.build import LabError, build_case
from failtriage.models import Status
from failtriage.parsers.junit import parse_junit

ROOT = Path(__file__).parent.parent
WALLET = ROOT / "evals" / "lab" / "wallet"
FIXTURES = ROOT / "tests" / "fixtures" / "lab"
SCENARIO = ROOT / "evals" / "lab" / "scenarios" / "product-bug-fee-rounding"


def test_wallet_tests_pass_without_any_scenario() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(WALLET)],
        capture_output=True,
        text=True,
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
