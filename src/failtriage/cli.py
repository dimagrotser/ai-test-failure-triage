import textwrap
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer

from failtriage.models import Status
from failtriage.parsers.junit import ReportParseError, parse_junit
from failtriage.redaction import redact_result

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
def analyze(
    junit: Annotated[Path, typer.Option(help="JUnit XML report to analyze.")],
) -> None:
    """Print the failed tests of a report."""
    try:
        results = [redact_result(r) for r in parse_junit(junit)]
    except ReportParseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if not results:
        typer.echo(f"warning: no tests found in {junit}", err=True)
        return

    shown = [r for r in results if r.status is not Status.PASSED and r.status is not Status.SKIPPED]
    for result in shown:
        last_failure = next(a for a in reversed(result.attempts) if a.status is not Status.PASSED)
        count = len(result.attempts)
        noun = "attempt" if count == 1 else "attempts"
        typer.echo(f"{result.status.name} {result.test_id} ({count} {noun})")
        if last_failure.message:
            typer.echo(f"  {last_failure.message}")
        if last_failure.stack_trace:
            typer.echo(textwrap.indent(last_failure.stack_trace.strip(), "    "))
        typer.echo()
    failed = sum(r.status in (Status.FAILED, Status.ERROR) for r in results)
    on_retry = sum(r.status is Status.PASSED_ON_RETRY for r in results)
    typer.echo(f"{failed} failed, {on_retry} passed on retry, {len(results)} tests total")
