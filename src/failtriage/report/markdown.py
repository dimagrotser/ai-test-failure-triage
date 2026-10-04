import re

from failtriage.models import ClassifiedBy
from failtriage.report.json_output import AnalysisReport, GroupReport

_TESTS_SHOWN = 5


def render_markdown(report: AnalysisReport) -> str:
    """Render the analysis as a short Markdown report, from the JSON model alone."""
    if not report.run.tests:
        return "No tests found.\n"
    if not report.groups:
        return f"All {report.run.tests} tests passed or were skipped.\n"
    run = report.run
    failed = f"{run.failed} failed {_plural(run.failed, 'test')}"
    groups = f"{len(report.groups)} {_plural(len(report.groups), 'group')}"
    parts = [
        f"**{failed} in {groups}**, {run.passed_on_retry} passed on retry, {run.tests} tests total."
    ]
    if report.history is None:
        parts.append(
            "History: none. No earlier runs on main were read, "
            "so nothing here says a test was stable before."
        )
    parts += [_group_section(n, g) for n, g in enumerate(report.groups, start=1)]
    return "\n\n".join(parts) + "\n"


def _group_section(number: int, group: GroupReport) -> str:
    result = group.classification
    shown = [f"`{t.test_id}`" for t in group.tests[:_TESTS_SHOWN]]
    if len(group.tests) > _TESTS_SHOWN:
        shown.append(f"and {len(group.tests) - _TESTS_SHOWN} more")
    tests = ", ".join(shown)
    lines = [
        f"### {number}. {result.category.value}, {result.confidence.value} confidence",
        result.summary,
        f"Tests ({len(group.tests)}): {tests}",
    ]
    if result.evidence:
        lines.append("Evidence:\n\n" + _fenced(result.evidence))
    else:
        lines.append("No evidence found, so the category is unknown.")
    lines.append(f"Next step: {result.next_step}")
    if result.classified_by is ClassifiedBy.HEURISTICS:
        lines.append("Classified by heuristics, not sent to LLM.")
    return "\n\n".join(lines)


def _fenced(quotes: list[str]) -> str:
    text = "\n".join(quotes)
    # A quote may hold backticks, a longer fence keeps it from closing the block early.
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else noun + "s"
