from typing import Literal

from pydantic import BaseModel

from failtriage.classify.heuristics import classify_with_heuristics, signals
from failtriage.models import (
    Classification,
    FailureGroup,
    Signal,
    Signature,
    Status,
    TestResult,
)


class RunInfo(BaseModel):
    tool_version: str
    inputs: list[str]
    tests: int
    failed: int
    passed_on_retry: int


class GroupTest(BaseModel):
    test_id: str
    status: Status
    attempts: int


class GroupReport(BaseModel):
    signature: Signature
    tests: list[GroupTest]
    signals: list[Signal]
    classification: Classification


class Cost(BaseModel):
    # None when no LLM was used.
    model: str | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # None when the model is not in the price table, which is not the same as free.
    usd: float | None = 0.0


class AnalysisReport(BaseModel):
    schema_version: Literal[1] = 1
    run: RunInfo
    groups: list[GroupReport]
    cost: Cost
    # Always null until history is read from artifacts, which a report must say instead of
    # implying a clean record (ADR 0002).
    history: None = None


def build_report(
    tool_version: str,
    inputs: list[str],
    results: list[TestResult],
    groups: list[FailureGroup],
    classifications: list[Classification] | None = None,
    cost: Cost | None = None,
) -> AnalysisReport:
    """`classifications` line up with `groups`; without them the heuristics classify."""
    return AnalysisReport(
        run=RunInfo(
            tool_version=tool_version,
            inputs=inputs,
            tests=len(results),
            failed=sum(r.status in (Status.FAILED, Status.ERROR) for r in results),
            passed_on_retry=sum(r.status is Status.PASSED_ON_RETRY for r in results),
        ),
        groups=[
            _group_report(g, classifications[i] if classifications else None)
            for i, g in enumerate(groups)
        ],
        cost=cost or Cost(),
    )


def _group_report(group: FailureGroup, classification: Classification | None) -> GroupReport:
    return GroupReport(
        signature=group.signature,
        tests=[
            GroupTest(test_id=r.test_id, status=r.status, attempts=len(r.attempts))
            for r in group.results
        ],
        signals=signals(group),
        classification=classification or classify_with_heuristics(group),
    )
