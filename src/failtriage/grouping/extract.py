import re

from failtriage.models import Attempt

_TYPE_PREFIX = re.compile(r"^(?P<type>[A-Za-z_][\w.$]*):\s*(?P<rest>.*)$")
_PYTEST_EXCEPTION_LINE = re.compile(r"^E\s+(?P<text>\S.*)$", re.MULTILINE)
# The last line of a pytest failure: `tests/test_cart.py:21: AssertionError`.
_PYTEST_LOCATION_TYPE = re.compile(r"^\S+:\d+: (?P<type>[A-Za-z_][\w.$]*)$", re.MULTILINE)

_PYTEST_FRAME = re.compile(r"^(?P<path>[^\s:<>]+\.\w+):\d+: (?P<rest>.*)$")
_PYTEST_FUNCTION = re.compile(r"^\s*(?:async\s+)?def (?P<name>\w+)\(")
_PYTHON_FRAME = re.compile(r'^\s*File "(?P<path>[^"]+)", line \d+, in (?P<func>\S+)')
_JS_FRAME = re.compile(r"^\s*at (?:(?P<func>.+?) \()?(?P<path>[^()\s]+?):\d+:\d+\)?$")
_JAVA_FRAME = re.compile(r"^\s*at (?:[\w.]+/)?(?P<method>[\w.$<>]+)\([^)]*\)")

_EXTERNAL_DIRS = {"site-packages", "dist-packages", "node_modules", "_pytest", "pluggy"}
_STDLIB_DIR = re.compile(r"/(?:lib/python[\d.]*|stdlib)/")
_JAVA_FRAMEWORK_PREFIXES = (
    "java.",
    "javax.",
    "jdk.",
    "sun.",
    "com.sun.",
    "junit.",
    "org.junit.",
    "org.opentest4j.",
    "org.testng.",
    "org.apache.maven.",
    "org.gradle.",
    "worker.org.gradle.",
)


def exception_type(attempt: Attempt) -> str:
    """Name of the exception, from the message prefix or else from the pytest trace."""
    from_message = _TYPE_PREFIX.match(_first_line(attempt.message))
    if from_message:
        return from_message["type"]
    trace = attempt.stack_trace or ""
    for line in reversed(_PYTEST_EXCEPTION_LINE.findall(trace)):
        if match := _TYPE_PREFIX.match(line):
            return match["type"]
    if locations := _PYTEST_LOCATION_TYPE.findall(trace):
        return str(locations[-1])
    return ""


def first_message_line(attempt: Attempt) -> str:
    """The first line of the message without the exception name, which is kept apart."""
    line = _first_line(attempt.message)
    if not line:
        exception_lines = _PYTEST_EXCEPTION_LINE.findall(attempt.stack_trace or "")
        line = exception_lines[-1] if exception_lines else ""
    match = _TYPE_PREFIX.match(line)
    return match["rest"] if match else line


def top_frame(attempt: Attempt) -> str | None:
    """File and function of the frame closest to the exception that is project code."""
    trace = attempt.stack_trace or ""
    return _pytest_frame(trace) or _python_frame(trace) or _js_frame(trace) or _java_frame(trace)


def _first_line(text: str | None) -> str:
    return text.strip().splitlines()[0].strip() if text and text.strip() else ""


def _pytest_frame(trace: str) -> str | None:
    frame = None
    block: list[str] = []
    for line in trace.splitlines():
        match = _PYTEST_FRAME.match(line)
        if not match:
            block.append(line)
            continue
        path = match["path"]
        if not _is_external(path):
            frame = f"{path}:{_pytest_function(match['rest'], block)}"
        block = []
    return frame


def _pytest_function(rest: str, block: list[str]) -> str:
    if rest.startswith("in "):
        return rest[3:].split()[0]
    for line in reversed(block):
        if match := _PYTEST_FUNCTION.match(line):
            return match["name"]
    return ""


def _python_frame(trace: str) -> str | None:
    frame = None
    for line in trace.splitlines():
        if (match := _PYTHON_FRAME.match(line)) and not _is_external(match["path"]):
            frame = f"{_display_path(match['path'])}:{match['func']}"
    return frame


def _js_frame(trace: str) -> str | None:
    for line in trace.splitlines():
        match = _JS_FRAME.match(line)
        if match and not _is_external(match["path"]):
            return f"{_display_path(match['path'])}:{match['func'] or ''}"
    return None


def _java_frame(trace: str) -> str | None:
    for line in trace.splitlines():
        match = _JAVA_FRAME.match(line)
        if match and not match["method"].startswith(_JAVA_FRAMEWORK_PREFIXES):
            return match["method"]
    return None


def _is_external(path: str) -> bool:
    path = path.replace("\\", "/")
    if path.startswith(("node:", "<")) or _STDLIB_DIR.search(path):
        return True
    return not _EXTERNAL_DIRS.isdisjoint(path.split("/"))


def _display_path(path: str) -> str:
    # An absolute path differs between machines, its last two parts do not.
    path = path.replace("\\", "/")
    if path.startswith("/") or re.match(r"[A-Za-z]:/", path):
        return "/".join(path.split("/")[-2:])
    return path
