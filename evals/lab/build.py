import hashlib
import json
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from evals.lab.environment import CONDITIONS, lab_environment
from failtriage.models import Category, Status
from failtriage.parsers.junit import parse_junit
from failtriage.redaction import redact

LAB = Path(__file__).parent
WALLET = LAB / "wallet"
SCENARIOS = LAB / "scenarios"
CASES = LAB.parent / "cases"
REAL = LAB / "real"

# Per category: the directory a patch may change and the one that must stay as it was.
# That is what makes the counterfactual meaningful: a product_bug is fixed in the app
# alone, a test_bug in the tests alone. An environment case keeps its patch (an unrelated
# change to the app) and is fixed by restoring the environment instead. A flaky case adds
# both app code and a test, and is checked by running it with and without retries. An
# unknown case may change either side, because the ambiguity is the point. It has no
# counterfactual, but it must fail and must not pass on retry, which would be evidence.
SCOPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "product_bug": (("wallet",), ("tests",)),
    "test_bug": (("tests",), ("wallet",)),
    "environment": (("wallet",), ("tests",)),
    "flaky": (("wallet", "tests"), ()),
    "unknown": (("wallet", "tests"), ()),
}
KINDS = ("timing", "randomness", "order_dependence")
LABEL_FIELDS = ("category", "source", "scenario", "notes")


class LabError(Exception):
    """A scenario failed one of the checks that make its label trustworthy."""


