from typing import Literal

from pydantic import BaseModel

from failtriage.github.client import GitHubClient


class _User(BaseModel):
    type: str


class _Comment(BaseModel):
    id: int
    body: str | None = None
    user: _User | None = None


def marker(key: str) -> str:
    return f"<!-- failtriage:{key} -->"


def upsert_comment(
    client: GitHubClient, repo: str, pr: int, key: str, body: str, *, has_failures: bool
) -> Literal["created", "updated", "skipped"]:
    """Update the bot comment of this `key`, or create it when there is something to report.

    A run with no failures and no comment to refresh posts nothing. With several matches
    the first is updated and the rest stay as they are."""
    existing = _find(client, repo, pr, key)
    if existing is not None:
        client.patch_json(f"/repos/{repo}/issues/comments/{existing.id}", {"body": body})
        return "updated"
    if not has_failures:
        return "skipped"
    client.post_json(f"/repos/{repo}/issues/{pr}/comments", {"body": body})
    return "created"


def _find(client: GitHubClient, repo: str, pr: int, key: str) -> _Comment | None:
    tag = marker(key)
    for item in client.get_pages(f"/repos/{repo}/issues/{pr}/comments"):
        comment = _Comment.model_validate(item)
        # The marker must open the body: a person can quote it, and a quote is not ours.
        if comment.user and comment.user.type == "Bot" and (comment.body or "").startswith(tag):
            return comment
    return None
