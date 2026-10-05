import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from failtriage.models import Attempt, Status, Step, TestResult
from failtriage.parsers import ReportParseError, final_status

STATUS = {
    "passed": Status.PASSED,
    "failed": Status.FAILED,
    "broken": Status.ERROR,
    "skipped": Status.SKIPPED,
}


class _StatusDetails(BaseModel):
    message: str | None = None
    trace: str | None = None


class _Attachment(BaseModel):
    source: str


class _Step(BaseModel):
    name: str = ""
    status: str = "unknown"
    statusDetails: _StatusDetails | None = None
    steps: list["_Step"] = []
    attachments: list[_Attachment] = []


class _Label(BaseModel):
    name: str
    value: str


class _Result(BaseModel):
    name: str
    historyId: str = ""
    fullName: str = ""
    status: str
    statusDetails: _StatusDetails | None = None
    start: int | None = None
    stop: int | None = None
    labels: list[_Label] = []
    steps: list[_Step] = []
    attachments: list[_Attachment] = []


def parse_allure(directory: Path) -> list[TestResult]:
    results = [_read(path) for path in sorted(directory.glob("*-result.json"))]
    # Allure writes one file per attempt, the history id ties the attempts of a test together.
    by_test: dict[str, list[_Result]] = {}
    for result in results:
        by_test.setdefault(result.historyId or result.fullName or result.name, []).append(result)
    return [
        _to_test_result(directory, sorted(attempts, key=_start)) for attempts in by_test.values()
    ]


def _start(result: _Result) -> int:
    return result.start or 0


def _read(path: Path) -> _Result:
    try:
        return _Result.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ReportParseError(f"cannot read {path}: {type(exc).__name__}") from exc


def _to_test_result(directory: Path, results: list[_Result]) -> TestResult:
    result = results[-1]
    attempts = [_to_attempt(directory, r) for r in results]
    return TestResult(
        test_id=f"{_group(result)}::{re.sub(r'\[.*\]$', '', result.name)}",
        status=final_status(attempts),
        attempts=attempts,
    )


def _group(result: _Result) -> str:
    labels = {label.name: label.value for label in result.labels}
    return labels.get("testClass") or labels.get("package") or result.fullName.rpartition(".")[0]


def _to_attempt(directory: Path, result: _Result) -> Attempt:
    details = result.statusDetails
    duration = None
    if result.start is not None and result.stop is not None:
        duration = (result.stop - result.start) / 1000
    return Attempt(
        status=STATUS.get(result.status, Status.ERROR),
        message=details.message if details else None,
        stack_trace=details.trace if details else None,
        duration=duration,
        attachments=[
            str(directory / source)
            for source in [*_sources(result.attachments), *_step_sources(result.steps)]
        ],
        steps=[_to_step(step) for step in result.steps],
    )


def _sources(attachments: list[_Attachment]) -> list[str]:
    return [a.source for a in attachments]


def _step_sources(steps: list[_Step]) -> list[str]:
    return [s for step in steps for s in [*_sources(step.attachments), *_step_sources(step.steps)]]


def _to_step(step: _Step) -> Step:
    return Step(
        name=step.name,
        status=STATUS.get(step.status, Status.ERROR),
        message=step.statusDetails.message if step.statusDetails else None,
        steps=[_to_step(child) for child in step.steps],
    )
