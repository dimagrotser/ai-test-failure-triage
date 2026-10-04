import io
import zipfile
from typing import NamedTuple

from pydantic import BaseModel

from failtriage.github.client import GitHubClient, GitHubError
from failtriage.history import HistoryEntry, HistoryError, parse_history

ARTIFACT_NAME = "failtriage-history"
ARTIFACT_FILE = "history.json"
MAIN_BRANCH = "main"
# A history file is a few hundred bytes per run, so anything bigger is not ours.
MAX_FILE_BYTES = 10 * 1024 * 1024


class _WorkflowRun(BaseModel):
    head_branch: str | None = None
    repository_id: int | None = None
    head_repository_id: int | None = None


class _Artifact(BaseModel):
    id: int
    expired: bool
    workflow_run: _WorkflowRun | None = None


class FetchedHistory(NamedTuple):
    entries: list[HistoryEntry]
    skipped: list[int]


def fetch_history(client: GitHubClient, repo: str, runs: int) -> FetchedHistory:
    """Merge the history artifacts of the last `runs` runs on main, newest first.

    An artifact that is gone or unreadable is skipped and reported by id. Only a failing
    listing raises, since then there is nothing to merge."""
    listing = client.get_json(
        f"/repos/{repo}/actions/artifacts", {"name": ARTIFACT_NAME, "per_page": 100}
    )
    artifacts = [_Artifact.model_validate(a) for a in listing.get("artifacts", [])]
    recent = [a for a in artifacts if _on_main(a) and not a.expired][:runs]
    entries: list[HistoryEntry] = []
    skipped: list[int] = []
    for artifact in recent:
        try:
            entries.extend(_read_artifact(client, repo, artifact.id))
        except (GitHubError, HistoryError, zipfile.BadZipFile, KeyError, ValueError):
            skipped.append(artifact.id)
    return FetchedHistory(entries, skipped)


def _on_main(artifact: _Artifact) -> bool:
    run = artifact.workflow_run
    if run is None or run.head_branch != MAIN_BRANCH:
        return False
    # A fork can have a `main` branch too, and its runs must not feed our history.
    return run.head_repository_id is not None and run.head_repository_id == run.repository_id


def _read_artifact(client: GitHubClient, repo: str, artifact_id: int) -> list[HistoryEntry]:
    data = client.get_bytes(f"/repos/{repo}/actions/artifacts/{artifact_id}/zip")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with archive.open(ARTIFACT_FILE) as file:
            # The size in the zip header is declared by whoever made the zip, so cap the read.
            data = file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("history file too large")
    return parse_history(data)
