import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import yaml

from evals.lab.environment import CONDITIONS, lab_environment
from failtriage.parsers.junit import parse_junit

LAB = Path(__file__).parent
WALLET = LAB / "wallet"
SCENARIOS = LAB / "scenarios"
CASES = LAB.parent / "cases"

# Per category: the directory a patch may change and the one that must stay as it was.
# That is what makes the counterfactual meaningful: a product_bug is fixed in the app
# alone, a test_bug in the tests alone. An environment case keeps its patch (an unrelated
# change to the app) and is fixed by restoring the environment instead.
SCOPES = {
    "product_bug": ("wallet", "tests"),
    "test_bug": ("tests", "wallet"),
    "environment": ("wallet", "tests"),
}
ENVIRONMENT_VARIABLES = ("WALLET_LEDGER_URL", "WALLET_STATEMENTS_DIR")
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
        protected_sha = _tree_sha(app / protected)

        history = _run_baseline(app, Path(tmp) / "baseline.xml")
        if any(entry["status"] != "passed" for entry in history):
            raise LabError("the wallet tests fail without the scenario applied")

        _git_apply(app, patch)
        junit = Path(tmp) / "junit.xml"
        condition = label.get("condition")
        if _run_pytest(app, junit, condition).returncode != 1:
            raise LabError(f"scenario {scenario_dir.name} does not fail the wallet tests")

        if condition:
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
        if _tree_sha(app / protected) != protected_sha:
            raise LabError(f"scenario {scenario_dir.name} changed {protected}/")

        case = cases_dir / scenario_dir.name
        shutil.rmtree(case, ignore_errors=True)
        case.mkdir(parents=True)
        (case / "junit.xml").write_text(_normalize_junit(junit.read_text(), Path(tmp)))
        shutil.copy(patch, case / "diff.patch")
        shutil.copy(scenario_dir / "scenario.yaml", case / "label.yaml")
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


def build_all(cases_dir: Path = CASES) -> list[Path]:
    return [build_case(scenario, cases_dir) for scenario in sorted(SCENARIOS.iterdir())]


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
    return dict(label)


def _check_patch_scope(patch: Path, category: str, editable: str) -> None:
    for line in patch.read_text().splitlines():
        if line.startswith(("--- a/", "+++ b/")):
            path = line[6:]
            if not path.startswith(f"{editable}/"):
                raise LabError(
                    f"the patch of {patch.parent.name} edits {path}, "
                    f"but a {category} case may only change {editable}/"
                )


def _git_apply(app: Path, patch: Path, reverse: bool = False) -> None:
    args = ["git", "apply", *(["-R"] if reverse else []), str(patch)]
    result = subprocess.run(args, cwd=app, capture_output=True, text=True)
    if result.returncode != 0:
        raise LabError(f"cannot apply {patch}: {result.stderr.strip()}")


def _run_baseline(app: Path, junit: Path) -> list[dict[str, object]]:
    _run_pytest(app, junit)
    sha = _tree_sha(app)
    return [
        {
            "test_id": result.test_id,
            "status": result.status.value,
            "attempts": len(result.attempts),
            "sha": sha,
            "run_id": 1,
        }
        for result in parse_junit(junit)
    ]


def _tree_sha(app: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(app.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(app).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _run_pytest(
    app: Path, junit: Path, condition: str | None = None
) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in ENVIRONMENT_VARIABLES}
    base |= {"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
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
        sys.exit(f"lab: {exc}")
