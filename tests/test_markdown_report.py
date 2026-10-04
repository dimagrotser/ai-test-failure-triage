import os
from pathlib import Path

import pytest

from failtriage.grouping import group_failures
from failtriage.models import ClassifiedBy
from failtriage.parsers.junit import parse_junit
from failtriage.report.json_output import AnalysisReport, build_report
from failtriage.report.markdown import render_markdown

JUNIT = Path(__file__).parent / "fixtures" / "junit"
CASES = Path(__file__).parent.parent / "evals" / "cases"


def report_for(junit: Path) -> AnalysisReport:
    results = parse_junit(junit)
    return build_report("0.1.0", [str(junit)], results, group_failures(results))


def test_an_all_green_run_is_one_line() -> None:
    markdown = render_markdown(report_for(JUNIT / "all_green.xml"))

    assert markdown.strip() == "All 4 tests passed or were skipped."


def test_a_run_without_tests_is_one_line() -> None:
    markdown = render_markdown(report_for(JUNIT / "empty.xml"))

    assert markdown.strip() == "No tests found."


def test_the_summary_line_counts_failures_retries_and_groups() -> None:
    markdown = render_markdown(report_for(JUNIT / "mixed.xml"))

    assert markdown.startswith("**3 failed tests in 3 groups**, 0 passed on retry, 6 tests total.")


def test_each_group_gets_a_section_with_category_confidence_and_next_step() -> None:
    markdown = render_markdown(report_for(JUNIT / "mixed.xml"))

    assert markdown.count("\n### ") == 3
    assert "### 1. unknown, low confidence" in markdown
    assert "### 2. environment, medium confidence" in markdown
    assert "Next step: Check the service or setting in the quote, then rerun" in markdown
    assert "`tests.test_cart::test_total_with_discount`" in markdown


def test_a_group_shows_its_evidence_quotes() -> None:
    report = report_for(JUNIT / "mixed.xml")
    quotes = "\n".join(report.groups[1].classification.evidence)

    markdown = render_markdown(report)

    assert f"Evidence:\n\n```text\n{quotes}\n```" in markdown


def test_a_group_without_evidence_says_so() -> None:
    markdown = render_markdown(report_for(JUNIT / "minimal.xml"))

    assert "### 1. unknown, low confidence" in markdown
    assert "No evidence found, so the category is unknown." in markdown
    assert "Evidence:" not in markdown


def test_a_quote_with_backticks_cannot_close_its_fence() -> None:
    report = report_for(JUNIT / "mixed.xml")
    group = report.groups[0]
    evidence = ["x ``` <script> y", *group.classification.evidence[1:]]
    group.classification = group.classification.model_copy(update={"evidence": evidence})

    markdown = render_markdown(report)

    assert "````text\nx ``` <script> y\n" in markdown


def test_the_report_says_history_is_missing() -> None:
    markdown = render_markdown(report_for(JUNIT / "mixed.xml"))

    assert "History: none." in markdown
    assert markdown.index("History: none.") < markdown.index("### 1.")


def test_only_groups_the_llm_did_not_classify_are_marked_as_not_sent() -> None:
    report = report_for(JUNIT / "mixed.xml")
    first, *rest = report.groups
    llm = first.classification.model_copy(update={"classified_by": ClassifiedBy.LLM})
    report.groups = [first.model_copy(update={"classification": llm}), *rest]

    markdown = render_markdown(report)

    assert markdown.count("not sent to LLM") == 2
    assert markdown.split("### 2.")[0].count("not sent to LLM") == 0


def test_a_long_test_list_is_cut_after_five_tests() -> None:
    markdown = render_markdown(report_for(CASES / "product-bug-deposit-float" / "junit.xml"))

    assert "Tests (41):" in markdown
    assert markdown.count("`tests.test_balance::") == 5
    assert "and 36 more" in markdown


SNAPSHOTS = Path(__file__).parent / "fixtures" / "report"


@pytest.mark.parametrize(
    ("name", "junit"),
    [
        ("mixed", JUNIT / "mixed.xml"),
        ("retries_surefire", JUNIT / "retries_surefire.xml"),
        ("minimal", JUNIT / "minimal.xml"),
        ("all_green", JUNIT / "all_green.xml"),
        ("product_bug_deposit_float", CASES / "product-bug-deposit-float" / "junit.xml"),
    ],
)
def test_the_report_matches_its_snapshot(name: str, junit: Path) -> None:
    snapshot = SNAPSHOTS / f"{name}.md"
    markdown = render_markdown(report_for(junit))

    if os.environ.get("UPDATE_SNAPSHOTS"):
        snapshot.parent.mkdir(exist_ok=True)
        snapshot.write_text(markdown)

    assert markdown == snapshot.read_text()
