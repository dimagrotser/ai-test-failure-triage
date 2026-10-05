import json
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from typer.testing import CliRunner

from failtriage import cli
from failtriage.classify.provider import recording_path
from failtriage.cli import app
from failtriage.github.client import GitHubClient
from failtriage.models import Category, ClassifiedBy
from failtriage.prompts import load_prompt
from failtriage.report.json_output import AnalysisReport

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"
THREE_CAUSES = FIXTURES / "junit" / "three_causes.xml"
RECORDINGS = FIXTURES / "llm"
MODEL = cli.DEFAULT_MODEL

PR_FILES = [
    {
        "filename": "shop/cart.py",
        "status": "modified",
        "patch": (
            "@@ -19,3 +19,3 @@\n-    return self.subtotal - self.discount\n"
            "+    return self.subtotal"
        ),
    },
]


def replay_recordings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API is a local handler that answers from tests/fixtures/llm, found by the payload.
    A payload without a recording gets a 500, so that group falls back to the heuristics."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-ReplayOnly0123456789")

    def handle(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)["messages"][0]["content"]
        path = recording_path(RECORDINGS, load_prompt().version, MODEL, payload)
        if not path.is_file():
            return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error"}})
        recording = json.loads(path.read_text(encoding="utf-8"))
        body = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [{"type": "text", "text": recording["response"]}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": recording["usage"],
        }
        return httpx2.Response(200, json=body)

    def make_client() -> anthropic.Anthropic:
        return anthropic.Anthropic(
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle)),
        )

    monkeypatch.setattr(cli, "_client", make_client)


class PullRequest:
    """One pull request on GitHub: its files, no history artifacts and the comments it keeps."""

    def __init__(self) -> None:
        self.comments: list[dict[str, Any]] = []
        self.writes: list[httpx.Request] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_ReplayOnly0123456789")
        monkeypatch.setattr(
            cli,
            "_github_client",
            lambda token: GitHubClient(token, transport=httpx.MockTransport(self._handle)),
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/pulls/7/files"):
            return httpx.Response(200, json=PR_FILES)
        if path.endswith("/actions/artifacts"):
            return httpx.Response(200, json={"total_count": 0, "artifacts": []})
        if request.method == "GET":
            return httpx.Response(200, json=self.comments)
        self.writes.append(request)
        body = json.loads(request.content)["body"]
        if request.method == "POST":
            comment = {
                "id": 100 + len(self.comments),
                "body": body,
                "user": {"login": "github-actions[bot]", "type": "Bot"},
            }
            self.comments.append(comment)
            return httpx.Response(201, json=comment)
        comment_id = int(path.rsplit("/", 1)[1])
        [comment] = [c for c in self.comments if c["id"] == comment_id]
        comment["body"] = body
        return httpx.Response(200, json=comment)


def analyze_pr(*extra: str) -> Any:
    args = ["analyze", "--junit", str(THREE_CAUSES), "--repo", "acme/shop", "--pr", "7"]
    return runner.invoke(app, [*args, *extra])


def test_three_causes_become_three_groups_with_their_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_recordings(monkeypatch)
    PullRequest().install(monkeypatch)

    result = analyze_pr("--json")

    assert result.exit_code == 0
    report = AnalysisReport.model_validate_json(result.stdout)
    assert len(report.groups) == 3
    classifications = [g.classification for g in report.groups]
    assert all(c.classified_by is ClassifiedBy.LLM for c in classifications)
    assert {c.category for c in classifications} == {
        Category.PRODUCT_BUG,
        Category.FLAKY,
        Category.ENVIRONMENT,
    }
    raw_report = THREE_CAUSES.read_text(encoding="utf-8")
    for group in report.groups:
        # A quote is from the report itself or from a signal, which quotes its proof.
        known = [raw_report, *(s.quote for s in group.signals)]
        assert group.classification.evidence
        for quote in group.classification.evidence:
            assert any(quote in text for text in known)
    assert report.cost.llm_calls == 3


def test_the_three_groups_go_into_one_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    replay_recordings(monkeypatch)
    github = PullRequest()
    github.install(monkeypatch)

    result = analyze_pr("--comment")

    assert result.exit_code == 0
    assert [w.method for w in github.writes] == ["POST"]
    [comment] = github.comments
    assert comment["body"].startswith("<!-- failtriage:default -->\n")
    for text in ("product_bug", "flaky", "environment", "test_total_with_discount"):
        assert text in comment["body"]


def test_a_second_run_updates_the_comment_instead_of_adding_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_recordings(monkeypatch)
    github = PullRequest()
    github.install(monkeypatch)

    analyze_pr("--comment")
    [first] = github.comments
    second_run = analyze_pr("--comment")

    assert [w.method for w in github.writes] == ["POST", "PATCH"]
    assert github.writes[1].url.path.endswith(f"/issues/comments/{first['id']}")
    assert len(github.comments) == 1
    assert "comment updated" in second_run.stderr
