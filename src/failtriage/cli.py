import textwrap
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer

from failtriage.models import Status
from failtriage.parsers.junit import ReportParseError, parse_junit

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
        results = parse_junit(junit)
    except ReportParseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if not results:
        typer.echo(f"warning: no tests found in {junit}", err=True)
        return

    failed = [r for r in results if r.status in (Status.FAILED, Status.ERROR)]
    for result in failed:
        attempt = result.attempts[-1]
        typer.echo(f"{result.status.name} {result.test_id}")
        if attempt.message:
            typer.echo(f"  {attempt.message}")
        if attempt.stack_trace:
            typer.echo(textwrap.indent(attempt.stack_trace.strip(), "    "))
        typer.echo()
    typer.echo(f"{len(failed)} failed, {len(results)} tests total")
