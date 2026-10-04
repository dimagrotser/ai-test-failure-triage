import re
from collections.abc import Iterator

from failtriage.models import (
    Attempt,
    Category,
    Classification,
    ClassifiedBy,
    Confidence,
    FailureGroup,
    Signal,
    SignalName,
    Status,
    TestResult,
)
from failtriage.redaction import redact_result

_QUOTE_LIMIT = 200
_PYTEST_EXCEPTION_LINE = re.compile(r"^E\s+(?P<text>\S.*)$")
_ASSERTION_TYPES = {"AssertionError", "AssertionFailedError", "ComparisonFailure"}
_TEST_DIRS = {"tests", "test", "__tests__"}
_TEST_FILE = re.compile(r"^(?:test_.*|.*_test\..*|.*\.(?:test|spec)\..*)$")
_JAVA_TEST_CLASS = re.compile(r"(?:Test|Tests)$")

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
        r"|(?-i:KeyError: '[A-Z][A-Z0-9]*_[A-Z0-9_]+')"
        r"|environment variable \S+ (?:is )?(?:not set|missing)",
        re.IGNORECASE,
    ),
}

# Every verdict rule lives here. The first rule whose signal is present, and whose excluded signal
# is absent, decides. A frame in code only says where it failed, so an assertion mismatch there
# stays undecided: the test or the product could be wrong.
_RULES: list[tuple[SignalName, SignalName | None, Category]] = [
    (SignalName.PASSED_ON_RETRY, None, Category.FLAKY),
    (SignalName.NETWORK_ERROR, None, Category.ENVIRONMENT),
    (SignalName.TIMEOUT, None, Category.ENVIRONMENT),
    (SignalName.MISSING_ENV_OR_PERMISSION, None, Category.ENVIRONMENT),
    (SignalName.FRAME_IN_TEST_CODE, SignalName.ASSERTION_MISMATCH, Category.TEST_BUG),
    (SignalName.FRAME_IN_SOURCE_CODE, SignalName.ASSERTION_MISMATCH, Category.PRODUCT_BUG),
]


# Passed on retry is direct evidence of nondeterminism (ADR 0001), but only for the tests that
# did it, see classify_with_heuristics. The other rules only read where or how a test failed,
# so they stay below high.
_CONFIDENCE: dict[Category, Confidence] = {
    Category.FLAKY: Confidence.HIGH,
    Category.ENVIRONMENT: Confidence.MEDIUM,
    Category.TEST_BUG: Confidence.LOW,
    Category.PRODUCT_BUG: Confidence.LOW,
}

_TEXT: dict[Category, tuple[str, str]] = {
    Category.FLAKY: (
        "Failed, then passed on retry",
        "Find the source of nondeterminism in the test or the code it calls",
    ),
    Category.ENVIRONMENT: (
        "The failure points at the CI environment",
        "Check the service or setting in the quote, then rerun",
    ),
    Category.TEST_BUG: (
        "The failing frame is in test code",
        "Check the test setup and expected values",
    ),
    Category.PRODUCT_BUG: (
        "The failing frame is in source code",
        "Check the source code at the failing frame",
    ),
    Category.UNKNOWN: (
        "No clear cause in the failure output",
        "Read the failure output, there is not enough evidence to classify it",
    ),
}


def classify_with_heuristics(group: FailureGroup) -> Classification:
    """Classify a group from its signals alone, unknown when they say nothing."""
    found = signals(group)
    verdict = heuristic_verdict(found)
    evidence = [s.quote for s in found]
    category = verdict if verdict and evidence else Category.UNKNOWN
    summary, next_step = _TEXT[category]
    confidence = _CONFIDENCE.get(category, Confidence.LOW)
    if category is Category.FLAKY and any(
        r.status is not Status.PASSED_ON_RETRY for r in group.results
    ):
        # Other tests with this signature failed every attempt, so only part of the group is proven.
        confidence = Confidence.MEDIUM
    return Classification(
        category=category,
        confidence=confidence,
        summary=summary,
        evidence=evidence,
        next_step=next_step,
        classified_by=ClassifiedBy.HEURISTICS,
        heuristic_verdict=verdict,
        agrees_with_heuristics=None,
    )


def heuristic_verdict(found: list[Signal]) -> Category | None:
    """The Category the rules derive from the signals, or None when nothing is clear."""
    names = {s.name for s in found}
    for required, excluded, category in _RULES:
        if required in names and excluded not in names:
            return category
    return None


def signals(group: FailureGroup) -> list[Signal]:
    """Signals of a group, each with the first quote that proves it."""
    results = [redact_result(r) for r in group.results]
    found = [_passed_on_retry(results)]
    lines = [line for r in results for line in _failure_lines(r)]
    for name, pattern in _TEXT_PATTERNS.items():
        found.append(_from_lines(name, pattern, lines))
    found.append(_frame_signal(group.signature.frame, results))
    found.append(_assertion_mismatch(group.signature.exception_type, lines))
    return [s for s in found if s]


def _passed_on_retry(results: list[TestResult]) -> Signal | None:
    for result in results:
        if result.status is Status.PASSED_ON_RETRY:
            passed = next(
                (n for n, a in enumerate(result.attempts, 1) if a.status is Status.PASSED), 0
            )
            when = f"attempt {passed}" if passed else "retry"
            quote = f"{result.test_id}: failed, then passed on {when}"
            return Signal(name=SignalName.PASSED_ON_RETRY, quote=quote)
    return None


def _frame_signal(frame: str | None, results: list[TestResult]) -> Signal | None:
    if not frame:
        return None
    path = frame.rsplit(":", 1)[0] if ":" in frame else None
    in_tests = _is_test_path(path) if path else _is_java_test(frame)
    name = SignalName.FRAME_IN_TEST_CODE if in_tests else SignalName.FRAME_IN_SOURCE_CODE
    # The last trace line that names the file is the frame closest to the exception.
    trace = [line.strip() for r in results for line in _trace_lines(r)]
    quote = next((line for line in reversed(trace) if path and path in line), frame)
    return Signal(name=name, quote=quote[:_QUOTE_LIMIT])


def _assertion_mismatch(exception_type: str, lines: list[str]) -> Signal | None:
    if exception_type.rsplit(".", 1)[-1] not in _ASSERTION_TYPES:
        return None
    quote = next((line for line in lines if exception_type in line), lines[0] if lines else "")
    return Signal(
        name=SignalName.ASSERTION_MISMATCH, quote=(quote or exception_type)[:_QUOTE_LIMIT]
    )


def _is_test_path(path: str) -> bool:
    *directories, filename = path.split("/")
    if path.startswith("/"):
        # Only the end of an absolute path belongs to the project, a checkout may sit in `test/`.
        directories = directories[-2:]
    return not _TEST_DIRS.isdisjoint(directories) or bool(_TEST_FILE.match(filename))


def _is_java_test(frame: str) -> bool:
    return bool(_JAVA_TEST_CLASS.search(frame.rsplit(".", 1)[0]))


def _trace_lines(result: TestResult) -> Iterator[str]:
    for attempt in result.attempts:
        if attempt.status in (Status.FAILED, Status.ERROR):
            yield from (attempt.stack_trace or "").splitlines()


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
