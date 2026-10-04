import json
import os
import textwrap
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import anthropic
import typer

from failtriage.classify.anthropic_provider import AnthropicProvider
from failtriage.classify.llm import GroupClassifications, answer_schema, classify_groups
from failtriage.classify.payload import Limits
from failtriage.classify.pricing import cost_usd
from failtriage.evaluate import EvalError, evaluate, render_eval
from failtriage.grouping import group_failures
from failtriage.models import FailureGroup, Status
from failtriage.parsers.junit import ReportParseError, parse_junit
from failtriage.prompts import load_prompt
from failtriage.redaction import redact_result
from failtriage.report.json_output import AnalysisReport, Cost, build_report
from failtriage.report.markdown import render_markdown

DEFAULT_MODEL = "claude-sonnet-5-5"

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
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print the analysis as a Markdown report.")
    ] = False,
    model: Annotated[str, typer.Option(help="Anthropic model that classifies the groups.")] = (
        DEFAULT_MODEL
    ),
    record: Annotated[
        Path | None,
        typer.Option(help="Save every LLM answer here so tests can replay it. Needs the API key."),
    ] = None,
) -> None:
    """Group the failed tests of a report by cause and print the groups."""
    if json_output and markdown:
        typer.echo("use either --json or --markdown, not both", err=True)
        raise typer.Exit(code=2)
    if record is not None and not (json_output or markdown):
        typer.echo("--record needs --json or --markdown, text output never calls the LLM", err=True)
        raise typer.Exit(code=2)
    if record is not None and not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("--record makes real LLM calls and needs ANTHROPIC_API_KEY", err=True)
        raise typer.Exit(code=2)
    try:
        _analyze(junit, json_output, markdown, model, record)
    except ReportParseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        # Only the type: a message or traceback may quote text that is not redacted.
        typer.echo(f"internal error: {type(exc).__name__}", err=True)
        raise typer.Exit(code=1) from None


@app.command(name="eval")
def eval_(
    evals_dir: Annotated[Path, typer.Argument(help="Directory with a cases/ folder of Lab cases.")],
) -> None:
    """Score the heuristics-only classifier against the labeled Lab cases."""
    try:
        typer.echo(render_eval(evaluate(evals_dir)), nl=False)
    except EvalError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        typer.echo(f"internal error: {type(exc).__name__}", err=True)
        raise typer.Exit(code=1) from None


def _client() -> anthropic.Anthropic:
    # The SDK reads the key from the environment, so it never passes through our code.
    return anthropic.Anthropic()


def _analyze(
    junit: Path, json_output: bool, markdown: bool, model: str, record: Path | None
) -> None:
    results = [redact_result(r) for r in parse_junit(junit)]
    if not results:
        typer.echo(f"warning: no tests found in {junit}", err=True)
        if not (json_output or markdown):
            return

    groups = group_failures(results)
    if json_output or markdown:
        classified, cost = _classify(groups, model, record)
        report = build_report(
            package_version("failtriage"),
            [str(junit)],
            results,
            groups,
            classified.classifications if classified else None,
            cost,
        )
        if json_output:
            typer.echo(report.model_dump_json(indent=2))
        else:
            typer.echo(render_markdown(report), nl=False)
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


def _classify(
    groups: list[FailureGroup], model: str, record: Path | None
) -> tuple[GroupClassifications | None, Cost]:
    """Classify with the LLM when there is a key, else leave it to the heuristics."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("ANTHROPIC_API_KEY not set, classifying with heuristics only", err=True)
        return None, Cost()
    provider = AnthropicProvider(_client(), model, answer_schema(), record)
    classified = classify_groups(groups, provider, load_prompt(), Limits())
    usage = provider.usage
    usd = cost_usd(model, usage)
    calls = f"{usage.calls} {'call' if usage.calls == 1 else 'calls'}"
    price = f"${usd:.4f}" if usd is not None else "cost unknown, no price for this model"
    typer.echo(
        f"llm: {model}, {calls}, {usage.input_tokens} input tokens, "
        f"{usage.output_tokens} output tokens, {price}",
        err=True,
    )
    for index, error in sorted(classified.failed.items()):
        typer.echo(f"group {index + 1}: {error}, classified with heuristics", err=True)
    cost = Cost(
        model=model,
        llm_calls=usage.calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        usd=usd,
    )
    return classified, cost


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
