import os
from pathlib import Path

import pytest

from failtriage.grouping import group_failures
from failtriage.history import load_history
from failtriage.models import ClassifiedBy, SignalName
from failtriage.parsers.junit import parse_junit
from failtriage.report.json_output import AnalysisReport, DiffInfo, build_report
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

    assert markdown.startswith(
        "**3 failure groups**: 3 failed tests, 0 passed on retry, 6 tests total."
    )


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
    report = report_for(JUNIT / "mixed.xml")
    group = report.groups[0]
    group.tests = [group.tests[0].model_copy(update={"test_id": f"a::t{n}"}) for n in range(41)]

    markdown = render_markdown(report)

    assert "Tests (41):" in markdown
    assert markdown.count("`a::t") == 5
    assert "and 36 more" in markdown


def test_parametrized_cases_are_listed_once() -> None:
    markdown = render_markdown(report_for(CASES / "product-bug-deposit-float" / "junit.xml"))

    assert "Tests (2):" in markdown
    assert markdown.count("`tests.test_balance::test_deposit_keeps_every_cent`") == 1


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


def test_a_test_id_with_backticks_cannot_close_its_code_span() -> None:
    report = report_for(JUNIT / "mixed.xml")
    group = report.groups[0]
    group.tests = [group.tests[0].model_copy(update={"test_id": "a::t[`x`]"})]

    assert "`` a::t[`x`] ``" not in render_markdown(report)
    assert "``a::t[`x`]``" in render_markdown(report)


def test_a_one_test_run_uses_singular_nouns() -> None:
    markdown = render_markdown(report_for(JUNIT / "minimal.xml"))

    assert markdown.startswith(
        "**1 failure group**: 1 failed test, 0 passed on retry, 1 test total."
    )


def report_with_diff(diff: DiffInfo | None) -> AnalysisReport:
    results = parse_junit(JUNIT / "mixed.xml")
    return build_report("0.1.0", ["mixed.xml"], results, group_failures(results), diff=diff)


def test_a_run_without_a_pull_request_says_nothing_about_the_diff() -> None:
    assert "Diff" not in render_markdown(report_with_diff(None))


def test_missing_hunks_are_named_in_the_report() -> None:
    diff = DiffInfo(changed_files=["a.py", "logo.png"], files_without_hunks=["logo.png"])

    markdown = render_markdown(report_with_diff(diff))

    assert "Hunks missing for `logo.png`: GitHub returned no patch" in markdown


def test_a_diff_with_every_hunk_has_no_note() -> None:
    diff = DiffInfo(changed_files=["a.py"], files_without_hunks=[])

    assert "Hunks missing" not in render_markdown(report_with_diff(diff))


def test_many_files_without_hunks_are_cut_to_a_few_names() -> None:
    names = [f"f{n}.png" for n in range(8)]
    diff = DiffInfo(changed_files=names, files_without_hunks=names)

    markdown = render_markdown(report_with_diff(diff))

    assert "`f4.png`" in markdown
    assert "`f5.png`" not in markdown
    assert "and 3 more" in markdown


def test_an_unavailable_diff_is_reported() -> None:
    markdown = render_markdown(report_with_diff(DiffInfo(available=False)))

    assert "Diff: not available" in markdown


def test_the_changed_file_signal_is_in_the_group_signals() -> None:
    results = parse_junit(JUNIT / "mixed.xml")
    groups = group_failures(results)
    frame = groups[0].signature.frame
    assert frame
    diff = DiffInfo(changed_files=[frame.rsplit(":", 1)[0]], files_without_hunks=[])

    report = build_report("0.1.0", [], results, groups, diff=diff)

    names = {s.name for s in report.groups[0].signals}
    assert SignalName.TOUCHES_CHANGED_FILE in names


def test_a_report_with_history_says_how_much_was_read() -> None:
    results = parse_junit(CASES / "flaky-random-transfer-id" / "junit.xml")
    history = load_history(CASES / "flaky-random-transfer-id" / "history.json")
    report = build_report("0.1.0", ["j"], results, group_failures(results), history=history)

    markdown = render_markdown(report)

    assert "History: 2 runs on main, 17 tests." in markdown
    assert "History: none" not in markdown


def test_an_empty_history_is_reported_as_none() -> None:
    results = parse_junit(JUNIT / "mixed.xml")
    report = build_report("0.1.0", ["j"], results, group_failures(results), history=[])

    assert report.history is None
    assert "History: none." in render_markdown(report)
