from failtriage.models import Attempt, Status


class ReportParseError(Exception):
    """The report file is missing, unreadable or not in the expected format."""


def final_status(attempts: list[Attempt]) -> Status:
    final = attempts[-1].status
    earlier_failed = any(a.status in (Status.FAILED, Status.ERROR) for a in attempts[:-1])
    if final is Status.PASSED and earlier_failed:
        return Status.PASSED_ON_RETRY
    return final
