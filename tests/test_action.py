import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "analyze.sh"

# Stands in for uv: logs how it was called, answers the analyze call with a report
# that has two groups, and runs the group counting one-liner with the real python.
FAKE_UV = f"""#!{sys.executable}
import json, os, sys

argv = sys.argv[1:]
with open(os.environ["UV_LOG"], "a") as log:
    log.write(json.dumps({{"argv": argv, "anthropic_key": "ANTHROPIC_API_KEY" in os.environ,
                           "github_token": os.environ.get("GITHUB_TOKEN")}}) + "\\n")
if "failtriage" in argv:
    if os.environ.get("UV_FAIL"):
        sys.exit(int(os.environ["UV_FAIL"]))
    print(json.dumps({{"groups": [{{}}, {{}}]}}))
else:
    os.execv(sys.executable, [sys.executable, *argv[argv.index("python") + 1:]])
"""


def run_script(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], Any]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV, encoding="utf-8")
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    outputs = tmp_path / "output"
    log = tmp_path / "uv.log"
    base = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "UV_LOG": str(log),
        "GITHUB_OUTPUT": str(outputs),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ACTION_PATH": str(ROOT),
        "JUNIT": "reports/junit.xml",
        "MODEL": "claude-sonnet-5-5",
        "COMMENT": "true",
        "COMMENT_KEY": "",
        "EVENT_NAME": "pull_request",
        "REPO": "acme/wallet",
        "HEAD_REPO": "acme/wallet",
        "PR_NUMBER": "7",
        "ACTOR": "someone",
        "GITHUB_TOKEN": "gh-token",
        "ANTHROPIC_API_KEY": "sk-test",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={**base, **env},
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def analyze_call(calls: list[Any]) -> Any:
    return next(c for c in calls if "failtriage" in c["argv"])


def test_a_pull_request_from_the_same_repo_gets_a_comment(tmp_path: Path) -> None:
    result, calls = run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    call = analyze_call(calls)
    argv = call["argv"]
    assert argv[argv.index("--repo") + 1] == "acme/wallet"
    assert argv[argv.index("--pr") + 1] == "7"
    assert "--comment" in argv
    assert "--summary" in argv
    assert call["anthropic_key"] is True
    assert call["github_token"] == "gh-token"


def test_the_comment_key_is_passed_on(tmp_path: Path) -> None:
    _, calls = run_script(tmp_path, COMMENT_KEY="e2e")

    argv = analyze_call(calls)["argv"]
    assert argv[argv.index("--comment-key") + 1] == "e2e"


def test_no_comment_key_input_leaves_the_default(tmp_path: Path) -> None:
    _, calls = run_script(tmp_path)

    assert "--comment-key" not in analyze_call(calls)["argv"]


@pytest.mark.parametrize(
    "env",
    [
        {"HEAD_REPO": "stranger/wallet"},
        {"ACTOR": "dependabot[bot]"},
        {"COMMENT": "false"},
    ],
    ids=["fork", "dependabot", "comment-input-off"],
)
def test_without_write_access_the_report_stays_in_the_summary_and_the_llm_is_skipped(
    tmp_path: Path, env: dict[str, str]
) -> None:
    result, calls = run_script(tmp_path, **env)

    assert result.returncode == 0, result.stderr
    call = analyze_call(calls)
    assert "--comment" not in call["argv"]
    assert "--comment-key" not in call["argv"]
    assert "--summary" in call["argv"]
    assert call["anthropic_key"] is False


def test_a_push_run_has_no_pull_request_to_talk_to(tmp_path: Path) -> None:
    _, calls = run_script(tmp_path, EVENT_NAME="push", PR_NUMBER="", HEAD_REPO="")

    call = analyze_call(calls)
    for flag in ["--repo", "--pr", "--comment"]:
        assert flag not in call["argv"]
    assert call["anthropic_key"] is True


def test_the_report_and_the_group_count_become_outputs(tmp_path: Path) -> None:
    run_script(tmp_path)

    lines = (tmp_path / "output").read_text().splitlines()
    report = tmp_path / "failtriage-report.json"
    assert f"report={report}" in lines
    assert "groups=2" in lines
    assert json.loads(report.read_text())["groups"] == [{}, {}]


def test_an_input_cannot_run_as_shell(tmp_path: Path) -> None:
    junit = f"a b; touch {tmp_path}/pwned #"

    _, calls = run_script(tmp_path, JUNIT=junit)

    argv = analyze_call(calls)["argv"]
    assert argv[argv.index("--junit") + 1] == junit
    assert not (tmp_path / "pwned").exists()


def test_a_failed_analysis_fails_the_step(tmp_path: Path) -> None:
    result, _ = run_script(tmp_path, UV_FAIL="2")

    assert result.returncode == 2
    assert not (tmp_path / "output").exists()


def load_action() -> Any:
    return yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))


def test_the_action_is_a_composite_with_the_documented_inputs_and_outputs() -> None:
    action = load_action()

    assert action["runs"]["using"] == "composite"
    assert action["inputs"]["junit"]["required"] is True
    assert action["inputs"]["github-token"]["default"] == "${{ github.token }}"
    assert action["inputs"]["comment"]["default"] == "true"
    assert set(action["outputs"]) == {"report", "groups"}


def test_the_action_refuses_pull_request_target_before_anything_else() -> None:
    first = load_action()["runs"]["steps"][0]

    assert first["if"] == "github.event_name == 'pull_request_target'"
    assert "exit 1" in first["run"]


def test_no_step_puts_an_expression_into_a_shell_script() -> None:
    steps = load_action()["runs"]["steps"]

    scripts = [step["run"] for step in steps if "run" in step]
    assert scripts
    assert all("${{" not in script for script in scripts)


def test_no_workflow_runs_on_pull_request_target() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

    assert workflows
    for path in workflows:
        triggers = yaml.safe_load(path.read_text(encoding="utf-8"))[True]
        names = triggers if isinstance(triggers, (list, dict)) else [triggers]
        assert "pull_request_target" not in names, path.name
