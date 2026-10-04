from failtriage.models import FailureGroup, Signal, SignalName, Status, TestResult


def signals(group: FailureGroup) -> list[Signal]:
    """Signals of a group, each with the first quote that proves it."""
    found = [_passed_on_retry(r) for r in group.results]
    return [s for s in found if s]


def _passed_on_retry(result: TestResult) -> Signal | None:
    if result.status is not Status.PASSED_ON_RETRY:
        return None
    attempt = next(n for n, a in enumerate(result.attempts, start=1) if a.status is Status.PASSED)
    quote = f"{result.test_id}: failed, then passed on attempt {attempt}"
    return Signal(name=SignalName.PASSED_ON_RETRY, quote=quote)
