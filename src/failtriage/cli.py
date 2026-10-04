import json
import os
import re
import textwrap
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import anthropic
import typer
from pydantic import ValidationError

from failtriage.classify.anthropic_provider import AnthropicProvider
from failtriage.classify.llm import GroupClassifications, answer_schema, classify_groups
from failtriage.classify.payload import Limits
from failtriage.classify.pricing import cost_usd
from failtriage.evaluate import EvalError, evaluate, render_eval
from failtriage.github.client import GitHubClient, GitHubError
from failtriage.github.history_artifacts import fetch_history
from failtriage.github.pr_files import list_pr_files, to_unified_diff
from failtriage.grouping import group_failures
from failtriage.history import HistoryEntry, HistoryError, history_schema, load_history
from failtriage.models import FailureGroup, Status
from failtriage.parsers.junit import ReportParseError, parse_junit
from failtriage.prompts import load_prompt
from failtriage.redaction import redact_result
from failtriage.report.json_output import AnalysisReport, Cost, DiffInfo, build_report
from failtriage.report.markdown import render_markdown

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_HISTORY_RUNS = 10

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
def schema(
    history: Annotated[
        bool, typer.Option("--history", help="Print the schema of the history file instead.")
    ] = False,
) -> None:
    """Print the JSON schema of `analyze --json` output."""
    typer.echo(
        json.dumps(history_schema() if history else AnalysisReport.model_json_schema(), indent=2)
    )


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
    repo: Annotated[
        str | None,
        typer.Option(help="GitHub repository as owner/name, to read the pull request diff."),
    ] = None,
    pr: Annotated[
        int | None, typer.Option(help="Pull request number. Needs --repo and GITHUB_TOKEN.")
    ] = None,
    history: Annotated[
        Path | None,
        typer.Option(help="JSON file with test statuses from earlier runs on main."),
    ] = None,
    history_runs: Annotated[
        int,
        typer.Option(
            min=1,
            help="Runs on main to read history from, with --repo and --pr and no --history.",
        ),
    ] = DEFAULT_HISTORY_RUNS,
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
    if (repo is None) != (pr is None):
        typer.echo("--repo and --pr go together", err=True)
        raise typer.Exit(code=2)
    if repo is not None and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        typer.echo("--repo must look like owner/name", err=True)
        raise typer.Exit(code=2)
    if repo is not None and not (json_output or markdown):
        typer.echo("--repo needs --json or --markdown, text output has no diff", err=True)
        raise typer.Exit(code=2)
    if repo is not None and not os.environ.get("GITHUB_TOKEN"):
        typer.echo("--repo and --pr read from GitHub and need GITHUB_TOKEN", err=True)
        raise typer.Exit(code=2)
    if history is not None and not (json_output or markdown):
        typer.echo("--history needs --json or --markdown, text output has no signals", err=True)
        raise typer.Exit(code=2)
    pull_request = (repo, pr) if repo is not None and pr is not None else None
    try:
        entries: list[HistoryEntry] | None = None
        if history is not None:
            entries = load_history(history)
        elif pull_request is not None and (json_output or markdown):
            entries = _fetch_history(pull_request[0], history_runs)
        _analyze(junit, json_output, markdown, model, record, pull_request, entries)
    except (ReportParseError, HistoryError) as exc:
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


def _github_client(token: str) -> GitHubClient:
    return GitHubClient(token)


def _fetch_history(repo: str, runs: int) -> list[HistoryEntry] | None:
    """History from the artifacts of recent runs on main. Without it the run goes on."""
    client = _github_client(os.environ["GITHUB_TOKEN"])
    try:
        fetched = fetch_history(client, repo, runs)
    except (GitHubError, ValidationError) as exc:
        reason = str(exc) if isinstance(exc, GitHubError) else type(exc).__name__
        typer.echo(f"github: {reason}, continuing without history", err=True)
        return None
    for artifact_id in fetched.skipped:
        typer.echo(f"github: history artifact {artifact_id} skipped", err=True)
    return fetched.entries


def _read_diff(repo: str, pr: int) -> tuple[DiffInfo, str | None]:
    """The pull request files as a report entry and one diff. Without GitHub the run goes on."""
    client = _github_client(os.environ["GITHUB_TOKEN"])
    try:
        files = list_pr_files(client, repo, pr)
    except (GitHubError, ValidationError) as exc:
        # Only the type or our own message: GitHub's answer may echo what we sent.
        reason = str(exc) if isinstance(exc, GitHubError) else type(exc).__name__
        typer.echo(f"github: {reason}, continuing without the diff", err=True)
        return DiffInfo(available=False), None
    info = DiffInfo(
        changed_files=[f.filename for f in files],
        files_without_hunks=[f.filename for f in files if not f.patch],
    )
    return info, to_unified_diff(files)


def _analyze(
    junit: Path,
    json_output: bool,
    markdown: bool,
    model: str,
    record: Path | None,
    pull_request: tuple[str, int] | None,
    history: list[HistoryEntry] | None = None,
) -> None:
    results = [redact_result(r) for r in parse_junit(junit)]
    if not results:
        typer.echo(f"warning: no tests found in {junit}", err=True)
        if not (json_output or markdown):
            return

    groups = group_failures(results)
    if json_output or markdown:
        diff_info, diff = _read_diff(*pull_request) if pull_request else (None, None)
        changed = diff_info.changed_files if diff_info else []
        classified, cost = _classify(groups, model, record, diff, changed, history or [])
        report = build_report(
            package_version("failtriage"),
            [str(junit)],
            results,
            groups,
            classified.classifications if classified else None,
            cost,
            diff_info,
            history,
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
    groups: list[FailureGroup],
    model: str,
    record: Path | None,
    diff: str | None,
    changed_files: list[str],
    history: list[HistoryEntry],
) -> tuple[GroupClassifications | None, Cost]:
    """Classify with the LLM when there is a key, else leave it to the heuristics."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        typer.echo("ANTHROPIC_API_KEY not set, classifying with heuristics only", err=True)
        return None, Cost()
    provider = AnthropicProvider(_client(), model, answer_schema(), record)
    classified = classify_groups(
        groups, provider, load_prompt(), Limits(), diff, changed_files, history
    )
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
