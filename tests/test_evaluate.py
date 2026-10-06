import json
import shutil
from pathlib import Path

import pytest

from failtriage.evaluate import EvalError, evaluate, render_eval
from failtriage.models import Category, ClassifiedBy, Confidence

DATASET = Path(__file__).parent / "fixtures" / "evals"


def _row(output: str, name: str) -> list[str]:
    return next(line.split() for line in output.splitlines() if line.startswith(name + " "))


def test_every_failure_group_is_scored_against_the_label_of_its_case() -> None:
    result = evaluate(DATASET)

    scored = {(g.case_id, g.predicted) for g in result.groups}
    assert scored == {
        ("flaky-retry", Category.FLAKY),
        ("environment-refused", Category.ENVIRONMENT),
        ("product-bug-assert", Category.UNKNOWN),
        ("test-bug-two-groups", Category.TEST_BUG),
        ("test-bug-two-groups", Category.UNKNOWN),
    }


def test_accuracy_is_reported_per_category_with_weak_ones_included() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "5 failure groups" in output
    assert "Accuracy: 3/5 (60%)" in output
    assert _row(output, "flaky") == ["flaky", "1", "1", "100%"]
    assert _row(output, "product_bug") == ["product_bug", "1", "0", "0%"]
    assert _row(output, "test_bug") == ["test_bug", "2", "1", "50%"]


def test_a_category_without_cases_is_listed_with_a_dash() -> None:
    output = render_eval([evaluate(DATASET)])

    assert _row(output, "unknown") == ["unknown", "0", "0", "-"]


def test_accuracy_is_reported_per_source() -> None:
    output = render_eval([evaluate(DATASET)])

    assert _row(output, "injected") == ["injected", "2", "2", "100%"]
    assert _row(output, "mutation") == ["mutation", "1", "0", "0%"]
    assert _row(output, "real") == ["real", "2", "1", "50%"]


def test_a_source_without_cases_is_listed_with_a_dash(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    output = render_eval([evaluate(tmp_path)])

    assert _row(output, "mutation") == ["mutation", "0", "0", "-"]
    assert _row(output, "real") == ["real", "0", "0", "-"]


def test_confusion_matrix_counts_label_against_prediction() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "Confusion matrix (rows: label, columns: predicted)" in output
    matrix = output.split("Confusion matrix")[1].split("Misses")[0]
    assert _row(matrix, "product_bug") == ["product_bug", "0", "0", "0", "0", "1"]
    assert _row(matrix, "test_bug") == ["test_bug", "0", "1", "0", "0", "1"]
    assert _row(matrix, "flaky") == ["flaky", "0", "0", "1", "0", "0"]


def test_misses_name_the_case_and_both_categories() -> None:
    output = render_eval([evaluate(DATASET)])

    misses = output.split("Misses")[1]
    assert "product-bug-assert: product_bug -> unknown" in misses
    assert "test-bug-two-groups: test_bug -> unknown" in misses
    assert "flaky-retry" not in misses


def test_no_misses_is_said_plainly(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    assert "Misses: none" in render_eval([evaluate(tmp_path)])


def _dataset(tmp_path: Path, label: str | None, junit: str | None) -> Path:
    case = tmp_path / "cases" / "broken-case"
    case.mkdir(parents=True)
    if label is not None:
        (case / "label.yaml").write_text(label)
    if junit is not None:
        (case / "junit.xml").write_text(junit)
    return tmp_path


GREEN = '<testsuites><testsuite><testcase classname="t" name="ok"/></testsuite></testsuites>'


@pytest.mark.parametrize(
    ("label", "junit"),
    [
        (None, GREEN),
        ("category: nonsense\nsource: injected\n", GREEN),
        ("category: flaky\nsource: invented\n", GREEN),
        ("category: flaky\nsource: injected\n", None),
        ("category: flaky\nsource: injected\n", "<not xml"),
        ("category: flaky\nsource: injected\n", GREEN),
    ],
    ids=["no-label", "bad-category", "bad-source", "no-junit", "broken-junit", "no-failures"],
)
def test_a_broken_case_stops_the_run_and_is_named(
    tmp_path: Path, label: str | None, junit: str | None
) -> None:
    with pytest.raises(EvalError, match="broken-case"):
        evaluate(_dataset(tmp_path, label, junit))


def test_a_dataset_without_cases_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="no cases"):
        evaluate(tmp_path)


def _with_history(tmp_path: Path, history: str) -> Path:
    case = tmp_path / "cases" / "product-bug-assert"
    shutil.copytree(DATASET / "cases" / "product-bug-assert", case)
    (case / "history.json").write_text(history)
    return tmp_path


def test_the_history_of_a_case_reaches_the_heuristics(tmp_path: Path) -> None:
    test_id = "tests.test_fees::test_fee_rounds_half_up"
    runs = [
        {"test_id": test_id, "status": status, "attempts": 1, "sha": "a" * 40, "run_id": run}
        for run, status in enumerate(["passed", "failed"], start=1)
    ]

    result = evaluate(_with_history(tmp_path, json.dumps(runs)))

    assert [g.predicted for g in result.groups] == [Category.FLAKY]


def test_a_case_without_history_is_scored_cold(tmp_path: Path) -> None:
    result = evaluate(_with_history(tmp_path, "[]"))

    assert [g.predicted for g in result.groups] == [Category.UNKNOWN]


def test_an_invalid_history_stops_the_run_and_is_named(tmp_path: Path) -> None:
    with pytest.raises(EvalError, match="product-bug-assert.*history.json"):
        evaluate(_with_history(tmp_path, "not json"))


def test_every_scored_group_keeps_its_confidence_and_who_classified_it() -> None:
    result = evaluate(DATASET)

    by_case = {(g.case_id, g.predicted): (g.confidence, g.classified_by) for g in result.groups}
    assert by_case[("flaky-retry", Category.FLAKY)] == (Confidence.HIGH, ClassifiedBy.HEURISTICS)
    assert by_case[("product-bug-assert", Category.UNKNOWN)][0] is Confidence.LOW


def test_the_run_states_the_share_of_unknown_and_accuracy_among_high_confidence() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "Predicted unknown: 2/5 (40%)" in output
    assert "High confidence: 1/1 correct (100%)" in output


def test_a_run_without_high_confidence_answers_says_so(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "environment-refused", tmp_path / "cases" / "e")

    assert "High confidence: no answers" in render_eval([evaluate(tmp_path)])


def test_categories_below_half_are_named_as_weak() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "Weak categories: product_bug 0%" in output


def test_no_weak_category_is_said_plainly(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    assert "Weak categories: none below 50%" in render_eval([evaluate(tmp_path)])


def test_the_small_size_of_real_is_stated() -> None:
    output = render_eval([evaluate(DATASET)])

    assert "The real source has 2 groups from 1 case, too few to draw conclusions." in output


def test_a_dataset_without_real_cases_says_nothing_is_known_about_real_failures(
    tmp_path: Path,
) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    output = render_eval([evaluate(tmp_path)])

    assert "The real source has no cases yet" in output
