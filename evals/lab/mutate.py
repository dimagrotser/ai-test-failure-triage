import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml

from evals.lab.build import SCENARIOS, WALLET, LabError, build_case
from evals.lab.environment import lab_environment

PREFIX = "product-bug-mutant-"
MUTMUT_CONFIG = """\
[tool.mutmut]
source_paths = ["wallet/"]
pytest_add_cli_args_test_selection = ["tests/"]
also_copy = ["conftest.py", "pytest.ini"]
"""

_RESULT = re.compile(r"^\s*(?P<function>\S+)__mutmut_(?P<number>\d+): killed$")


def write_scenarios(
    results: str, show: Callable[[str], str], scenarios_dir: Path = SCENARIOS
) -> list[Path]:
    """Write one scenario per mutated function, from the lowest-numbered mutant the tests killed.

    A mutant is only kept if applying its diff to the wallet as a plain patch fails the tests
    and reverting it turns them green, which is the check `make lab` runs on every scenario.
    """
    killed: dict[str, int] = {}
    for line in results.splitlines():
        match = _RESULT.match(line)
        if match:
            function = match["function"]
            killed[function] = min(int(match["number"]), killed.get(function, sys.maxsize))

    with tempfile.TemporaryDirectory() as tmp:
        candidates: list[Path] = []
        for function, number in sorted(killed.items()):
            name = f"{function}__mutmut_{number}"
            candidate = _scenario(function, name, show(name), Path(tmp) / "scenarios")
            if candidate is None:
                continue
            try:
                build_case(candidate, Path(tmp) / "cases")
            except LabError:
                continue
            candidates.append(candidate)

        scenarios_dir.mkdir(parents=True, exist_ok=True)
        for old in scenarios_dir.glob(f"{PREFIX}*"):
            shutil.rmtree(old)
        return [
            shutil.copytree(candidate, scenarios_dir / candidate.name) for candidate in candidates
        ]


def _scenario(function: str, name: str, diff: str, root: Path) -> Path | None:
    _, module, local = function.split(".", 2)
    path = f"wallet/{module}.py"
    patch = _git_patch(WALLET / path, path, diff)
    if patch is None:
        return None
    scenario = root / f"{PREFIX}{module}-{_slug(local)}"
    scenario.mkdir(parents=True)
    (scenario / "diff.patch").write_text(patch)
    label = {
        "category": "product_bug",
        "source": "mutation",
        "scenario": f"mutmut changes {_slug(local).replace('-', ' ')} in {path}",
        "notes": (
            f"mutmut mutant {name}. The wallet tests kill it, so they fail on the changed code "
            "and pass again once the patch is reverted."
        ),
    }
    (scenario / "scenario.yaml").write_text(yaml.safe_dump(label, sort_keys=False, width=100))
    return scenario


def _git_patch(source: Path, path: str, diff: str) -> str | None:
    # `mutmut show` numbers its hunks from the start of the function, which git apply
    # rejects. Find the function's lines in the file and diff the real thing instead.
    lines = diff.splitlines()
    lines = lines[next(i for i, line in enumerate(lines) if line.startswith("@@")) + 1 :]
    old = "".join(f"{line[1:]}\n" for line in lines if line.startswith((" ", "-")))
    new = "".join(f"{line[1:]}\n" for line in lines if line.startswith((" ", "+")))
    original = source.read_text()
    if original.count(old) != 1:
        return None
    mutated = original.replace(old, new)
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            mutated.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _slug(local_name: str) -> str:
    # mutmut names functions x_name and methods xǁClassǁname.
    return re.sub(r"[^a-z0-9]+", "-", local_name[1:].lower()).strip("-")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp, lab_environment(Path(tmp) / "env", None) as env:
        work = Path(tmp) / "wallet"
        shutil.copytree(WALLET, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        (work / "pyproject.toml").write_text(MUTMUT_CONFIG)

        def mutmut(*args: str) -> str:
            result = subprocess.run(
                [sys.executable, "-m", "mutmut", *args],
                cwd=work,
                env={**os.environ, **env},
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                sys.exit(f"lab: mutmut {' '.join(args)} failed\n{result.stdout}{result.stderr}")
            return result.stdout

        mutmut("run")
        written = write_scenarios(mutmut("results", "--all", "true"), lambda n: mutmut("show", n))
    print(f"lab: wrote {len(written)} mutation scenarios, run make lab to build the cases")


if __name__ == "__main__":
    main()
