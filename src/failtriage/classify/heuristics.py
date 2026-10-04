import re
from collections.abc import Iterator

from failtriage.models import Attempt, FailureGroup, Signal, SignalName, Status, TestResult
from failtriage.redaction import redact_result

_QUOTE_LIMIT = 200
_PYTEST_EXCEPTION_LINE = re.compile(r"^E\s+(?P<text>\S.*)$")

# Matched only against the message, the pytest `E` lines and the first trace line. The rest of
# a trace is library code, where a comment like `# timeout error` would give a false signal.
_TEXT_PATTERNS: dict[SignalName, re.Pattern[str]] = {
    SignalName.NETWORK_ERROR: re.compile(
        r"connection (?:refused|reset)|ECONNREFUSED|ECONNRESET|ENOTFOUND|nodename nor servname"
        r"|name or service not known|getaddrinfo|name resolution|network is unreachable"
        r"|Connect(?:ion)?(?:Error|Exception)",
        re.IGNORECASE,
    ),
    SignalName.TIMEOUT: re.compile(
        r"TimeoutError|timed out|Timeout \S+ exceeded|ReadTimeout|SocketTimeoutException|ETIMEDOUT",
        re.IGNORECASE,
    ),
    SignalName.MISSING_ENV_OR_PERMISSION: re.compile(
        r"PermissionError|permission denied|EACCES|read-only file system"
        # An upper snake case key is an environment variable, `KeyError: 'EUR'` is data.
        r"|KeyError: '[A-Z][A-Z0-9]*_[A-Z0-9_]+'"
        r"|environment variable \S+ (?:is )?(?:not set|missing)",
        re.IGNORECASE,
    ),
}


def signals(group: FailureGroup) -> list[Signal]:
    """Signals of a group, each with the first quote that proves it."""
    results = [redact_result(r) for r in group.results]
    found = [_passed_on_retry(results)]
    lines = [line for r in results for line in _failure_lines(r)]
    for name, pattern in _TEXT_PATTERNS.items():
        found.append(_from_lines(name, pattern, lines))
    return [s for s in found if s]


def _passed_on_retry(results: list[TestResult]) -> Signal | None:
    for result in results:
        if result.status is Status.PASSED_ON_RETRY:
            attempt = next(n for n, a in enumerate(result.attempts, 1) if a.status is Status.PASSED)
            quote = f"{result.test_id}: failed, then passed on attempt {attempt}"
            return Signal(name=SignalName.PASSED_ON_RETRY, quote=quote)
    return None


def _from_lines(name: SignalName, pattern: re.Pattern[str], lines: list[str]) -> Signal | None:
    quote = next((line for line in lines if pattern.search(line)), None)
    return Signal(name=name, quote=quote[:_QUOTE_LIMIT]) if quote else None


def _failure_lines(result: TestResult) -> Iterator[str]:
    for attempt in result.attempts:
        if attempt.status in (Status.FAILED, Status.ERROR):
            yield from _attempt_lines(attempt)


def _attempt_lines(attempt: Attempt) -> Iterator[str]:
    yield from (line.strip() for line in (attempt.message or "").splitlines() if line.strip())
    trace = (attempt.stack_trace or "").strip().splitlines()
    for line in trace:
        if match := _PYTEST_EXCEPTION_LINE.match(line):
            yield match["text"].strip()
    if trace:
        yield trace[0].strip()
