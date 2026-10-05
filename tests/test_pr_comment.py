import json
from typing import Any

import httpx

from failtriage.github.client import GitHubClient
from failtriage.github.pr_comment import marker, upsert_comment


def tagged(text: str, key: str = "default") -> str:
    return f"{marker(key)}\n{text}"


REPO = "acme/wallet"
PAGE2 = "https://api.github.com/repositories/1/issues/7/comments?per_page=100&page=2"


def comment(id: int, body: str, user_type: str = "Bot") -> dict[str, Any]:
    return {"id": id, "body": body, "user": {"login": "someone", "type": user_type}}


class FakeGitHub:
    """Comments of one pull request, served in pages of `page_size`."""

    def __init__(self, comments: list[dict[str, Any]], page_size: int = 100) -> None:
        self.comments = comments
        self.page_size = page_size
        self.writes: list[httpx.Request] = []

    def client(self) -> GitHubClient:
        return GitHubClient("ghs_exampletoken", transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            page = int(request.url.params.get("page", "1"))
            start = (page - 1) * self.page_size
            items = self.comments[start : start + self.page_size]
            headers = {}
            if start + self.page_size < len(self.comments):
                headers["Link"] = f'<{PAGE2.replace("page=2", f"page={page + 1}")}>; rel="next"'
            return httpx.Response(200, json=items, headers=headers)
        self.writes.append(request)
        body = json.loads(request.content)["body"]
        if request.method == "POST":
            return httpx.Response(201, json=comment(99, body))
        comment_id = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=comment(comment_id, body))


def test_creates_a_comment_when_none_has_the_marker() -> None:
    github = FakeGitHub([comment(1, "Looks good to me", "User")])

    outcome = upsert_comment(
        github.client(), REPO, 7, "default", tagged("report"), has_failures=True
    )

    assert outcome == "created"
    assert [w.method for w in github.writes] == ["POST"]
    assert github.writes[0].url.path == "/repos/acme/wallet/issues/7/comments"
    assert json.loads(github.writes[0].content) == {"body": tagged("report")}


def test_updates_the_comment_with_the_marker_instead_of_adding_one() -> None:
    old = f"{marker('default')}\nold report"
    github = FakeGitHub([comment(1, "Looks good to me", "User"), comment(2, old)])

    outcome = upsert_comment(
        github.client(), REPO, 7, "default", tagged("new report"), has_failures=True
    )

    assert outcome == "updated"
    assert [w.method for w in github.writes] == ["PATCH"]
    assert github.writes[0].url.path == "/repos/acme/wallet/issues/comments/2"
    assert json.loads(github.writes[0].content)["body"].endswith("new report")


def test_finds_the_comment_on_a_later_page() -> None:
    fillers = [comment(i, "noise", "User") for i in range(1, 5)]
    github = FakeGitHub([*fillers, comment(5, f"{marker('default')}\nold")], page_size=2)

    outcome = upsert_comment(github.client(), REPO, 7, "default", tagged("new"), has_failures=True)

    assert outcome == "updated"
    assert github.writes[0].url.path.endswith("/issues/comments/5")


def test_with_several_matches_the_first_is_updated_and_the_others_are_left_alone() -> None:
    body = f"{marker('default')}\nold"
    github = FakeGitHub([comment(3, body), comment(8, body)])

    upsert_comment(github.client(), REPO, 7, "default", tagged("new"), has_failures=True)

    assert [w.url.path.rsplit("/", 1)[1] for w in github.writes] == ["3"]


def test_ignores_a_marker_from_a_human_and_a_marker_of_another_key() -> None:
    github = FakeGitHub(
        [
            comment(1, f"{marker('default')}\nquoted by a person", "User"),
            comment(2, f"{marker('e2e')}\nother report"),
        ]
    )

    outcome = upsert_comment(
        github.client(), REPO, 7, "default", tagged("report"), has_failures=True
    )

    assert outcome == "created"


def test_a_comment_that_only_quotes_the_marker_is_not_ours() -> None:
    github = FakeGitHub([comment(1, f"see {marker('default')} above")])

    outcome = upsert_comment(
        github.client(), REPO, 7, "default", tagged("report"), has_failures=True
    )

    assert outcome == "created"


def test_a_key_that_starts_like_another_key_does_not_match() -> None:
    github = FakeGitHub([comment(1, f"{marker('e2e-slow')}\nreport")])

    outcome = upsert_comment(
        github.client(), REPO, 7, "e2e", tagged("report", "e2e"), has_failures=True
    )

    assert outcome == "created"


def test_no_failures_and_no_comment_posts_nothing() -> None:
    github = FakeGitHub([comment(1, "Looks good to me", "User")])

    outcome = upsert_comment(
        github.client(), REPO, 7, "default", tagged("all green"), has_failures=False
    )

    assert outcome == "skipped"
    assert github.writes == []


def test_no_failures_with_a_comment_updates_it_to_all_green() -> None:
    github = FakeGitHub([comment(4, f"{marker('default')}\n3 failure groups")])

    outcome = upsert_comment(
        github.client(),
        REPO,
        7,
        "default",
        tagged("All 12 tests passed or were skipped.\n"),
        has_failures=False,
    )

    assert outcome == "updated"
    assert "All 12 tests passed" in json.loads(github.writes[0].content)["body"]
