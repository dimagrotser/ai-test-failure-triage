import re
from typing import Literal

from pydantic import BaseModel

from failtriage.github.client import GitHubClient


class _User(BaseModel):
    type: str


class _Comment(BaseModel):
    id: int
    body: str | None = None
    user: _User | None = None


# GitHub rejects a comment body over this many characters.
MAX_BODY_CHARS = 65536
CUT_NOTE = (
    "Report cut at the GitHub comment limit. The full report is in the job summary of this run."
)
_FENCE = re.compile(r"^(`{3,})(.*)$")


def marker(key: str) -> str:
    return f"<!-- failtriage:{key} -->"


def comment_body(report: str, key: str) -> tuple[str, bool]:
    """The report under its marker, cut to the GitHub limit. The flag says if it was cut."""
    body = f"{marker(key)}\n{report}"
    if len(body) <= MAX_BODY_CHARS:
        return body, False
    # Room for the note and a fence that closes a code block the cut may have opened.
    room = MAX_BODY_CHARS - len(CUT_NOTE) - len("\n\n") - len("\n" + "`" * 20)
    kept = body[:room]
    if "\n" in kept:
        kept = kept[: kept.rindex("\n")]
    open_fence = _open_fence(kept)
    closing = f"\n{open_fence}" if open_fence else ""
    return f"{kept}{closing}\n\n{CUT_NOTE}", True


def _open_fence(text: str) -> str | None:
    """The fence of a code block that `text` leaves open, if any."""
    fence: str | None = None
    for line in text.splitlines():
        found = _FENCE.match(line)
        if found is None:
            continue
        ticks, rest = found.groups()
        if fence is None:
            fence = ticks
        elif not rest.strip() and len(ticks) >= len(fence):
            fence = None
    return fence


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
