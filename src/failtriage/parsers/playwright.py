import base64
import binascii
import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from failtriage.models import Attempt, Status, TestResult
from failtriage.parsers import ReportParseError, final_status

ATTEMPT_STATUS = {
    "passed": Status.PASSED,
    "failed": Status.FAILED,
    "timedOut": Status.FAILED,
    "interrupted": Status.ERROR,
    "skipped": Status.SKIPPED,
}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
PROJECT_SEPARATOR = " › "


class _Error(BaseModel):
    message: str | None = None
    stack: str | None = None


class _Output(BaseModel):
    text: str | None = None
    buffer: str | None = None


class _Attachment(BaseModel):
    path: str | None = None


class _Result(BaseModel):
    status: str
    duration: float | None = None
    error: _Error | None = None
    stdout: list[_Output] = []
    stderr: list[_Output] = []
    attachments: list[_Attachment] = []


class _Test(BaseModel):
    projectName: str = ""
    expectedStatus: str = "passed"
    results: list[_Result] = []


class _Spec(BaseModel):
    title: str
    file: str = ""
    tests: list[_Test] = []


class _Suite(BaseModel):
    title: str = ""
    specs: list[_Spec] = []
    suites: list["_Suite"] = []


class _Report(BaseModel):
    suites: list[_Suite] = []


def parse_playwright(path: Path) -> list[TestResult]:
    try:
        report = _Report.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ReportParseError(f"cannot read {path}: {type(exc).__name__}") from exc
    results: list[TestResult] = []
    for file_suite in report.suites:
        # The top suite is the spec file, its title repeats the file name.
        results.extend(_walk(file_suite, []))
    return results


def _walk(suite: _Suite, describes: list[str]) -> list[TestResult]:
    results = [
        _parse_test(spec, test, describes)
        for spec in suite.specs
        for test in spec.tests
        if test.results
    ]
    for child in suite.suites:
        results.extend(_walk(child, [*describes, child.title]))
    return results


def _parse_test(spec: _Spec, test: _Test, describes: list[str]) -> TestResult:
    parts = [test.projectName, *describes, spec.title]
    name = PROJECT_SEPARATOR.join(p for p in parts if p)
    attempts = [_parse_attempt(r, test.expectedStatus) for r in test.results]
    return TestResult(
        test_id=f"{spec.file}::{name}", status=final_status(attempts), attempts=attempts
    )


def _parse_attempt(result: _Result, expected: str) -> Attempt:
    status = ATTEMPT_STATUS.get(result.status, Status.ERROR)
    # test.fail() marks a test that is expected to fail, so the failure is the pass.
    if expected == "failed" and status is Status.FAILED:
        status = Status.PASSED
    error = result.error
    message = _strip_ansi(error.message).split("\n", 1)[0] if error and error.message else None
    return Attempt(
        status=status,
        message=message,
        stack_trace=_strip_ansi(error.stack) if error and error.stack else None,
        stdout=_join_output(result.stdout),
        stderr=_join_output(result.stderr),
        duration=result.duration / 1000 if result.duration is not None else None,
        attachments=[a.path for a in result.attachments if a.path],
    )


def _strip_ansi(text: str) -> str:
    return ANSI.sub("", text)


def _join_output(chunks: list[_Output]) -> str | None:
    parts = [_decode(c) for c in chunks]
    return "".join(parts) or None


def _decode(chunk: _Output) -> str:
    if chunk.text is not None:
        return chunk.text
    try:
        return base64.b64decode(chunk.buffer or "").decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""
