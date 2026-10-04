import re

from failtriage.models import ClassifiedBy
from failtriage.report.json_output import AnalysisReport, DiffInfo, GroupReport

_NAMES_SHOWN = 5


def render_markdown(report: AnalysisReport) -> str:
    """Render the analysis as a short Markdown report, from the JSON model alone."""
    if not report.run.tests:
        return "No tests found.\n"
    if not report.groups:
        return f"All {report.run.tests} tests passed or were skipped.\n"
    run = report.run
    groups = f"{len(report.groups)} failure {_plural(len(report.groups), 'group')}"
    parts = [
        f"**{groups}**: {run.failed} failed {_plural(run.failed, 'test')}, "
        f"{run.passed_on_retry} passed on retry, {run.tests} {_plural(run.tests, 'test')} total."
    ]
    if report.history is None:
        parts.append(
            "History: none. No earlier runs on main were read, "
            "so nothing here says a test was stable before."
        )
    if note := _diff_note(report.diff):
        parts.append(note)
    parts += [_group_section(n, g) for n, g in enumerate(report.groups, start=1)]
    return "\n\n".join(parts) + "\n"


def _diff_note(diff: DiffInfo | None) -> str | None:
    if diff is None:
        return None
    if not diff.available:
        return "Diff: not available. The pull request files could not be read from GitHub."
    if not diff.files_without_hunks:
        return None
    names = diff.files_without_hunks
    shown = [_code(name) for name in names[:_NAMES_SHOWN]]
    if len(names) > _NAMES_SHOWN:
        shown.append(f"and {len(names) - _NAMES_SHOWN} more")
    return (
        f"Hunks missing for {', '.join(shown)}: GitHub returned no patch, "
        "so only the file names were used."
    )


def _group_section(number: int, group: GroupReport) -> str:
    result = group.classification
    # Parametrized cases share one test id, listing it once keeps the slots for distinct tests.
    test_ids = list(dict.fromkeys(t.test_id for t in group.tests))
    shown = [_code(test_id) for test_id in test_ids[:_NAMES_SHOWN]]
    if len(test_ids) > _NAMES_SHOWN:
        shown.append(f"and {len(test_ids) - _NAMES_SHOWN} more")
    tests = ", ".join(shown)
    lines = [
        f"### {number}. {result.category.value}, {result.confidence.value} confidence",
        result.summary,
        f"Tests ({len(test_ids)}): {tests}",
    ]
    if result.evidence:
        lines.append("Evidence:\n\n" + _fenced(result.evidence))
    else:
        lines.append("No evidence found, so the category is unknown.")
    lines.append(f"Next step: {result.next_step}")
    if result.classified_by is ClassifiedBy.HEURISTICS:
        lines.append("Classified by heuristics, not sent to LLM.")
    return "\n\n".join(lines)


def _code(text: str) -> str:
    fence = _backtick_fence(text, minimum=1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _backtick_fence(text: str, minimum: int) -> str:
    # A quote may hold backticks, a longer fence keeps it from closing the block early.
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(minimum, longest + 1)


def _fenced(quotes: list[str]) -> str:
    text = "\n".join(quotes)
    fence = _backtick_fence(text, minimum=3)
    return f"{fence}text\n{text}\n{fence}"


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else noun + "s"
