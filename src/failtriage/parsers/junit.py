import re
import xml.etree.ElementTree as ET
from pathlib import Path

from failtriage.models import Attempt, Status, TestResult
from failtriage.parsers import ReportParseError, final_status

OUTCOME_TAGS = {
    "failure": Status.FAILED,
    "error": Status.ERROR,
    "skipped": Status.SKIPPED,
}


# Surefire writes a retried test as one testcase: flaky* elements are failed attempts before
# the final pass, rerun* elements are failed attempts after the first failure.
FLAKY_TAGS = {"flakyFailure": Status.FAILED, "flakyError": Status.ERROR}
RERUN_TAGS = {"rerunFailure": Status.FAILED, "rerunError": Status.ERROR}


def parse_junit(path: Path) -> list[TestResult]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ReportParseError(f"cannot read {path}: {exc}") from exc
    attempts_by_case: dict[tuple[str, str, str], list[Attempt]] = {}
    for case in root.iter("testcase"):
        key = (case.get("file") or "", case.get("classname") or "", case.get("name") or "")
        attempts_by_case.setdefault(key, []).extend(_parse_attempts(case))
    return [
        TestResult(test_id=_test_id(*key), status=final_status(attempts), attempts=attempts)
        for key, attempts in attempts_by_case.items()
    ]


def _parse_attempts(case: ET.Element) -> list[Attempt]:
    before = [_parse_retry(el, st) for tag, st in FLAKY_TAGS.items() for el in case.findall(tag)]
    after = [_parse_retry(el, st) for tag, st in RERUN_TAGS.items() for el in case.findall(tag)]
    return [*before, _parse_attempt(case), *after]


def _parse_retry(element: ET.Element, status: Status) -> Attempt:
    return Attempt(
        status=status,
        message=element.get("message"),
        stack_trace=element.findtext("stackTrace") or element.text,
        stdout=element.findtext("system-out"),
        stderr=element.findtext("system-err"),
        duration=_parse_duration(element.get("time")),
    )


def _parse_attempt(case: ET.Element) -> Attempt:
    status = Status.PASSED
    message = None
    stack_trace = None
    for tag, tag_status in OUTCOME_TAGS.items():
        outcome = case.find(tag)
        if outcome is not None:
            status = tag_status
            message = outcome.get("message")
            stack_trace = outcome.text
            break
    return Attempt(
        status=status,
        message=message,
        stack_trace=stack_trace,
        stdout=case.findtext("system-out"),
        stderr=case.findtext("system-err"),
        duration=_parse_duration(case.get("time")),
    )


def _parse_duration(raw: str | None) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _test_id(file: str, classname: str, name: str) -> str:
    return f"{file or classname}::{re.sub(r'\[.*\]$', '', name)}"
