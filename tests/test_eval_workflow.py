from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


def _load(name: str) -> dict[Any, Any]:
    loaded = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _scripts(workflow: dict[Any, Any]) -> list[str]:
    return [
        step["run"] for job in workflow["jobs"].values() for step in job["steps"] if "run" in step
    ]


def test_the_eval_workflow_runs_only_when_someone_starts_it() -> None:
    triggers = _load("eval.yml")[True]

    assert list(triggers) == ["workflow_dispatch"]


def test_regular_workflows_never_run_the_eval() -> None:
    others = [p for p in WORKFLOWS.glob("*.yml") if p.name != "eval.yml"]

    assert others
    for path in others:
        text = path.read_text(encoding="utf-8")
        assert "failtriage eval" not in text, path.name
        assert "eval.yml" not in text, path.name


def test_the_eval_workflow_can_only_read_the_repository() -> None:
    assert _load("eval.yml")["permissions"] == {"contents": "read"}


def test_the_key_comes_from_a_secret_and_stays_out_of_the_scripts() -> None:
    workflow = _load("eval.yml")

    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    keyed = [s for s in steps if "ANTHROPIC_API_KEY" in s.get("env", {})]
    assert [s["env"]["ANTHROPIC_API_KEY"] for s in keyed] == ["${{ secrets.ANTHROPIC_API_KEY }}"]
    assert all("${{" not in script for script in _scripts(workflow))


def test_the_eval_workflow_fails_without_a_key_instead_of_scoring_heuristics_only() -> None:
    script = "\n".join(_scripts(_load("eval.yml")))

    assert '-z "$ANTHROPIC_API_KEY"' in script
    assert "exit 1" in script


def test_the_result_is_kept_as_an_artifact_and_in_the_job_summary() -> None:
    workflow = _load("eval.yml")

    uploads = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert [u["with"]["path"] for u in uploads] == ["evals/results/"]
    assert "evals/results/" in "\n".join(_scripts(workflow))
    assert "GITHUB_STEP_SUMMARY" in "\n".join(_scripts(workflow))
