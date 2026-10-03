import re
import xml.etree.ElementTree as ET
from pathlib import Path

from failtriage.models import Attempt, Status, TestResult

OUTCOME_TAGS = {
    "failure": Status.FAILED,
    "error": Status.ERROR,
    "skipped": Status.SKIPPED,
}


class ReportParseError(Exception):
    """The report file is missing, unreadable or not valid XML."""


def parse_junit(path: Path) -> list[TestResult]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ReportParseError(f"cannot read {path}: {exc}") from exc
    attempts_by_case: dict[tuple[str, str, str], list[Attempt]] = {}
    for case in root.iter("testcase"):
        key = (case.get("file") or "", case.get("classname") or "", case.get("name") or "")
        attempts_by_case.setdefault(key, []).append(_parse_attempt(case))
    return [
        TestResult(test_id=_test_id(*key), status=_final_status(attempts), attempts=attempts)
        for key, attempts in attempts_by_case.items()
    ]


def _final_status(attempts: list[Attempt]) -> Status:
    final = attempts[-1].status
    earlier_failed = any(a.status in (Status.FAILED, Status.ERROR) for a in attempts[:-1])
    if final is Status.PASSED and earlier_failed:
        return Status.PASSED_ON_RETRY
    return final


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
