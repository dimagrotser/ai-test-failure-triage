from pydantic import BaseModel, ConfigDict

from failtriage.classify.heuristics import heuristic_verdict, signals
from failtriage.models import Category, FailureGroup, Signal, Signature, Status
from failtriage.redaction import redact, redact_result

GROUP_CAP_NOTE = "not sent to LLM: group cap"


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True)

    stack_head: int = 15
    stack_tail: int = 25
    message_chars: int = 1000
    output_tail: int = 30
    diff_lines: int = 150
    max_groups: int = 10


class GroupPayload(BaseModel):
    signature: Signature
    tests: list[str]
    message: str | None
    stack_trace: str | None
    stdout: str | None
    stderr: str | None
    signals: list[Signal]
    heuristic_verdict: Category | None
    diff: str | None


class SkippedGroup(BaseModel):
    group: FailureGroup
    note: str


class PayloadPlan(BaseModel):
    sent: list[GroupPayload]
    skipped: list[SkippedGroup]


def build_payloads(
    groups: list[FailureGroup], limits: Limits, diff: str | None = None
) -> PayloadPlan:
    """Shape what the LLM gets: redact first, then truncate, largest groups first up to the cap."""
    ranked = sorted(groups, key=lambda g: -len(g.results))
    redacted_diff = redact(diff) if diff else None
    return PayloadPlan(
        sent=[_payload(g, limits, redacted_diff) for g in ranked[: limits.max_groups]],
        skipped=[SkippedGroup(group=g, note=GROUP_CAP_NOTE) for g in ranked[limits.max_groups :]],
    )


def _payload(group: FailureGroup, limits: Limits, diff: str | None) -> GroupPayload:
    redacted = redact_result(group.results[0])
    attempt = next(
        a for a in reversed(redacted.attempts) if a.status in (Status.FAILED, Status.ERROR)
    )
    found = signals(group)

    def tail(text: str | None) -> str | None:
        return None if text is None else truncate_lines(text, head=0, tail=limits.output_tail)

    stack_trace = attempt.stack_trace
    return GroupPayload(
        signature=group.signature,
        tests=[r.test_id for r in group.results],
        message=None
        if attempt.message is None
        else truncate_message(attempt.message, limits.message_chars),
        stack_trace=None
        if stack_trace is None
        else truncate_lines(stack_trace, limits.stack_head, limits.stack_tail),
        stdout=tail(attempt.stdout),
        stderr=tail(attempt.stderr),
        signals=found,
        heuristic_verdict=heuristic_verdict(found),
        diff=(select_hunks(diff, stack_trace or "", limits.diff_lines) or None) if diff else None,
    )


def truncate_lines(text: str, head: int, tail: int) -> str:
    """Keep the first `head` and last `tail` lines, marking how many were cut."""
    lines = text.splitlines()
    omitted = len(lines) - head - tail
    if omitted <= 0:
        return text
    marker = f"... {omitted} lines omitted ..."
    return "\n".join([*lines[:head], marker, *lines[len(lines) - tail :]])


def truncate_message(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... {len(text) - limit} characters omitted"


def select_hunks(diff: str, trace: str, limit: int) -> str:
    """Keep the diff of files whose path appears in the trace, at most `limit` lines."""
    relevant = [f for f in _split_files(diff) if _path(f) in trace]
    kept = [line for file_diff in relevant for line in file_diff]
    return truncate_lines("\n".join(kept), head=limit, tail=0)


def _split_files(diff: str) -> list[list[str]]:
    files: list[list[str]] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files.append([])
        if files:
            files[-1].append(line)
    return files


def _path(file_diff: list[str]) -> str:
    # `diff --git a/<path> b/<path>`: the b side is the file as it is after the change.
    return file_diff[0].rsplit(" b/", 1)[-1]
