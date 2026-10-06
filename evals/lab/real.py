import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Annotated

import typer

from evals.lab.build import REAL, LabError, check_redacted, redact_tree
from failtriage.grouping import group_failures
from failtriage.grouping.extract import first_message_line
from failtriage.parsers import ReportParseError
from failtriage.parsers.junit import parse_junit

LABEL_SKELETON = """\
# Fill this in by hand. category is one of product_bug, test_bug, flaky, environment, unknown.
category: ''
source: real
scenario: ''
notes: ''
"""


def import_case(report: Path, case_id: str, real_dir: Path = REAL) -> Path:
    """Write a redacted copy of a JUnit report as a real case with an empty label.

    The raw report is only parsed in memory. What lands on disk is redacted and checked first,
    so a failed check leaves nothing behind.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", case_id):
        raise LabError("the case id may hold lowercase letters, digits and dashes")
    case = real_dir / case_id
    if case.exists():
        raise LabError(f"real case {case_id} already exists")
    try:
        tree = ET.parse(report)
    except (OSError, ET.ParseError) as exc:
        raise ReportParseError(f"cannot read {report}: {type(exc).__name__}") from exc
    redact_tree(tree.getroot())
    real_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".import-", dir=real_dir))
    try:
        tree.write(staging / "junit.xml", encoding="utf-8", xml_declaration=True)
        results = parse_junit(staging / "junit.xml")
        if not group_failures(results):
            raise LabError(f"{report} has no failures to import")
        check_redacted(staging / "junit.xml")
        (staging / "label.yaml").write_text(LABEL_SKELETON)
        staging.rename(case)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return case


def summarize(case: Path) -> str:
    results = parse_junit(case / "junit.xml")
    groups = group_failures(results)
    lines = [
        f"wrote {case}/junit.xml and label.yaml",
        f"{len(results)} tests, {len(groups)} groups",
    ]
    for number, group in enumerate(groups, start=1):
        attempt = group.results[0].attempts[-1]
        lines.append(f"  {number}. {len(group.results)} tests: {first_message_line(attempt)}")
    lines.append("fill label.yaml by hand, then run make lab")
    return "\n".join(lines)


def main(
    report: Annotated[Path, typer.Argument(help="JUnit XML report of a failing run.")],
    case_id: Annotated[str, typer.Argument(help="Name of the new case, such as real-login.")],
    real_dir: Annotated[Path, typer.Option(help="Where real cases are kept.")] = REAL,
) -> None:
    """Import a JUnit report as a redacted Lab case with source real."""
    try:
        case = import_case(report, case_id, real_dir)
        typer.echo(summarize(case))
    except (LabError, ReportParseError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        # Only the type: a message or traceback may quote text that is not redacted.
        typer.echo(f"internal error: {type(exc).__name__}", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    typer.run(main)
