import shutil
from pathlib import Path

import pytest

from failtriage.evaluate import EvalError, evaluate, render_eval
from failtriage.models import Category

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
    output = render_eval(evaluate(DATASET))

    assert "5 failure groups" in output
    assert "Accuracy: 3/5 (60%)" in output
    assert _row(output, "flaky") == ["flaky", "1", "1", "100%"]
    assert _row(output, "product_bug") == ["product_bug", "1", "0", "0%"]
    assert _row(output, "test_bug") == ["test_bug", "2", "1", "50%"]


def test_a_category_without_cases_is_listed_with_a_dash() -> None:
    output = render_eval(evaluate(DATASET))

    assert _row(output, "unknown") == ["unknown", "0", "0", "-"]


def test_accuracy_is_reported_per_source() -> None:
    output = render_eval(evaluate(DATASET))

    assert _row(output, "injected") == ["injected", "2", "2", "100%"]
    assert _row(output, "mutation") == ["mutation", "1", "0", "0%"]
    assert _row(output, "real") == ["real", "2", "1", "50%"]


def test_a_source_without_cases_is_listed_with_a_dash(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    output = render_eval(evaluate(tmp_path))

    assert _row(output, "mutation") == ["mutation", "0", "0", "-"]
    assert _row(output, "real") == ["real", "0", "0", "-"]


def test_confusion_matrix_counts_label_against_prediction() -> None:
    output = render_eval(evaluate(DATASET))

    assert "Confusion matrix (rows: label, columns: predicted)" in output
    matrix = output.split("Confusion matrix")[1].split("Misses")[0]
    assert _row(matrix, "product_bug") == ["product_bug", "0", "0", "0", "0", "1"]
    assert _row(matrix, "test_bug") == ["test_bug", "0", "1", "0", "0", "1"]
    assert _row(matrix, "flaky") == ["flaky", "0", "0", "1", "0", "0"]


def test_misses_name_the_case_and_both_categories() -> None:
    output = render_eval(evaluate(DATASET))

    misses = output.split("Misses")[1]
    assert "product-bug-assert: product_bug -> unknown" in misses
    assert "test-bug-two-groups: test_bug -> unknown" in misses
    assert "flaky-retry" not in misses


def test_no_misses_is_said_plainly(tmp_path: Path) -> None:
    shutil.copytree(DATASET / "cases" / "flaky-retry", tmp_path / "cases" / "flaky-retry")

    assert "Misses: none" in render_eval(evaluate(tmp_path))


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
