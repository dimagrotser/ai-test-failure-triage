from failtriage.grouping.extract import exception_type, first_message_line, top_frame
from failtriage.grouping.normalize import normalize
from failtriage.models import Attempt, FailureGroup, Signature, Status, TestResult
from failtriage.redaction import redact_result

_FAILED = (Status.FAILED, Status.ERROR)


def signature(result: TestResult) -> Signature:
    """Signature of the last failed attempt, always computed on redacted text."""
    attempt = _last_failed_attempt(redact_result(result))
    return Signature(
        exception_type=exception_type(attempt),
        message=normalize(first_message_line(attempt)),
        frame=top_frame(attempt),
    )


def group_failures(results: list[TestResult]) -> list[FailureGroup]:
    """Group failed tests by exact signature, the largest group first."""
    members: dict[Signature, list[TestResult]] = {}
    for result in results:
        if any(a.status in _FAILED for a in result.attempts):
            members.setdefault(signature(result), []).append(result)
    groups = [FailureGroup(signature=sig, results=found) for sig, found in members.items()]
    return sorted(groups, key=lambda g: -len(g.results))


def _last_failed_attempt(result: TestResult) -> Attempt:
    return next(a for a in reversed(result.attempts) if a.status in _FAILED)
