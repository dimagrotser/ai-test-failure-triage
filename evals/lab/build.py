import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from failtriage.parsers.junit import parse_junit

WALLET = Path(__file__).parent / "wallet"


class LabError(Exception):
    """A scenario failed one of the checks that make its label trustworthy."""


def build_case(scenario_dir: Path, cases_dir: Path) -> Path:
    patch = scenario_dir / "diff.patch"
    _reject_test_edits(patch)
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "app"
        shutil.copytree(WALLET, app, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        tests_sha = _tree_sha(app / "tests")

        history = _run_baseline(app, Path(tmp) / "baseline.xml")
        if any(entry["status"] != "passed" for entry in history):
            raise LabError("the wallet tests fail without the scenario applied")

        _git_apply(app, patch)
        junit = Path(tmp) / "junit.xml"
        if _run_pytest(app, junit).returncode != 1:
            raise LabError(f"scenario {scenario_dir.name} does not fail the wallet tests")

        _git_apply(app, patch, reverse=True)
        if _run_pytest(app, Path(tmp) / "reverted.xml").returncode != 0:
            raise LabError(
                f"reverting the patch of {scenario_dir.name} does not turn the run green"
            )
        if _tree_sha(app / "tests") != tests_sha:
            raise LabError(f"scenario {scenario_dir.name} changed the tests")

        case = cases_dir / scenario_dir.name
        case.mkdir(parents=True)
        shutil.copy(junit, case / "junit.xml")
        shutil.copy(patch, case / "diff.patch")
        shutil.copy(scenario_dir / "scenario.yaml", case / "label.yaml")
        (case / "history.json").write_text(json.dumps(history, indent=2) + "\n")
    return case


def _reject_test_edits(patch: Path) -> None:
    for line in patch.read_text().splitlines():
        if line.startswith(("--- a/tests/", "+++ b/tests/")):
            raise LabError(f"the patch of {patch.parent.name} edits files under tests/")


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


def _run_pytest(app: Path, junit: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
        cwd=app,
        env=env,
        capture_output=True,
        text=True,
    )
