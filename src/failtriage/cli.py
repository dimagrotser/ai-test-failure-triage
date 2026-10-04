import json
import textwrap
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer

from failtriage.grouping import group_failures
from failtriage.models import FailureGroup, Status
from failtriage.parsers.junit import ReportParseError, parse_junit
from failtriage.redaction import redact_result
from failtriage.report.json_output import AnalysisReport, build_report

app = typer.Typer(no_args_is_help=True)


# Without a callback typer collapses a single-command app and drops the command list.
@app.callback()
def main() -> None:
    """Group failed tests by root cause and classify them."""


@app.command()
def version() -> None:
    """Print the installed failtriage version."""
    typer.echo(package_version("failtriage"))


@app.command()
def schema() -> None:
    """Print the JSON schema of `analyze --json` output."""
    typer.echo(json.dumps(AnalysisReport.model_json_schema(), indent=2))


@app.command()
def analyze(
    junit: Annotated[Path, typer.Option(help="JUnit XML report to analyze.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Print the analysis as JSON instead of text.")
    ] = False,
) -> None:
    """Group the failed tests of a report by cause and print the groups."""
    try:
        _analyze(junit, json_output)
    except ReportParseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        # Only the type: a message or traceback may quote text that is not redacted.
        typer.echo(f"internal error: {type(exc).__name__}", err=True)
        raise typer.Exit(code=1) from None


def _analyze(junit: Path, json_output: bool) -> None:
    results = [redact_result(r) for r in parse_junit(junit)]
    if not results:
        typer.echo(f"warning: no tests found in {junit}", err=True)
        if not json_output:
            return

    groups = group_failures(results)
    if json_output:
        report = build_report(package_version("failtriage"), [str(junit)], results, groups)
        typer.echo(report.model_dump_json(indent=2))
        return
    for number, group in enumerate(groups, start=1):
        _print_group(number, group)
    failed = sum(r.status in (Status.FAILED, Status.ERROR) for r in results)
    on_retry = sum(r.status is Status.PASSED_ON_RETRY for r in results)
    noun = "group" if len(groups) == 1 else "groups"
    typer.echo(
        f"{failed} failed, {on_retry} passed on retry, {len(results)} tests total, "
        f"{len(groups)} {noun}"
    )


def _print_group(number: int, group: FailureGroup) -> None:
    count = len(group.results)
    sig = group.signature
    typer.echo(f"Group {number}: {count} {'test' if count == 1 else 'tests'}")
    typer.echo(
        f"  Signature: {sig.exception_type or '-'} | {sig.message or '-'} | {sig.frame or '-'}"
    )
    for result in group.results:
        attempts = len(result.attempts)
        typer.echo(
            f"  {result.status.name} {result.test_id} "
            f"({attempts} {'attempt' if attempts == 1 else 'attempts'})"
        )
    sample = next(a for a in reversed(group.results[0].attempts) if a.status is not Status.PASSED)
    if sample.message:
        typer.echo(f"  {sample.message}")
    if sample.stack_trace:
        typer.echo(textwrap.indent(sample.stack_trace.strip(), "    "))
    typer.echo()
