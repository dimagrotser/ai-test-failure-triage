import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from failtriage.models import Attempt, Status, TestResult
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


class _Label(BaseModel):
    name: str
    value: str


class _Result(BaseModel):
    name: str
    fullName: str = ""
    status: str
    statusDetails: _StatusDetails | None = None
    start: int | None = None
    stop: int | None = None
    labels: list[_Label] = []


def parse_allure(directory: Path) -> list[TestResult]:
    results = [_read(path) for path in sorted(directory.glob("*-result.json"))]
    return [_to_test_result(r) for r in results]


def _read(path: Path) -> _Result:
    try:
        return _Result.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ReportParseError(f"cannot read {path}: {type(exc).__name__}") from exc


def _to_test_result(result: _Result) -> TestResult:
    attempts = [_to_attempt(result)]
    return TestResult(
        test_id=f"{_group(result)}::{re.sub(r'\[.*\]$', '', result.name)}",
        status=final_status(attempts),
        attempts=attempts,
    )


def _group(result: _Result) -> str:
    labels = {label.name: label.value for label in result.labels}
    return labels.get("testClass") or labels.get("package") or result.fullName.rpartition(".")[0]


def _to_attempt(result: _Result) -> Attempt:
    details = result.statusDetails
    duration = None
    if result.start is not None and result.stop is not None:
        duration = (result.stop - result.start) / 1000
    return Attempt(
        status=STATUS.get(result.status, Status.ERROR),
        message=details.message if details else None,
        stack_trace=details.trace if details else None,
        duration=duration,
    )