def build_case(scenario_dir: Path, cases_dir: Path) -> Path:
    scenario_dir = scenario_dir.resolve()
    patch = scenario_dir / "diff.patch"
    label = _load_label(scenario_dir)
    editable, protected = SCOPES[label["category"]]
    _check_patch_scope(patch, label["category"], editable)
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "app"
        shutil.copytree(WALLET, app, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        protected_sha = _protected_sha(app, protected)

        history = _run_baseline(app, Path(tmp) / "baseline.xml")
        if any(entry["status"] != "passed" for entry in history):
            raise LabError(f"the wallet tests fail without {scenario_dir.name} applied")

        _git_apply(app, patch)
        junit = Path(tmp) / "junit.xml"
        condition = label.get("condition")
        if label["category"] == "flaky":
            history = _check_flaky(app, junit, scenario_dir.name)
        elif _run_pytest(app, junit, condition).returncode != 1:
            raise LabError(f"scenario {scenario_dir.name} does not fail the wallet tests")
        elif label["category"] == "unknown":
            _check_not_flaky(app, junit, scenario_dir.name)
        elif condition:
            if _run_pytest(app, Path(tmp) / "restored.xml").returncode != 0:
                raise LabError(
                    f"restoring the environment of {scenario_dir.name} does not turn the run green"
                )
        else:
            _git_apply(app, patch, reverse=True)
            if _run_pytest(app, Path(tmp) / "reverted.xml").returncode != 0:
                raise LabError(
                    f"reverting the patch of {scenario_dir.name} does not turn the run green"
                )
        if _protected_sha(app, protected) != protected_sha:
            raise LabError(f"scenario {scenario_dir.name} changed {', '.join(protected)}/")

        case = cases_dir / scenario_dir.name
        shutil.rmtree(case, ignore_errors=True)
        case.mkdir(parents=True)
        (case / "junit.xml").write_text(_normalize_junit(junit.read_text(), Path(tmp)))
        shutil.copy(patch, case / "diff.patch")
        shutil.copy(scenario_dir / "scenario.yaml", case / "label.yaml")
        if label.get("history") == "none":
            history = []
        (case / "history.json").write_text(json.dumps(history, indent=2) + "\n")
    return case


def _normalize_junit(xml: str, tmp: Path) -> str:
    # Timings, the start time, the host name, object addresses in reprs, the temp dir, the
    # stub's port and the interpreter's stdlib path change between runs or machines and
    # would break byte-identical rebuilds. The rest of each message and traceback stays as
    # pytest wrote it.
    for path in sorted({str(tmp), str(tmp.resolve())}, key=len, reverse=True):
        xml = xml.replace(path, "/tmp/lab")
    xml = xml.replace(sysconfig.get_paths()["stdlib"], "/stdlib")
    xml = re.sub(r"127\.0\.0\.1(:|', )\d+", r"127.0.0.1\g<1>0", xml)
    xml = re.sub(r"0x[0-9a-f]{6,}", "0x0000000000", xml)
    xml = re.sub(r' time="[^"]*"', ' time="0.000"', xml)
    xml = re.sub(r' timestamp="[^"]*"', ' timestamp="2026-01-01T00:00:00+00:00"', xml)
    return re.sub(r' hostname="[^"]*"', ' hostname="lab"', xml)


def redact_tree(root: ET.Element) -> None:
    """Redact every text and attribute value of a JUnit tree in place."""
    for element in root.iter():
        if element.text:
            element.text = redact(element.text)
        if element.tail:
            element.tail = redact(element.tail)
        name, value = element.get("name"), element.get("value")
        if element.tag == "property" and name and value:
            # A property is an env var: the name is what marks the value as a secret.
            masked = redact(f"{name}={value}")
            element.set("value", masked[len(name) + 1 :] if masked.startswith(f"{name}=") else "")
        for key, attribute in element.attrib.items():
            element.set(key, redact(attribute))


def check_redacted(junit: Path) -> None:
    """Fail unless a second redaction pass changes nothing in the file.

    Nothing is quoted: the message must not leak the text it complains about.
    """
    try:
        root = ET.parse(junit).getroot()
    except (OSError, ET.ParseError) as exc:
        raise LabError(f"{junit.parent.name}: cannot read junit.xml") from exc
    # Tags and attribute names stay as they are, so a secret there cannot be masked.
    names = [element.tag for element in root.iter()] + [k for e in root.iter() for k in e.attrib]
    before = ET.tostring(root)
    redact_tree(root)
    if ET.tostring(root) != before or any(redact(name) != name for name in names):
        raise LabError(f"{junit.parent.name}: junit.xml still contains redactable text")


def build_real_case(case_dir: Path, cases_dir: Path) -> Path:
    """Copy a hand-labeled real case into the dataset after checking it again."""
    try:
        label = yaml.safe_load((case_dir / "label.yaml").read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise LabError(f"real case {case_dir.name} has no readable label.yaml") from exc
    if not isinstance(label, dict):
        raise LabError(f"real case {case_dir.name} has no category, fill label.yaml by hand")
    if label.get("category") not in {c.value for c in Category}:
        raise LabError(f"real case {case_dir.name} has no category, fill label.yaml by hand")
    for field in ("scenario", "notes"):
        if not isinstance(label.get(field), str) or not label[field].strip():
            raise LabError(f"real case {case_dir.name} has no {field}, fill label.yaml by hand")
    check_redacted(case_dir / "junit.xml")
    names = ["junit.xml", "label.yaml"]
    diff = case_dir / "diff.patch"
    if diff.exists():
        if redact(diff.read_text()) != diff.read_text():
            raise LabError(f"{case_dir.name}: diff.patch still contains redactable text")
        names.append("diff.patch")
    case = cases_dir / case_dir.name
    shutil.rmtree(case, ignore_errors=True)
    case.mkdir(parents=True)
    for name in names:
        shutil.copy(case_dir / name, case / name)
    return case


def build_all(
    scenarios_dir: Path = SCENARIOS, cases_dir: Path = CASES, real_dir: Path = REAL
) -> None:
    # Build next to the target and swap at the end, so a scenario that fails its check
    # leaves the old dataset alone and a removed scenario does not leave its case behind.
    cases_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".cases-", dir=cases_dir.parent))
    try:
        for scenario in sorted(p for p in scenarios_dir.iterdir() if p.is_dir()):
            build_case(scenario, staging)
        for real in sorted(p for p in real_dir.glob("*") if p.is_dir() and p.name[0] != "."):
            build_real_case(real, staging)
        shutil.rmtree(cases_dir, ignore_errors=True)
        staging.rename(cases_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _load_label(scenario_dir: Path) -> dict[str, str]:
    label = yaml.safe_load((scenario_dir / "scenario.yaml").read_text())
    for field in LABEL_FIELDS:
        if not isinstance(label.get(field), str) or not label[field].strip():
            raise LabError(f"scenario {scenario_dir.name} has no {field} in scenario.yaml")
    if label["category"] not in SCOPES:
        raise LabError(f"scenario {scenario_dir.name} has unsupported category {label['category']}")
    if label["category"] == "environment":
        condition = label.get("condition")
        if not isinstance(condition, str) or not condition.strip():
            raise LabError(f"scenario {scenario_dir.name} has no condition in scenario.yaml")
        if condition not in CONDITIONS:
            raise LabError(f"scenario {scenario_dir.name} has unsupported condition {condition}")
    if label["category"] == "flaky" and label.get("kind") not in KINDS:
        raise LabError(f"scenario {scenario_dir.name} has unsupported kind {label.get('kind')}")
    if label.get("history", "none") != "none":
        raise LabError(f"scenario {scenario_dir.name} has unsupported history {label['history']}")
    return dict(label)


def _check_patch_scope(patch: Path, category: str, editable: tuple[str, ...]) -> None:
    for line in patch.read_text().splitlines():
        if line.startswith(("--- a/", "+++ b/")):
            path = line[6:]
            if not path.startswith(tuple(f"{directory}/" for directory in editable)):
                raise LabError(
                    f"the patch of {patch.parent.name} edits {path}, "
                    f"but a {category} case may only change {' and '.join(editable)}/"
                )


def _git_apply(app: Path, patch: Path, reverse: bool = False) -> None:
    args = ["git", "apply", *(["-R"] if reverse else []), str(patch)]
    result = subprocess.run(args, cwd=app, capture_output=True, text=True)
    if result.returncode != 0:
        raise LabError(f"cannot apply {patch}: {result.stderr.strip()}")


def _run_baseline(app: Path, junit: Path) -> list[dict[str, object]]:
    _run_pytest(app, junit)
    return _history_entries(junit, _tree_sha(app), run_id=1)


def _check_flaky(app: Path, junit: Path, name: str) -> list[dict[str, object]]:
    """Run the patched tree twice and return both runs as history.

    Without retries the flaky tests fail; with one retry they pass on the second Attempt.
    """
    sha = _tree_sha(app)
    first = junit.with_name("no-retries.xml")
    if _run_pytest(app, first).returncode != 1:
        raise LabError(f"scenario {name} does not fail the wallet tests")
    failed = {r.test_id for r in parse_junit(first) if r.status in (Status.FAILED, Status.ERROR)}
    if not failed:
        raise LabError(f"scenario {name} does not fail the wallet tests")

    if _run_pytest(app, junit, retries=1).returncode != 0:
        raise LabError(f"scenario {name} does not pass on retry")
    results = {r.test_id: r.status for r in parse_junit(junit)}
    if any(results[test_id] is not Status.PASSED_ON_RETRY for test_id in failed):
        raise LabError(f"scenario {name} does not pass on retry")
    return [*_history_entries(first, sha, run_id=1), *_history_entries(junit, sha, run_id=2)]


def _check_not_flaky(app: Path, junit: Path, name: str) -> None:
    retried = junit.with_name("retried.xml")
    _run_pytest(app, retried, retries=1)
    if any(r.status is Status.PASSED_ON_RETRY for r in parse_junit(retried)):
        raise LabError(f"scenario {name} passes on retry, which is evidence of flakiness")


def _history_entries(junit: Path, sha: str, run_id: int) -> list[dict[str, object]]:
    return [
        {
            "test_id": result.test_id,
            "status": result.status.value,
            "attempts": len(result.attempts),
            "sha": sha,
            "run_id": run_id,
        }
        for result in parse_junit(junit)
    ]


def _protected_sha(app: Path, protected: tuple[str, ...]) -> str:
    return hashlib.sha1("".join(_tree_sha(app / name) for name in protected).encode()).hexdigest()


def _tree_sha(app: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(app.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(app).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _run_pytest(
    app: Path, junit: Path, condition: str | None = None, retries: int = 0
) -> subprocess.CompletedProcess[str]:
    # A fixed environment, not the host's: pytest prints os.environ in the message of a
    # missing variable, which would put the host's variables into the case.
    base = {
        "PATH": "/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "WALLET_RETRIES": str(retries),
    }
    with lab_environment(junit.parent / f"{junit.stem}-env", condition) as env:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
            cwd=app,
            env={**base, **env},
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    try:
        build_all()
    except LabError as exc:
        sys.exit(f"lab: {exc}\nlab: evals/cases was left unchanged")
